from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "hypermemory" / "scripts"


@pytest.fixture
def finalizer(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("completion_test", SCRIPTS / "codex_turn_finalizer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_job(tmp_path, turn="turn-1", session="parent"):
    path = tmp_path / "jobs" / f"{session}-{turn}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"session_id": session, "turn_id": turn, "transcript_path": None}))
    return path


def contract(turn="turn-1"):
    return {
        "schema_version": "2.10.0",
        "turn_id": turn,
        "occurred_at": "2026-10-06T10:00:00Z",
        "active_scope": {"project_key": None, "project_name": "test", "other_anchor_keys": []},
        "request": {"intent": "Exercise completed-turn handling", "explicit_memory_instruction": None},
        "outcome": {"status": "completed", "summary": "pre-answer summary", "durable_artifacts": []},
        "timeline_summary": "Test completion",
        "durable_candidates": [],
        "activity_segments": [{"category": "coding", "weight": 100}],
        "timeline_only": [],
        "excluded": [],
        "token_listener": {"listener_path": "/listener", "job_path": "/job"},
    }


def stop_payload(turn="turn-1", session="parent", text="The answer actually delivered."):
    return {"session_id": session, "turn_id": turn, "last_assistant_message": text}


def test_writer_cannot_claim_before_answer_and_sees_delivered_text(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    assert finalizer.claim(job, 0)["status"] == "pending"
    finalizer.record_stop(tmp_path, stop_payload())
    ready = finalizer.claim(job, 0)
    assert ready["status"] == "ready"
    assert ready["completion"]["answer_excerpt"] == "The answer actually delivered."
    assert ready["contract"]["outcome"]["summary"] == "pre-answer summary"
    assert finalizer.claim(job, 0)["status"] == "pending"
    queue = Path(ready["queue_path"])
    finalizer.finish(queue, ready["claim_id"], "done")
    assert "contract" not in json.loads(queue.read_text())
    assert not list((tmp_path / "completed-turns").glob("*.json"))
    finalizer.record_stop(tmp_path, stop_payload())
    assert not list((tmp_path / "completed-turns").glob("*.json"))
    assert finalizer.enqueue(job, contract())["status"] == "done"
    assert finalizer.claim(job, 0)["status"] == "pending"


def test_waiting_writer_resumes_when_stop_records_answer(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    timer = threading.Timer(0.1, finalizer.record_stop, args=(tmp_path, stop_payload()))
    timer.start()
    try:
        assert finalizer.claim(job, 1)["status"] == "ready"
    finally:
        timer.join()


def test_missing_completion_survives_restart_and_next_writer_retries(finalizer, tmp_path):
    first = make_job(tmp_path)
    finalizer.enqueue(first, contract())
    assert finalizer.claim(first, 0)["status"] == "pending"
    later = make_job(tmp_path, "turn-2")
    finalizer.enqueue(later, contract("turn-2"))
    finalizer.record_stop(tmp_path, stop_payload())
    recovered = finalizer.claim(later, 0)
    assert recovered["contract"]["turn_id"] == "turn-1"


def test_completion_is_exactly_scoped_and_bounded(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    finalizer.record_stop(tmp_path, stop_payload(turn="wrong"))
    finalizer.record_stop(tmp_path, stop_payload(session="sibling"))
    assert finalizer.claim(job, 0)["status"] == "pending"
    payload = stop_payload(text="x" * 20_000)
    payload["reasoning"] = "private reasoning"
    payload["tool_result"] = "private output"
    finalizer.record_stop(tmp_path, payload)
    ready = finalizer.claim(job, 0)
    assert len(ready["completion"]["answer_excerpt"]) == 12_000
    assert ready["completion"]["answer_truncated"]
    assert "private reasoning" not in json.dumps(ready)
    assert "private output" not in json.dumps(ready)
    assert Path(ready["queue_path"]).stat().st_mode & 0o777 == 0o600
    for p in (tmp_path / "completed-turns").glob("*.json"):
        assert p.stat().st_mode & 0o777 == 0o600


def test_two_workers_cannot_claim_same_turn(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    finalizer.record_stop(tmp_path, stop_payload())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalizer.claim(job, 0), range(2)))
    assert sorted(r["status"] for r in results) == ["pending", "ready"]


def test_retry_only_known_no_effect_failures_and_quarantine_ambiguous_crashes(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    finalizer.record_stop(tmp_path, stop_payload())
    first = finalizer.claim(job, 0)
    queue = Path(first["queue_path"])
    finalizer.finish(queue, first["claim_id"], "retry")
    second = finalizer.claim(job, 0)
    assert second["claim_id"] != first["claim_id"]
    with pytest.raises(ValueError):
        finalizer.finish(queue, first["claim_id"], "done")
    state = json.loads(queue.read_text())
    state["claimed_at"] = time.time() - 1000
    queue.write_text(json.dumps(state))
    assert finalizer.claim(job, 0)["status"] == "pending"
    assert json.loads(queue.read_text())["status"] == "needs_review"


def test_transcript_cannot_substitute_for_stop_completion(finalizer, tmp_path):
    job = make_job(tmp_path)
    rollout = tmp_path / "rollout.jsonl"
    data = json.loads(job.read_text())
    data["transcript_path"] = str(rollout)
    job.write_text(json.dumps(data))
    events = [
        {"type": "session_meta", "payload": {"id": "parent"}},
        {"type": "response_item", "payload": {"type": "reasoning", "text": "secret"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "intermediate"}},
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "other", "last_agent_message": "wrong answer"},
        },
    ]
    rollout.write_text("\n".join(map(json.dumps, events)) + "\n")
    finalizer.enqueue(job, contract())
    assert finalizer.claim(job, 0)["status"] == "pending"
    events.append(
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "final answer"},
        }
    )
    rollout.write_text("\n".join(map(json.dumps, events)) + '\n{"partially_flushed":')
    assert finalizer.claim(job, 0)["status"] == "pending"


def test_malformed_older_queue_cannot_block_current_turn(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    (tmp_path / "finalization" / "broken.json").write_text("invalid")
    finalizer.record_stop(tmp_path, stop_payload())
    assert finalizer.claim(job, 0)["status"] == "ready"


def test_reject_mismatched_or_oversized_contract(finalizer, tmp_path):
    job = make_job(tmp_path)
    with pytest.raises(ValueError):
        finalizer.enqueue(job, contract("other"))
    with pytest.raises(ValueError):
        finalizer.enqueue(job, {**contract(), "raw": "x" * 32_000})


def test_stop_hook_does_not_record_an_unverified_answer(finalizer, tmp_path):
    job = make_job(tmp_path)
    finalizer.enqueue(job, contract())
    env = {**os.environ, "PLUGIN_ROOT": str(SCRIPTS.parent), "PLUGIN_DATA": str(tmp_path)}
    for payload in (json.dumps(stop_payload()), "invalid JSON"):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "hypermemory_hook.py"), "stop"],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "systemMessage" in output
        assert output["decision"] == "block"
    assert finalizer.claim(job, 0)["status"] == "pending"
