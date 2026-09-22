#!/usr/bin/env python3
"""Durable, local handoff from a delivered Codex answer to its background writer.

No model or MCP calls live here. Stop is the sole source of the delivered answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from codex_token_listener import _atomic_json, _read_json, _state_lock
from codex_turn_contract import validate_contract

MAX_ANSWER_CHARS = 12_000
MAX_CONTRACT_BYTES = 32_000
CLAIM_SECONDS = 900


def _key(job: dict[str, Any]) -> str:
    session, turn = job.get("session_id"), job.get("turn_id")
    if not isinstance(session, str) or not session or not isinstance(turn, str) or not turn:
        raise ValueError("completion requires an exact session_id and turn_id")
    return hashlib.sha256(f"{session}\0{turn}".encode()).hexdigest()


def _queue_path(job_path: Path, job: dict[str, Any]) -> Path:
    return job_path.parent.parent / "finalization" / f"{_key(job)}.json"


def _answer(text: str, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "answer_excerpt": text[:MAX_ANSWER_CHARS],
        "answer_truncated": len(text) > MAX_ANSWER_CHARS,
        "answer_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def record_stop(data_dir: Path, payload: dict[str, Any]) -> None:
    """Persist only the public final message; never change the turn's control flow."""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return
    key = _key(payload)
    queue = data_dir / "finalization" / f"{key}.json"
    with _state_lock(queue):
        if queue.exists() and _read_json(queue).get("status") == "done":
            return
        path = data_dir / "completed-turns" / f"{key}.json"
        _atomic_json(
            path,
            {
                "session_id": payload["session_id"],
                "turn_id": payload["turn_id"],
                **_answer(text, "Stop.last_assistant_message"),
            },
        )


def _completion(job_path: Path, job: dict[str, Any]) -> dict[str, Any] | None:
    completed = job_path.parent.parent / "completed-turns" / f"{_key(job)}.json"
    if completed.exists():
        value = _read_json(completed)
        if (value.get("session_id"), value.get("turn_id")) == (job["session_id"], job["turn_id"]):
            return value

    return None


def enqueue(job_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    job = _read_json(job_path)
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != "2.10.0"
        or contract.get("turn_id") != job["turn_id"]
    ):
        raise ValueError("expected a matching bounded 2.10.0 turn contract")
    if len(json.dumps(contract).encode()) > MAX_CONTRACT_BYTES:
        raise ValueError("turn contract exceeds the 32 KB local queue limit")
    validate_contract(contract)
    path = _queue_path(job_path, job)
    with _state_lock(path):
        if not path.exists():
            _atomic_json(
                path,
                {
                    "version": 1,
                    "session_id": job["session_id"],
                    "turn_id": job["turn_id"],
                    "job_path": str(job_path.resolve()),
                    "contract": contract,
                    "status": "pending",
                    "created_at": time.time(),
                    "attempts": 0,
                },
            )
        entry = _read_json(path)
        if entry["status"] == "pending" and not entry.get("hook_dispatched_at") and _completion(job_path, job) is None:
            # Steering can amend the same turn before delivery. Preserve the
            # latest bounded facts without replacing completed/claimed work.
            entry["contract"] = contract
            _atomic_json(path, entry)
    return {"status": entry["status"], "queue_path": str(path)}


def _claim(path: Path, session_id: str) -> dict[str, Any] | None:
    with _state_lock(path):
        entry = _read_json(path)
        if entry.get("session_id") != session_id:
            return None
        if entry["status"] == "processing" and entry.get("claimed_at", 0) + CLAIM_SECONDS < time.time():
            # The remote result may have succeeded just before the crash. A
            # local lease cannot make a non-idempotent MCP mutation exactly-once.
            entry["status"] = "needs_review"
            entry["reason"] = "writer claim expired; remote effects may already exist"
            _atomic_json(path, entry)
        if entry["status"] != "pending":
            return None
        job_path = Path(entry["job_path"])
        job = _read_json(job_path)
        completion = _completion(job_path, job)
        if completion is None:
            return None
        entry.update(status="processing", claimed_at=time.time(), claim_id=uuid.uuid4().hex)
        entry["attempts"] += 1
        _atomic_json(path, entry)
        return {
            "queue_path": str(path),
            "claim_id": entry["claim_id"],
            "contract": entry["contract"],
            "completion": completion,
        }


def claim(job_path: Path, wait_seconds: float = 30) -> dict[str, Any]:
    """Only the writer waits. Return one claim at a time so unused jobs stay retryable."""
    job = _read_json(job_path)
    current = _queue_path(job_path, job)
    deadline = time.monotonic() + min(max(wait_seconds, 0), 30)
    while True:
        paths = [current] + sorted(p for p in current.parent.glob("*.json") if p != current)
        for path in paths:
            if not path.exists():
                continue
            try:
                result = _claim(path, job["session_id"])
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                continue  # A damaged older job must not block the current turn.
            if result is not None:
                return {"status": "ready", **result}
        if time.monotonic() >= deadline:
            return {"status": "pending", "reason": "no unclaimed completed turn; retry on a later writer"}
        time.sleep(0.25)


def finish(path: Path, claim_id: str, status: str) -> dict[str, str]:
    if status not in {"done", "retry", "needs_review"}:
        raise ValueError("unsupported finalization status")
    with _state_lock(path):
        entry = _read_json(path)
        if entry.get("claim_id") != claim_id or entry["status"] != "processing":
            raise ValueError("claim is stale or already finished")
        entry["status"] = "pending" if status == "retry" else status
        if status == "done":
            # Retain a deduplication tombstone, not the message or contract.
            entry.pop("contract", None)
            job_path = Path(entry["job_path"])
            completion_path = job_path.parent.parent / "completed-turns" / f"{_key(entry)}.json"
        _atomic_json(path, entry)
        if status == "done":
            completion_path.unlink(missing_ok=True)
    return {"status": entry["status"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("enqueue", "claim"):
        sub = commands.add_parser(command)
        sub.add_argument("--job", type=Path, required=True)
        if command == "claim":
            sub.add_argument("--wait-seconds", type=float, default=30)
    sub = commands.add_parser("finish")
    sub.add_argument("--queue", type=Path, required=True)
    sub.add_argument("--claim-id", required=True)
    sub.add_argument("--status", choices=("done", "retry", "needs_review"), required=True)
    args = parser.parse_args()
    if args.command == "enqueue":
        result = enqueue(args.job, json.load(sys.stdin))
    elif args.command == "claim":
        result = claim(args.job, args.wait_seconds)
    else:
        result = finish(args.queue, args.claim_id, args.status)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "message": str(exc)}))
        sys.exit(1)
