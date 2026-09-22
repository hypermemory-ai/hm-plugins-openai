#!/usr/bin/env python3
"""Codex lifecycle bridge for mandatory HyperMemory behavior.

The hook classifies lightweight prompts, prepares token-listener jobs before
model work, and injects concise hidden developer context. Stop captures the final
answer and starts a bounded completion handler without continuing the parent.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LIGHTWEIGHT_MAX_CHARS = 80
LIGHTWEIGHT_PHRASES = frozenset({"got it", "hello", "hey", "hi", "howdy", "ok", "okay", "thank you", "thanks"})


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
    print(json.dumps({"systemMessage": message}))
    return 1


def _turn_job(payload: dict[str, Any]) -> tuple[Path, Path]:
    session_id = _safe_id(payload.get("session_id"))
    turn_id = _safe_id(payload.get("turn_id"))
    data_dir = _plugin_data()
    job_path = data_dir / "jobs" / f"turn-{session_id}-{turn_id}.json"
    job = {
        "version": 1,
        "lifecycle": "post_response",
        "created_at": datetime.now(UTC).isoformat(),
        "session_id": str(payload.get("session_id") or session_id),
        "turn_id": str(payload.get("turn_id") or turn_id),
        "transcript_path": payload.get("transcript_path"),
        "model": payload.get("model"),
        "state_file": str(data_dir / "token-state.json"),
    }
    _atomic_json(job_path, job)
    return _plugin_root() / "scripts" / "codex_token_listener.py", job_path


def user_prompt(payload: dict[str, Any]) -> int:
    listener, job_path = _turn_job(payload)
    sys.path.insert(0, str(listener.parent))
    from codex_token_listener import baseline

    # First prompt establishes the session baseline; later prompts preserve it.
    with contextlib.redirect_stdout(io.StringIO()):
        baseline(job_path)
    notice = _completed_failure_notice(_plugin_data())
    lightweight = _is_lightweight_prompt(_prompt_text(payload))
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


def stop(payload: dict[str, Any]) -> int:
    # The parent answer exists before a completion handler can be dispatched.
    sys.path.insert(0, str(_plugin_root() / "scripts"))
    from codex_completion_worker import dispatch
    from codex_turn_finalizer import record_stop

    record_stop(_plugin_data(), payload)
    session = _safe_id(payload.get("session_id"))
    turn = _safe_id(payload.get("turn_id"))
    job_path = _plugin_data() / "jobs" / f"turn-{session}-{turn}.json"
    if not job_path.is_file():
        raise ValueError("completed turn is missing its prompt-hook job")
    dispatch(job_path, _plugin_root())
    print(json.dumps({"continue": True}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=("user-prompt", "stop"))
    args = parser.parse_args()
    try:
        payload = _read_input()
        if args.event == "user-prompt":
            return user_prompt(payload)
        return stop(payload)
    except Exception as exc:
        return _hook_error(args.event, exc)


if __name__ == "__main__":
    raise SystemExit(main())
