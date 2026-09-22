from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "hypermemory" / "scripts"


def test_native_protocol_keeps_bookkeeping_model_free_and_consumes_completed_evidence(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("codex_memory_transport")
    transcript = tmp_path / "requests.jsonl"
    server = tmp_path / "fake_server.py"
    server.write_text("""import json,sys
log = open(sys.argv[1], 'a')
def send(value):
 print(json.dumps(value), flush=True)
for line in sys.stdin:
 req=json.loads(line)
 log.write(json.dumps(req)+'\\n');log.flush()
 method=req['method']
 if 'id' not in req: continue
 if method=='thread/start': result={'thread':{'id':'private-worker'},'model':'gpt-test'}
 elif method=='mcpServer/tool/call': result={'content':[], 'isError':False}
 else: result={}
 if method=='turn/start':
  # Exercise notifications arriving before the RPC response.
  send({'method':'item/completed','params':{'item':{'type':'agentMessage',
       'text':json.dumps({'status':'completed','summary':'Nothing durable.'})}}})
  send({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'total':{
       'inputTokens':10,'cachedInputTokens':3,'outputTokens':4,'reasoningOutputTokens':1}}}})
 send({'id':req['id'],'result':result})
 if method=='turn/start':
  send({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
""")
    original = subprocess.Popen
    invocations = []

    def launch(command, **kwargs):
        invocations.append(command)
        return original([sys.executable, str(server), str(transcript)], **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with module.CodexTransport(SCRIPTS.parent, timeout=5) as client:
        client.tool("hm_timeline_write", {"summary": "Completed task"})
        requests = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert "turn/start" not in [r["method"] for r in requests]
        evidence = {"completion": {"answer_excerpt": "Actual final answer"}, "contract": {"durable_candidates": []}}
        assert client.write_memories(SCRIPTS.parent, evidence)["status"] == "completed"
        assert client.writer_usage["inputTokens"] == 10
    requests = [json.loads(line) for line in transcript.read_text().splitlines()]
    start = next(r for r in requests if r["method"] == "thread/start")
    assert start["params"]["ephemeral"] and start["params"]["sandbox"] == "read-only"
    turn = next(r for r in requests if r["method"] == "turn/start")
    assert "Actual final answer" in turn["params"]["input"][0]["text"]
    assert "features.hooks=false" in invocations[0]
    assert "features.shell_tool=false" in invocations[0]
    assert "features.plugins=false" in invocations[0]
    server_config = next(value for value in invocations[0] if value.startswith("mcp_servers="))
    server = tomllib.loads(server_config)["mcp_servers"]["hypermemory"]
    enabled = server["enabled_tools"]
    assert set(server["tools"]) == set(enabled)
    assert all(policy == {"approval_mode": "approve"} for policy in server["tools"].values())
    assert "hm_get_overview" not in enabled
    assert {"hm_recall", "hm_get_nodes", "hm_store", "hm_update"}.issubset(enabled)


def test_transport_times_out_and_closes_unresponsive_child(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("codex_memory_transport")
    original = subprocess.Popen
    children = []

    def launch(_, **kwargs):
        child = original([sys.executable, "-c", "import time;time.sleep(10)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with pytest.raises(TimeoutError):
        module.CodexTransport(SCRIPTS.parent, timeout=0.1)
    assert children[0].poll() is not None
