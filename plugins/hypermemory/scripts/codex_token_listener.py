#!/usr/bin/env python3
"""Read exact Codex token counters without reading or uploading chat content.

The memory-writer sub-agent uses a two-phase inspect/ack protocol. Inspect
returns a delta and a ready-to-submit hm_tokens payload. Ack checkpoints the
cumulative counter only after the MCP submission succeeds, so failures are
retried without losing usage.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from codex_turn_contract import validate_segments

COUNTER_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
ZERO_COUNTERS = {field: 0 for field in COUNTER_FIELDS}
DEFAULT_MAX_FRESH_TOKENS_PER_TURN = 2_000_000


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@contextmanager
def _state_lock(state_file: Path, timeout: float = 5.0) -> Iterator[None]:
    """Kernel-owned locks survive long calls and release on process exit."""
    lock_path = state_file.with_suffix(state_file.suffix + ".flock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"timed out waiting for token state lock: {lock_path}") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def _state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sessions": {}}
    value = _read_json(path)
    if value.get("version") != 1 or not isinstance(value.get("sessions"), dict):
        raise RuntimeError(f"unsupported token state format: {path}")
    return value


def _reverse_lines(path: Path, chunk_size: int = 65536) -> Iterator[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(chunk_size, position)
            position -= size
            handle.seek(position)
            block = handle.read(size) + remainder
            lines = block.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if remainder:
            yield remainder.decode("utf-8", errors="replace")


def _latest_counter(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    for line in _reverse_lines(path):
        if '"token_count"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        usage = info.get("total_token_usage") or {}
        if not usage:
            continue
        return {
            "timestamp": str(event.get("timestamp") or ""),
            "model": str(info.get("model") or ""),
            "counters": {field: int(usage.get(field) or 0) for field in COUNTER_FIELDS},
        }
    return None


def _rollout_files() -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    matches: list[Path] = []
    for directory in (codex_home / "sessions", codex_home / "archived_sessions"):
        if directory.exists():
            matches.extend(directory.rglob("rollout-*.jsonl"))
    return matches


def _session_metadata(path: Path) -> tuple[str, str] | None:
    """Return physical and logical session ids without reading chat content."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(32):
                line = handle.readline()
                if not line:
                    break
                if '"session_meta"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload") or {}
                physical = str(payload.get("id") or path.stem)
                logical = str(payload.get("session_id") or physical)
                return physical, logical
    except OSError:
        return None
    return None


def _session_rollouts(job: dict[str, Any]) -> list[Path]:
    session_id = str(job.get("session_id") or "")
    explicit = job.get("transcript_path")
    candidates = _rollout_files()
    if explicit and Path(str(explicit)).is_file():
        explicit_path = Path(str(explicit)).resolve()
        if explicit_path not in candidates:
            candidates.append(explicit_path)

    matches: dict[str, Path] = {}
    for path in candidates:
        metadata = _session_metadata(path)
        if metadata and session_id in metadata:
            physical_id = metadata[0]
            resolved = path.resolve()
            existing = matches.get(physical_id)
            if existing is None or resolved.stat().st_mtime_ns > existing.stat().st_mtime_ns:
                matches[physical_id] = resolved
    if not matches:
        raise RuntimeError(f"no Codex rollout transcripts found for session {session_id!r}")
    return sorted(matches.values())


def _rollout_identity(path: Path) -> str:
    metadata = _session_metadata(path)
    return metadata[0] if metadata else path.name


def _previous_counter(previous_rollouts: dict[str, Any], path: Path) -> dict[str, int]:
    """Read stable physical-id checkpoints and migrate legacy path checkpoints."""
    identity = _rollout_identity(path)
    if identity in previous_rollouts:
        return previous_rollouts[identity]
    resolved = str(path.resolve())
    if resolved in previous_rollouts:
        return previous_rollouts[resolved]
    for key, counters in previous_rollouts.items():
        if Path(key).name == path.name:
            return counters
    return ZERO_COUNTERS


def _delta(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
    result = {field: current[field] - int(previous.get(field) or 0) for field in COUNTER_FIELDS}
    if any(value < 0 for value in result.values()):
        raise RuntimeError("Codex token counters moved backwards; create a new baseline")
    return result


def _fresh_usage(delta: dict[str, int]) -> dict[str, int]:
    """Separate cache reads from newly processed input and the displayed total."""
    input_tokens = delta["input_tokens"]
    cached_tokens = delta["cached_input_tokens"]
    if cached_tokens > input_tokens:
        raise RuntimeError("Codex cached input exceeds total input; refusing an unsafe report")
    fresh_input = input_tokens - cached_tokens
    return {
        "input_tokens": fresh_input,
        "output_tokens": delta["output_tokens"],
        "cache_tokens": cached_tokens,
        "reasoning_tokens": delta["reasoning_output_tokens"],
        "total_tokens": fresh_input + delta["output_tokens"],
    }


def _max_fresh_tokens_per_turn() -> int:
    raw = os.environ.get("HYPERMEMORY_MAX_FRESH_TOKENS_PER_TURN", "")
    try:
        value = int(raw) if raw else DEFAULT_MAX_FRESH_TOKENS_PER_TURN
    except ValueError as exc:
        raise RuntimeError("HYPERMEMORY_MAX_FRESH_TOKENS_PER_TURN must be an integer") from exc
    if value <= 0:
        raise RuntimeError("HYPERMEMORY_MAX_FRESH_TOKENS_PER_TURN must be positive")
    return value


def baseline(job_path: Path) -> int:
    job = _read_json(job_path)
    state_file = Path(str(job["state_file"]))
    session_id = job["session_id"]
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("exact session_id is required")
    with _state_lock(state_file):
        if session_id in _state(state_file)["sessions"]:
            print(json.dumps({"baselined": False, "reason": "session_already_tracked", "session_id": session_id}))
            return 0
    rollouts: dict[str, dict[str, int]] = {}
    transcript = job.get("transcript_path")
    paths = _session_rollouts(job)
    for path in paths:
        latest = _latest_counter(path)
        rollouts[_rollout_identity(path)] = (latest or {"counters": ZERO_COUNTERS})["counters"]
    with _state_lock(state_file):
        state = _state(state_file)
        existing = state["sessions"].get(session_id)
        if existing:
            print(json.dumps({"baselined": False, "reason": "session_already_tracked", "session_id": session_id}))
            return 0
        state["sessions"][session_id] = {
            "rollouts": rollouts,
            "turn_sequence": 0,
            "transcript_path": transcript,
        }
        _atomic_json(state_file, state)
    print(json.dumps({"baselined": True, "session_id": session_id}))
    return 0


def inspect(job_path: Path, wait_seconds: float, segments: list[dict]) -> int:
    validate_segments(segments)
    job = _read_json(job_path)
    state_file = Path(str(job["state_file"]))
    session_id = job["session_id"]
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("exact session_id is required")
    with _state_lock(state_file):
        state = _state(state_file)
        previous_entry = dict(state["sessions"].get(session_id) or {})
    previous_rollouts = previous_entry.get("rollouts") or {}

    deadline = time.monotonic() + max(0.0, wait_seconds)
    paths = _session_rollouts(job)

    def collect() -> tuple[dict[str, dict[str, int]], dict[str, int], str, str]:
        current_rollouts: dict[str, dict[str, int]] = {}
        combined = ZERO_COUNTERS.copy()
        discovered_model = ""
        latest_timestamp = ""
        for path in paths:
            latest = _latest_counter(path)
            current = (latest or {"counters": ZERO_COUNTERS})["counters"]
            current_rollouts[_rollout_identity(path)] = current
            physical_delta = _delta(current, _previous_counter(previous_rollouts, path))
            for field in COUNTER_FIELDS:
                combined[field] += physical_delta[field]
            if latest and latest.get("model"):
                discovered_model = str(latest["model"])
            if latest and str(latest.get("timestamp") or "") > latest_timestamp:
                latest_timestamp = str(latest["timestamp"])
        return current_rollouts, combined, discovered_model, latest_timestamp

    current_rollouts, delta, discovered_model, latest_timestamp = collect()
    while delta["total_tokens"] == 0 and time.monotonic() < deadline:
        time.sleep(0.1)
        paths = _session_rollouts(job)
        current_rollouts, delta, discovered_model, latest_timestamp = collect()

    if delta["total_tokens"] == 0:
        raise RuntimeError("no new exact token_count record")
    usage = _fresh_usage(delta)
    safety_limit = _max_fresh_tokens_per_turn()
    if usage["total_tokens"] > safety_limit:
        raise RuntimeError("fresh token safety limit exceeded; rejected exact report")

    turn_sequence = int(previous_entry.get("turn_sequence") or 0) + 1
    model = job.get("model")
    if model is None:
        model = discovered_model
    if not isinstance(model, str) or not model or model == "unknown":
        raise ValueError("exact model identity is unavailable")
    report = {
        "ai_tool": "codex",
        "provider": "openai",
        "model": model,
        "session_id": session_id,
        "turn_sequence": turn_sequence,
        "measurement_quality": "client_exact",
        **usage,
        "cache_accounting": "separate",
        "cost_quality": "unavailable",
        "segments": segments,
    }
    if latest_timestamp:
        report["timestamp"] = latest_timestamp
    claim = {
        "version": 1,
        "session_id": session_id,
        "turn_id": job.get("turn_id"),
        "rollouts": current_rollouts,
        "turn_sequence": turn_sequence,
    }
    claim_path = job_path.with_suffix(job_path.suffix + ".claim.json")
    _atomic_json(claim_path, claim)
    print(
        json.dumps(
            {
                "exact_available": True,
                "privacy": "Only token_count counters were parsed; no conversation content is returned or uploaded.",
                "claim_path": str(claim_path),
                "hm_tokens_payload": report,
            },
            indent=2,
        )
    )
    return 0


def ack(job_path: Path) -> int:
    job = _read_json(job_path)
    claim_path = job_path.with_suffix(job_path.suffix + ".claim.json")
    claim = _read_json(claim_path)
    if claim.get("session_id") != job.get("session_id") or claim.get("turn_id") != job.get("turn_id"):
        raise RuntimeError("token claim does not match its job")
    state_file = Path(str(job["state_file"]))
    session_id = str(claim["session_id"])
    with _state_lock(state_file):
        state = _state(state_file)
        state["sessions"][session_id] = {
            "rollouts": claim["rollouts"],
            "turn_sequence": int(claim["turn_sequence"]),
        }
        _atomic_json(state_file, state)
    claim_path.unlink(missing_ok=True)
    print(json.dumps({"acknowledged": True, "session_id": session_id}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="codex-token-listener")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("baseline", "ack"):
        command = subparsers.add_parser(name)
        command.add_argument("--job", type=Path, required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--job", type=Path, required=True)
    inspect_parser.add_argument("--wait-seconds", type=float, default=2.0)
    inspect_parser.add_argument(
        "--segments-json",
        type=str,
        required=True,
        help='JSON array of activity segments (e.g. \'[{"category":"coding","weight":80}]\')',
    )
    args = parser.parse_args()
    try:
        if args.command == "baseline":
            return baseline(args.job)
        if args.command == "inspect":
            segs = json.loads(args.segments_json)
            return inspect(args.job, args.wait_seconds, segments=segs)
        return ack(args.job)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
