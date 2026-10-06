#!/usr/bin/env python3
"""Fail-closed Codex lifecycle enforcement for mandatory HyperMemory behavior.

The prompt hook injects the complete skill and creates an exact per-turn ledger.
Tool hooks prevent substantive work before the required reads and record only
successful MCP receipts. Stop verifies those receipts and the staged handoff
before it accepts the answer or starts post-response processing.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

LIGHTWEIGHT_MAX_CHARS = 80
LIGHTWEIGHT_PHRASES = frozenset({"got it", "hello", "hey", "hi", "howdy", "ok", "okay", "thank you", "thanks"})
OVERVIEW_TOOL = "mcp__hypermemory__hm_get_overview"
RECALL_TOOL = "mcp__hypermemory__hm_recall"
READ_TOOLS = frozenset({OVERVIEW_TOOL, RECALL_TOOL})
CODE_MODE_TOOLS = frozenset({"exec", "functions.exec"})


def _prompt_text(payload):
    prompt = payload.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def _normalize_prompt(text):
    text = text.casefold().replace("’", "'")
    return " ".join(re.sub(r"[^\w\s']+", " ", text).split())


def _is_lightweight_prompt(text):
    return bool(text) and len(text) <= LIGHTWEIGHT_MAX_CHARS and _normalize_prompt(text) in LIGHTWEIGHT_PHRASES


def _read_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid hook JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError("hook input must be a JSON object")
    return value


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def _plugin_data() -> Path:
    configured = os.environ.get("PLUGIN_DATA")
    if not configured:
        raise ValueError("PLUGIN_DATA is required")
    path = Path(configured).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _skill_text() -> str:
    path = _plugin_root() / "skills" / "hypermemory" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("HyperMemory skill is empty")
    return text


def _skill_sha256() -> str:
    return hashlib.sha256(_skill_text().encode()).hexdigest()


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ValueError("an exact session or turn identifier is required")
    if any(not (char.isalnum() or char in "-_") for char in value):
        raise ValueError("unsupported characters in session or turn identifier")
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _context(event: str, text: str, notice: str | None = None) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": text,
                },
                **({"systemMessage": notice} if notice else {}),
            }
        )
    )


def _block(event: str, reason: str) -> int:
    if event == "pre-tool":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    elif event == "session-start":
        output = {"continue": False, "stopReason": reason, "systemMessage": reason}
    else:
        output = {"decision": "block", "reason": reason, "systemMessage": reason}
    print(json.dumps(output))
    return 0


def _completed_failure_notice(data_dir: Path) -> str | None:
    """Announce finished failures once; never inspect a running handler."""
    from codex_token_listener import _read_json, _state_lock

    notices = []
    for path in sorted((data_dir / "finalization").glob("*.json")):
        with _state_lock(path):
            entry = _read_json(path)
            if entry.get("status") != "needs_review" or entry.get("failure_announced"):
                continue
            details = []
            for phase, result in entry.get("phases", {}).items():
                if result.get("status") == "failed":
                    details.append(f"{phase}: {result.get('message', '')[:400]}")
                if result.get("result", {}).get("checkpoint") == "failed":
                    details.append(f"{phase}: remote write accepted, local checkpoint failed")
            if not details:
                details.append(f"recorded failure: {entry.get('failure_type', entry.get('reason', 'needs_review'))}")
            notices.append(f"turn {entry['turn_id']}: " + "; ".join(details))
            entry["failure_announced"] = True
            _atomic_json(path, entry)
        if len(notices) == 3:
            break
    return "HyperMemory persistence failed — " + " | ".join(notices) if notices else None


def _hook_error(event: str, exc: Exception) -> int:
    message = f"HyperMemory {event} failed: {type(exc).__name__}: {exc}"
    print(message, file=sys.stderr)
    return _block(event, message)


def _job_path(payload: dict[str, Any]) -> Path:
    session_id = _safe_id(payload.get("session_id"))
    turn_id = _safe_id(payload.get("turn_id"))
    return _plugin_data() / "jobs" / f"turn-{session_id}-{turn_id}.json"


def _read_job(payload: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    from codex_token_listener import _read_json

    path = _job_path(payload)
    if not path.is_file():
        raise ValueError("turn is missing its UserPromptSubmit enforcement ledger")
    job = _read_json(path)
    if (job.get("session_id"), job.get("turn_id")) != (payload.get("session_id"), payload.get("turn_id")):
        raise ValueError("turn enforcement ledger identity mismatch")
    return path, job


def _turn_job(payload: dict[str, Any]) -> tuple[Path, Path]:
    session_id = _safe_id(payload.get("session_id"))
    turn_id = _safe_id(payload.get("turn_id"))
    data_dir = _plugin_data()
    job_path = data_dir / "jobs" / f"turn-{session_id}-{turn_id}.json"
    session_path = data_dir / "sessions" / f"{session_id}.json"
    session = json.loads(session_path.read_text(encoding="utf-8")) if session_path.is_file() else {}
    if job_path.is_file():
        job = json.loads(job_path.read_text(encoding="utf-8"))
        if "enforcement" not in job:
            lightweight = _is_lightweight_prompt(_prompt_text(payload))
            job["enforcement"] = {
                "mode": "lightweight" if lightweight else "substantive",
                "skill_sha256": _skill_sha256(),
                "overview_required": not lightweight and not bool(session.get("overview_verified")),
                "overview_verified": False,
                "recall_required": not lightweight,
                "recall_verified": False,
                "tool_receipts": [],
            }
            _atomic_json(job_path, job)
        return _plugin_root() / "scripts" / "codex_token_listener.py", job_path
    lightweight = _is_lightweight_prompt(_prompt_text(payload))
    job = {
        "version": 1,
        "lifecycle": "post_response",
        "created_at": datetime.now(UTC).isoformat(),
        "session_id": str(payload.get("session_id") or session_id),
        "turn_id": str(payload.get("turn_id") or turn_id),
        "transcript_path": payload.get("transcript_path"),
        "model": payload.get("model"),
        "state_file": str(data_dir / "token-state.json"),
        "enforcement": {
            "mode": "lightweight" if lightweight else "substantive",
            "skill_sha256": _skill_sha256(),
            "overview_required": not lightweight and not bool(session.get("overview_verified")),
            "overview_verified": False,
            "recall_required": not lightweight,
            "recall_verified": False,
            "tool_receipts": [],
        },
    }
    _atomic_json(job_path, job)
    return _plugin_root() / "scripts" / "codex_token_listener.py", job_path


def session_start(_payload: dict[str, Any]) -> int:
    _context(
        "SessionStart",
        "HyperMemory mandatory protocol follows. Read and apply the entire skill.\n\n"
        + _skill_text()
        + "\n\nEnd HyperMemory mandatory protocol.",
    )
    return 0


def user_prompt(payload: dict[str, Any]) -> int:
    listener, job_path = _turn_job(payload)
    sys.path.insert(0, str(listener.parent))
    from codex_token_listener import baseline

    # First prompt establishes the session baseline; later prompts preserve it.
    with contextlib.redirect_stdout(io.StringIO()):
        baseline(job_path)
    notice = _completed_failure_notice(_plugin_data())
    _, job = _read_job(payload)
    lightweight = job["enforcement"]["mode"] == "lightweight"
    recall_instruction = (
        "mode=lightweight; skip hm_get_overview and hm_recall on the main agent."
        if lightweight
        else "mode=substantive; call hm_recall before substantive work and call "
        "hm_get_overview first if it has not run in this conversation. "
        "Complete these reads before investigating the task or composing the substantive answer. "
        "Use relevant results in that answer. Do not defer answer-context retrieval to Stop "
        "or count a background writer's later recall as satisfying this requirement."
    )
    _context(
        "UserPromptSubmit",
        "HyperMemory mandatory protocol follows. Read and apply the entire skill.\n\n"
        + _skill_text()
        + "\n\nEnd HyperMemory mandatory protocol.\n\n"
        f"HyperMemory turn: {recall_instruction}\n"
        "Apply the HyperMemory skill. Keep telemetry off the main agent. Keep graph "
        "writes off the main agent unless the user explicitly requests a memory "
        "mutation. When they do, perform it directly; for multi-node writes, create "
        "nodes before relationships and report only actual tool results. Do not "
        "duplicate completed direct mutations in the local handoff. Do not spawn a "
        "memory-writer. Stage a bounded local turn contract "
        "using codex_turn_finalizer.py enqueue --job with JSON on stdin; this "
        "does not start an agent or write remote memory. Include supported durable "
        "candidates covering useful user facts, corrections, decisions and outcomes, "
        "a concise timeline_summary, and activity_segments totalling 100. "
        "Use only categories: reasoning, memory, context, doc_processing, automation, "
        "personal, chatting, research, design, calculations, coding, planning, "
        "productivity, writing, unmatched. Do not hide HyperMemory use or failures. "
        "Then deliver your answer. The Stop hook owns "
        "all post-response processing and starts a writer only for candidate "
        "memories or an explicit memory instruction. Do not wait or poll.\n"
        f"finalizer={listener.with_name('codex_turn_finalizer.py')}\n"
        f"listener={listener}\njob={job_path}\n",
        notice=notice,
    )
    return 0


def _pending_read(job: dict[str, Any]) -> str | None:
    enforcement = job.get("enforcement")
    if not isinstance(enforcement, dict):
        raise ValueError("turn enforcement ledger is malformed")
    if enforcement.get("mode") == "lightweight":
        return None
    if enforcement.get("overview_required") and not enforcement.get("overview_verified"):
        return OVERVIEW_TOOL
    if enforcement.get("recall_required") and not enforcement.get("recall_verified"):
        return RECALL_TOOL
    return None


def pre_tool(payload: dict[str, Any]) -> int:
    _, job = _read_job(payload)
    required = _pending_read(job)
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    code = tool_input.get("code") if isinstance(tool_input, dict) else None
    code_mode_read = tool in CODE_MODE_TOOLS and isinstance(code, str) and required in code
    if required and tool != required and not code_mode_read:
        return _block(
            "pre-tool",
            f"BLOCKED by HyperMemory enforcement: call {required} successfully before {tool or 'any other tool'}.",
        )
    print("{}")
    return 0


def _successful_mcp_response(response: object) -> bool:
    return (
        isinstance(response, dict)
        and response.get("isError") is not True
        and ("structuredContent" in response or "content" in response)
    )


def post_tool(payload: dict[str, Any]) -> int:
    from codex_token_listener import _read_json, _state_lock

    tool = payload.get("tool_name")
    if tool not in READ_TOOLS:
        raise ValueError(f"unsupported HyperMemory receipt tool: {tool!r}")
    if not _successful_mcp_response(payload.get("tool_response")):
        return _block("post-tool", f"HyperMemory enforcement rejected the failed {tool} result. Retry it.")
    path, _ = _read_job(payload)
    with _state_lock(path):
        job = _read_json(path)
        enforcement = job["enforcement"]
        if tool == RECALL_TOOL and _pending_read(job) == OVERVIEW_TOOL:
            return _block("post-tool", "HyperMemory enforcement requires hm_get_overview before hm_recall.")
        response_hash = hashlib.sha256(
            json.dumps(payload["tool_response"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt = {
            "tool": tool,
            "tool_use_id": _safe_id(payload.get("tool_use_id")),
            "response_sha256": response_hash,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        if not any(item.get("tool_use_id") == receipt["tool_use_id"] for item in enforcement["tool_receipts"]):
            enforcement["tool_receipts"].append(receipt)
        if tool == OVERVIEW_TOOL:
            enforcement["overview_verified"] = True
        else:
            enforcement["recall_verified"] = True
        _atomic_json(path, job)
    if tool == OVERVIEW_TOOL:
        session_path = _plugin_data() / "sessions" / f"{_safe_id(payload.get('session_id'))}.json"
        _atomic_json(
            session_path,
            {"overview_verified": True, "verified_at": datetime.now(UTC).isoformat()},
        )
    print("{}")
    return 0


def _stop_failures(payload: dict[str, Any]) -> tuple[Path, list[str]]:
    from codex_token_listener import _read_json
    from codex_turn_contract import validate_contract
    from codex_turn_finalizer import _queue_path

    job_path, job = _read_job(payload)
    failures = []
    required = _pending_read(job)
    if required:
        failures.append(f"missing successful {required} receipt")
    queue = _queue_path(job_path, job)
    if not queue.is_file():
        failures.append("missing staged turn contract")
        return job_path, failures
    entry = _read_json(queue)
    if (entry.get("session_id"), entry.get("turn_id")) != (job["session_id"], job["turn_id"]):
        failures.append("staged turn contract identity mismatch")
        return job_path, failures
    if entry.get("status") != "pending" or not isinstance(entry.get("contract"), dict):
        failures.append("staged turn contract is not pending and complete")
        return job_path, failures
    try:
        validate_contract(entry["contract"])
    except (TypeError, ValueError) as exc:
        failures.append(f"invalid staged turn contract: {exc}")
    listener = _plugin_root() / "scripts" / "codex_token_listener.py"
    expected = {"listener_path": str(listener), "job_path": str(job_path)}
    if entry["contract"].get("token_listener") != expected:
        failures.append("turn contract token_listener does not match the hook-supplied paths")
    return job_path, failures


def stop(payload: dict[str, Any]) -> int:
    # Refuse completion until hook-observed reads and a strict handoff exist.
    sys.path.insert(0, str(_plugin_root() / "scripts"))
    from codex_completion_worker import dispatch
    from codex_turn_finalizer import record_stop

    job_path, failures = _stop_failures(payload)
    if failures:
        return _block(
            "stop",
            "BLOCKED by HyperMemory Stop verification: "
            + "; ".join(failures)
            + ". Perform the missing work; claims of compliance do not count.",
        )
    record_stop(_plugin_data(), payload)
    dispatch(job_path, _plugin_root())
    print(json.dumps({"continue": True}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=("session-start", "user-prompt", "pre-tool", "post-tool", "stop"))
    args = parser.parse_args()
    try:
        payload = _read_input()
        handlers = {
            "session-start": session_start,
            "user-prompt": user_prompt,
            "pre-tool": pre_tool,
            "post-tool": post_tool,
            "stop": stop,
        }
        return handlers[args.event](payload)
    except Exception as exc:
        return _hook_error(args.event, exc)


if __name__ == "__main__":
    raise SystemExit(main())
