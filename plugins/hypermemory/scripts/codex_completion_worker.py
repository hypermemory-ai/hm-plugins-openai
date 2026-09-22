#!/usr/bin/env python3
"""Persist one completed turn, with independent and auditable write results."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path

from codex_memory_transport import CodexTransport
from codex_token_listener import _atomic_json, _read_json, _state_lock, ack, inspect
from codex_turn_contract import validate_segments
from codex_turn_finalizer import _claim, _completion, _queue_path, finish


def prepare_completed_job(job_path: Path) -> Path | None:
    job = _read_json(job_path)
    if job.get("lifecycle") != "post_response":
        raise ValueError("job is not owned by the post-response lifecycle")
    if _completion(job_path, job) is None:
        return None
    path = _queue_path(job_path, job)
    if not path.exists():
        raise ValueError("completed turn has no staged handoff; no substitute contract was created")
    return path


def dispatch(job_path: Path, plugin_root: Path) -> bool:
    path = prepare_completed_job(job_path)
    if path is None:
        return False
    with _state_lock(path):
        entry = _read_json(path)
        if entry["status"] != "pending" or entry.get("hook_dispatched_at"):
            return False
        entry["hook_dispatched_at"] = time.time()
        _atomic_json(path, entry)
    try:
        subprocess.Popen(
            [sys.executable, str(plugin_root / "scripts" / "codex_completion_worker.py"), "--job", str(job_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        with _state_lock(path):
            entry = _read_json(path)
            entry.pop("hook_dispatched_at", None)
            _atomic_json(path, entry)
        raise
    return True


def needs_memory_writer(contract: dict) -> bool:
    return bool(contract["durable_candidates"]) or bool(
        (contract.get("request") or {}).get("explicit_memory_instruction")
    )


def _capture(function, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        function(*args)
    return json.loads(output.getvalue())


def _record(path, phase, result):
    with _state_lock(path):
        entry = _read_json(path)
        entry.setdefault("phases", {})[phase] = {**result, "updated_at": time.time()}
        _atomic_json(path, entry)


def _phase(path, name, operation):
    _record(path, name, {"status": "running"})
    try:
        result = operation()
        _record(path, name, {"status": "succeeded", "result": result})
        return True
    except Exception as exc:
        _record(path, name, {"status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:2000]})
        return False


def _receipt(result):
    if not isinstance(result, dict) or result.get("isError"):
        raise RuntimeError("MCP operation did not return a successful response")
    receipt = result.get("structuredContent")
    if not isinstance(receipt, dict) or not receipt:
        raise RuntimeError("MCP operation returned no structured receipt; remote outcome is unconfirmed")
    return receipt


def _token_receipt(result):
    receipt = _receipt(result)
    # The Rust target returns event_id + duplicate after the storage transaction.
    # It does not return the legacy Python service's 'accepted' field.
    if not isinstance(receipt.get("event_id"), str) or not receipt["event_id"]:
        raise RuntimeError("hm_tokens returned no persisted event identity")
    if not isinstance(receipt.get("duplicate"), bool):
        raise RuntimeError("hm_tokens returned no duplicate disposition")
    return receipt


def report_tokens(client, job_path, contract, path):
    job = _read_json(job_path)
    lock = _queue_path(job_path, {**job, "turn_id": "bookkeeping"}).with_suffix(".usage")
    with _state_lock(lock, timeout=30):
        segments = validate_segments(contract.get("activity_segments"))
        result = _capture(inspect, job_path, 2, segments)
        if not result.get("exact_available"):
            raise RuntimeError("exact token counters unavailable; no estimate was submitted")
        payload = result["hm_tokens_payload"]
        receipt = _token_receipt(client.tool("hm_tokens", payload))
        if receipt["duplicate"]:
            # A previous submission may have used this sequence with an older
            # counter snapshot. The server does not echo that payload, so a
            # duplicate cannot prove acceptance of the newly inspected delta.
            raise RuntimeError(
                "hm_tokens sequence already exists; reconcile its original counter claim before acknowledgement"
            )
        # Keep the accepted remote result even if the local checkpoint fails.
        _record(
            path,
            "tokens",
            {
                "status": "accepted",
                "receipt": receipt,
                "session_id": payload["session_id"],
                "turn_sequence": payload["turn_sequence"],
            },
        )
        try:
            _capture(ack, job_path)
        except Exception as exc:
            return {"receipt": receipt, "checkpoint": "failed", "message": str(exc)[:2000]}
        return {"receipt": receipt, "checkpoint": "acknowledged"}


def process(job_path: Path, transport_factory=CodexTransport) -> str:
    job = _read_json(job_path)
    path = _queue_path(job_path, job)
    if not path.exists():
        raise ValueError("turn has no staged handoff")
    ready = _claim(path, job["session_id"])
    if ready is None:
        return "pending"
    root = Path(__file__).resolve().parents[1]
    contract = ready["contract"]
    writer_usage = {}

    def memory():
        if not needs_memory_writer(contract):
            return {"status": "no_candidates", "summary": "The staged turn contains no memory candidates."}
        with transport_factory(root) as client:
            try:
                return client.write_memories(
                    root,
                    {
                        "mode": "completed_hook",
                        "contract": contract,
                        "completion": ready["completion"],
                    },
                )
            finally:
                if client.writer_usage is not None:
                    writer_usage.update(usage=client.writer_usage, model=client.model, session_id=client.thread_id)

    memory_ok = _phase(path, "memory", memory)

    def timeline():
        summary = contract.get("timeline_summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
            raise ValueError("timeline_summary is missing or invalid")
        with transport_factory(root) as client:
            return _receipt(
                client.tool(
                    "hm_timeline_write",
                    {
                        "summary": summary,
                        "meta": {
                            "turn_id": contract["turn_id"],
                            "session_id": job["session_id"],
                            "memory": _read_json(path)["phases"]["memory"],
                        },
                        "response_format": "structured",
                    },
                )
            )

    timeline_ok = _phase(path, "timeline", timeline)

    def tokens():
        with transport_factory(root) as client:
            return report_tokens(client, job_path, contract, path)

    tokens_ok = _phase(path, "tokens", tokens)
    if tokens_ok:
        tokens_ok = _read_json(path)["phases"]["tokens"]["result"]["checkpoint"] == "acknowledged"

    def writer_tokens():
        if not writer_usage:
            raise RuntimeError("writer token counters unavailable; no estimate was submitted")
        usage = writer_usage["usage"]
        if not all(
            isinstance(usage.get(k), int) and usage[k] >= 0
            for k in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens")
        ):
            raise ValueError("writer token counters are incomplete")
        fresh = usage["inputTokens"] - usage["cachedInputTokens"]
        if fresh < 0:
            raise ValueError("writer cached input exceeds total input")
        payload = {
            "ai_tool": "codex",
            "provider": "openai",
            "model": writer_usage["model"],
            "session_id": writer_usage["session_id"],
            "turn_sequence": 1,
            "measurement_quality": "client_exact",
            "cache_accounting": "separate",
            "cost_quality": "unavailable",
            "input_tokens": fresh,
            "output_tokens": usage["outputTokens"],
            "cache_tokens": usage["cachedInputTokens"],
            "reasoning_tokens": usage["reasoningOutputTokens"],
            "total_tokens": fresh + usage["outputTokens"],
            "segments": [{"category": "memory", "weight": 100}],
        }
        with transport_factory(root) as client:
            return _token_receipt(client.tool("hm_tokens", payload))

    writer_tokens_ok = True
    if needs_memory_writer(contract):
        writer_tokens_ok = _phase(path, "writer_tokens", writer_tokens)
    status = "done" if all((memory_ok, timeline_ok, tokens_ok, writer_tokens_ok)) else "needs_review"
    finish(path, ready["claim_id"], status)
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    try:
        status = process(args.job)
    except Exception as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}), file=sys.stderr)
        status = "failed"
    raise SystemExit(0 if status in {"done", "pending"} else 1)
