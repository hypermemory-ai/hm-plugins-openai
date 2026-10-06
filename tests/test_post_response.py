from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "hypermemory" / "scripts"


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("codex_completion_worker"), importlib.import_module("codex_turn_finalizer")


def candidate(description="A durable decision"):
    return {
        "action_hint": "store",
        "key_hint": None,
        "node_type": "decision",
        "description_draft": description,
        "facts": {},
        "relationship_changes": {"add": [], "remove_or_replace": []},
        "durability_reason": "Improves future project recall",
        "source_basis": "completed_work",
        "confidence": "high",
    }


def stage(tmp_path, finalizer, candidates=None):
    job = tmp_path / "jobs" / "turn-parent-turn-1.json"
    job.parent.mkdir(exist_ok=True)
    job.write_text(
        json.dumps(
            {
                "session_id": "parent",
                "turn_id": "turn-1",
                "lifecycle": "post_response",
                "transcript_path": None,
                "state_file": str(tmp_path / "state.json"),
            }
        )
    )
    finalizer.enqueue(
        job,
        {
            "schema_version": "2.10.0",
            "turn_id": "turn-1",
            "occurred_at": "2026-10-06T10:00:00Z",
            "active_scope": {"project_key": None, "project_name": "test", "other_anchor_keys": []},
            "request": {"intent": "Exercise post-response handling", "explicit_memory_instruction": None},
            "outcome": {"status": "completed", "summary": "Completed a test task.", "durable_artifacts": []},
            "durable_candidates": candidates or [],
            "timeline_summary": "Completed a test task.",
            "activity_segments": [{"category": "coding", "weight": 100}],
            "timeline_only": [],
            "excluded": [],
            "token_listener": {"listener_path": "/listener", "job_path": str(job)},
        },
    )
    return job


def complete(tmp_path, finalizer, text="The completed answer."):
    finalizer.record_stop(tmp_path, {"session_id": "parent", "turn_id": "turn-1", "last_assistant_message": text})


class FakeTransport:
    calls = []
    fail_on = None
    writer_usage = None
    model = "gpt-test"
    thread_id = "writer-test"

    def __init__(self, _):
        self.calls.append(("connect", None))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail_on:
            raise RuntimeError("ambiguous remote failure")
        return {"structuredContent": {"event_id": "evt-test-receipt", "duplicate": False}, "isError": False}

    def write_memories(self, root, evidence):
        self.calls.append(("writer", evidence))
        self.writer_usage = {"inputTokens": 12, "cachedInputTokens": 2, "outputTokens": 3, "reasoningOutputTokens": 1}
        if self.fail_on == "writer":
            raise RuntimeError("node type is not an active ontology class")
        return {"status": "completed", "summary": "Verified durable work."}


@pytest.fixture
def transport(monkeypatch, runtime):
    FakeTransport.calls = []
    FakeTransport.fail_on = None
    worker, _ = runtime
    payload = {"input_tokens": 5, "output_tokens": 4, "total_tokens": 9, "session_id": "parent", "turn_sequence": 1}
    monkeypatch.setattr(
        worker, "inspect", lambda *a: print(json.dumps({"exact_available": True, "hm_tokens_payload": payload}))
    )
    monkeypatch.setattr(worker, "ack", lambda *a: print("{}"))
    return FakeTransport


def test_no_process_or_writer_before_final_answer(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer, [candidate()])
    spawned = []
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    assert not worker.dispatch(job, SCRIPTS.parent)
    assert worker.process(job, transport) == "pending"
    assert not spawned and not transport.calls
    complete(tmp_path, finalizer)
    assert worker.dispatch(job, SCRIPTS.parent)
    assert len(spawned) == 1


def test_completed_turn_without_candidates_only_logs(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    assert worker.process(job, transport) == "done"
    assert [call[0] for call in transport.calls] == ["connect", "hm_timeline_write", "connect", "hm_tokens"]
    entry = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    assert entry["phases"]["tokens"]["result"]["checkpoint"] == "acknowledged"
    assert "contract" not in entry
    assert not list((tmp_path / "completed-turns").glob("*.json"))


def test_candidate_writer_receives_actual_answer_and_logs_separately(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer, [candidate("Durable decision")])
    complete(tmp_path, finalizer, "Final result differs from the draft.")
    assert worker.process(job, transport) == "done"
    assert [call[0] for call in transport.calls] == [
        "connect",
        "writer",
        "connect",
        "hm_timeline_write",
        "connect",
        "hm_tokens",
        "connect",
        "hm_tokens",
    ]
    assert transport.calls[1][1]["completion"]["answer_excerpt"] == "Final result differs from the draft."


def test_repeated_concurrent_stops_only_dispatch_once(runtime, tmp_path, monkeypatch):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    calls = []
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: calls.append(a))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: worker.dispatch(job, SCRIPTS.parent), range(4)))
    assert results.count(True) == 1 and len(calls) == 1


def test_missing_handoff_never_invents_memory(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    next((tmp_path / "finalization").glob("*.json")).unlink()
    complete(tmp_path, finalizer)
    with pytest.raises(ValueError, match="no staged handoff"):
        worker.prepare_completed_job(job)
    assert not list((tmp_path / "finalization").glob("*.json"))
    assert "writer" not in [call[0] for call in transport.calls]


def test_failure_never_replays_partial_remote_writes(runtime, tmp_path, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    transport.fail_on = "hm_timeline_write"
    assert worker.process(job, transport) == "needs_review"
    before = list(transport.calls)
    assert worker.process(job, transport) == "pending"
    assert not worker.dispatch(job, SCRIPTS.parent)
    assert transport.calls == before


def test_usage_ack_follows_successful_submission(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    payload = {"total_tokens": 9, "session_id": "parent", "turn_sequence": 1}
    monkeypatch.setattr(
        worker, "inspect", lambda *a: print(json.dumps({"exact_available": True, "hm_tokens_payload": payload}))
    )

    def ack(path):
        assert transport.calls[-1] == ("hm_tokens", payload)
        transport.calls.append(("ack", str(path)))
        print("{}")

    monkeypatch.setattr(worker, "ack", ack)
    assert worker.process(job, transport) == "done"
    assert [call[0] for call in transport.calls] == ["connect", "hm_timeline_write", "connect", "hm_tokens", "ack"]


def test_explicit_forget_starts_writer_without_store_candidates(runtime):
    worker, _ = runtime
    assert worker.needs_memory_writer(
        {"durable_candidates": [], "request": {"explicit_memory_instruction": "Forget this preference"}}
    )
    assert not worker.needs_memory_writer({"durable_candidates": []})


def test_steering_updates_pending_contract_only(runtime, tmp_path):
    _, finalizer = runtime
    job = stage(tmp_path, finalizer)
    queue = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    changed = queue["contract"]
    changed["durable_candidates"] = [candidate("Corrected durable fact")]
    changed["timeline_summary"] = "Corrected outcome"
    changed["activity_segments"] = [{"category": "writing", "weight": 100}]
    finalizer.enqueue(job, changed)
    complete(tmp_path, finalizer)
    finalizer.enqueue(job, {**changed, "durable_candidates": []})
    assert finalizer.claim(job, 0)["contract"] == changed


def test_legacy_job_is_not_dispatched_twice_during_upgrade(runtime, tmp_path):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    data = json.loads(job.read_text())
    data.pop("lifecycle")
    job.write_text(json.dumps(data))
    complete(tmp_path, finalizer)
    with pytest.raises(ValueError, match="not owned"):
        worker.dispatch(job, SCRIPTS.parent)


def test_stop_failure_does_not_continue_parent(tmp_path):
    env = {**os.environ, "PLUGIN_ROOT": str(SCRIPTS.parent), "PLUGIN_DATA": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "hypermemory_hook.py"), "stop"],
        input="invalid",
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output
    assert output["decision"] == "block"


@pytest.mark.parametrize("durable", [False, True])
def test_actual_hook_to_background_process_end_to_end(runtime, tmp_path, durable):
    _, finalizer = runtime
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "rpc.jsonl"
    fake = binary / "codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json,os,sys
log=open(os.environ['TEST_RPC_LOG'],'a')
for line in sys.stdin:
 r=json.loads(line);log.write(json.dumps(r)+'\\n');log.flush()
 if 'id' not in r:continue
 m=r['method']
 if m=='thread/start':result={'thread':{'id':'isolated'},'model':'gpt-test'}
 else:result={'structuredContent':{'event_id':'evt-test','duplicate':False}}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
 if m=='turn/start':
  print(json.dumps({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'total':{'inputTokens':12,'cachedInputTokens':2,'outputTokens':3,'reasoningOutputTokens':1}}}}),flush=True)
  print(json.dumps({'method':'item/completed','params':{'item':{'type':'agentMessage',
    'text':json.dumps({'status':'completed','summary':'Candidate checked.'})}}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'turn':{'status':'completed'}}}),flush=True)
"""
    )
    fake.chmod(0o700)
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "TEST_RPC_LOG": str(log),
        "PLUGIN_ROOT": str(SCRIPTS.parent),
        "PLUGIN_DATA": str(tmp_path),
    }
    rollout = tmp_path / "rollout-parent.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "parent", "session_id": "parent"}}) + "\n")
    env["CODEX_HOME"] = str(tmp_path / "codex")
    payload = {
        "session_id": "parent",
        "turn_id": "turn-1",
        "prompt": "Complete this work",
        "transcript_path": str(rollout),
        "model": "gpt-test",
    }
    submitted = subprocess.run(
        [sys.executable, str(SCRIPTS / "hypermemory_hook.py"), "user-prompt"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "Do not spawn a memory-writer" in submitted.stdout
    job = tmp_path / "jobs" / "turn-parent-turn-1.json"
    for tool_name, tool_use_id, response in (
        ("mcp__hypermemory__hm_get_overview", "overview-1", {"nodes": 1}),
        ("mcp__hypermemory__hm_recall", "recall-1", {"count": 0}),
    ):
        subprocess.run(
            [sys.executable, str(SCRIPTS / "hypermemory_hook.py"), "post-tool"],
            input=json.dumps(
                {
                    **payload,
                    "tool_name": tool_name,
                    "tool_use_id": tool_use_id,
                    "tool_response": {"structuredContent": response, "isError": False},
                }
            ),
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
    finalizer.enqueue(
        job,
        {
            "schema_version": "2.10.0",
            "turn_id": "turn-1",
            "occurred_at": "2026-10-06T10:00:00Z",
            "active_scope": {"project_key": None, "project_name": "test", "other_anchor_keys": []},
            "request": {"intent": "Exercise the full hook lifecycle", "explicit_memory_instruction": None},
            "outcome": {"status": "completed", "summary": "Completed test task", "durable_artifacts": []},
            "timeline_summary": "Completed test task",
            "durable_candidates": [candidate("Known durable fact")] if durable else [],
            "activity_segments": [{"category": "coding", "weight": 100}],
            "timeline_only": [],
            "excluded": [],
            "token_listener": {
                "listener_path": str(SCRIPTS / "codex_token_listener.py"),
                "job_path": str(job),
            },
        },
    )
    assert not log.exists()
    with rollout.open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "model": "gpt-test",
                            "total_token_usage": {
                                "input_tokens": 10,
                                "cached_input_tokens": 5,
                                "output_tokens": 4,
                                "reasoning_output_tokens": 1,
                                "total_tokens": 14,
                            },
                        },
                    },
                }
            )
            + "\n"
        )
    stopped = subprocess.run(
        [sys.executable, str(SCRIPTS / "hypermemory_hook.py"), "stop"],
        input=json.dumps({**payload, "last_assistant_message": "Verified final answer"}),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert json.loads(stopped.stdout) == {"continue": True}
    queue_path = next((tmp_path / "finalization").glob("*.json"))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        entry = json.loads(queue_path.read_text())
        if entry["status"] in {"done", "needs_review"}:
            break
        time.sleep(0.05)
    assert entry["status"] == "done", entry
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    turns = [r for r in requests if r["method"] == "turn/start"]
    assert len(turns) == int(durable)
    if durable:
        assert "Verified final answer" in turns[0]["params"]["input"][0]["text"]
    assert [r["params"]["tool"] for r in requests if r["method"] == "mcpServer/tool/call"] == [
        "hm_timeline_write",
        "hm_tokens",
    ] + (["hm_tokens"] if durable else [])
    assert not list((tmp_path / "completed-turns").glob("*.json"))


@pytest.mark.parametrize("failed_phase", ["writer", "hm_timeline_write", "hm_tokens"])
def test_each_write_failure_preserves_the_other_operations(runtime, tmp_path, transport, failed_phase):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer, [candidate("Remember the operating constraint")])
    complete(tmp_path, finalizer)
    transport.fail_on = failed_phase
    assert worker.process(job, transport) == "needs_review"
    names = [call[0] for call in transport.calls]
    assert "writer" in names and "hm_timeline_write" in names and "hm_tokens" in names
    entry = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    phase = {"writer": "memory", "hm_timeline_write": "timeline", "hm_tokens": "tokens"}[failed_phase]
    assert entry["phases"][phase]["status"] == "failed"
    assert entry["phases"][phase]["message"]
    assert "contract" in entry


def test_unavailable_exact_tokens_fail_without_an_estimate(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    monkeypatch.setattr(worker, "inspect", lambda *a: print('{"exact_available":false}'))
    assert worker.process(job, transport) == "needs_review"
    assert "hm_tokens" not in [call[0] for call in transport.calls]
    entry = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    assert entry["phases"]["timeline"]["status"] == "succeeded"
    assert "no estimate" in entry["phases"]["tokens"]["message"]


@pytest.mark.parametrize(
    "segments",
    [
        None,
        [],
        [{"category": "investigation", "weight": 100}],
        [{"category": "other", "weight": 100}],
        [{"category": "coding", "weight": 99}],
        [{"category": "coding", "weight": 50}, {"category": "coding", "weight": 50}],
    ],
)
def test_invalid_activity_categories_never_reach_the_queue(runtime, tmp_path, segments):
    _, finalizer = runtime
    job = stage(tmp_path, finalizer)
    queue = next((tmp_path / "finalization").glob("*.json"))
    before = queue.read_bytes()
    contract = json.loads(before)["contract"]
    with pytest.raises(ValueError):
        finalizer.enqueue(job, {**contract, "activity_segments": segments})
    assert queue.read_bytes() == before


def test_remote_acceptance_is_retained_when_local_ack_fails(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)

    def fail_ack(*_):
        raise OSError("disk full")

    monkeypatch.setattr(worker, "ack", fail_ack)
    assert worker.process(job, transport) == "needs_review"
    entry = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    assert entry["phases"]["tokens"]["result"]["receipt"]["event_id"] == "evt-test-receipt"
    assert entry["phases"]["tokens"]["result"]["checkpoint"] == "failed"
    calls = list(transport.calls)
    assert worker.process(job, transport) == "pending"
    assert calls == transport.calls


def test_duplicate_token_sequence_never_acknowledges_a_new_counter_snapshot(runtime, tmp_path, monkeypatch, transport):
    worker, finalizer = runtime
    job = stage(tmp_path, finalizer)
    complete(tmp_path, finalizer)
    acknowledgements = []
    monkeypatch.setattr(worker, "ack", lambda *args: acknowledgements.append(args))
    monkeypatch.setattr(transport, "tool", lambda *args: {
        "structuredContent": {"event_id": "evt-earlier-snapshot", "duplicate": True}, "isError": False,
    })
    assert worker.process(job, transport) == "needs_review"
    assert not acknowledgements
    entry = json.loads(next((tmp_path / "finalization").glob("*.json")).read_text())
    assert "reconcile its original counter claim" in entry["phases"]["tokens"]["message"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_token_receipt_uses_the_rust_storage_contract(runtime, duplicate):
    worker, _ = runtime
    receipt = {"event_id": "evt-persisted", "duplicate": duplicate, "provider_verified": False}
    assert worker._token_receipt({"structuredContent": receipt}) == receipt


@pytest.mark.parametrize("receipt", [{"accepted": True}, {"event_id": "evt"},
    {"event_id": "", "duplicate": False}, {"event_id": "evt", "duplicate": "false"}])
def test_token_ack_requires_a_persisted_event_identity(runtime, receipt):
    worker, _ = runtime
    with pytest.raises(RuntimeError):
        worker._token_receipt({"structuredContent": receipt})
