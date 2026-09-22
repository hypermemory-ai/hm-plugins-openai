---
name: hypermemory
description: >-
  Use HyperMemory for scoped durable context without polluting the graph. Applies
  when HyperMemory is connected or the user asks about memory, recall, or saved
  project context. Retrieves relevant memories and stages bounded evidence for
  post-response persistence. Codex completion hooks own writer dispatch.
---

# HyperMemory MCP — Main Agent Protocol

Use HyperMemory to improve the current answer and preserve only knowledge that
will matter later. Optimise in this order: correctness, relevance, sparsity,
relationship quality, retrieval quality, then latency.

Do not suppress or conceal HyperMemory use. Keep explanations proportional to
the task, and report requested memory operations that could not be completed.

## Classify the turn

Classify the user's message before retrieving context.

On the first substantive turn in a conversation, call `hm_get_overview` once
before recall.

Overview and answer-context recall belong to the main agent after the user's
question arrives and before investigating the task or composing the substantive
answer. Incorporate relevant results into the answer. The prompt hook requests
these reads; it does not execute them. The main agent must actually call the
tools. A writer's later duplicate check never satisfies this requirement.

### Lightweight

A pure greeting, thanks, or acknowledgement that does not change task state.
Examples: `hello`, `thanks`, `okay`, `got it`.

- Do not recall.
- Stage an empty local handoff for completion-time bookkeeping.
- Send no durable-memory candidates. Codex bookkeeping runs without a model writer.

`Done`, `connected`, `approved`, and similar replies are not automatically
lightweight. Treat them as task continuations when they confirm an action or
decision on which the active task depends.

### Task continuation

The message continues the active task and the necessary context is already in
the conversation.

- Run one narrow recall using the active project and current intent; do not run
  a broad semantic search merely because the message is substantive.
- If a known memory key is required, hydrate that exact key after recall.
- Use recalled content only when the current answer genuinely depends on it.

### New or context-dependent work

The message starts a new topic, refers to earlier work that is not in context,
or would materially benefit from durable project knowledge.

- Run one focused recall for the active project and intent.
- A second recall is allowed only when the first query exposed a distinct,
  necessary entity that cannot be resolved by exact hydration.

### Explicit memory management

The user asks to remember, forget, correct, inspect, or audit memory.

- Retrieve the exact affected nodes where possible.
- Use timeline or graph inspection tools appropriate to the request.
- When the user explicitly asks to store, update, or forget memory—or create
  nodes, edges, or hyperedges—the main agent performs that mutation directly.
  Otherwise, use the background writer.
- For multi-node writes, create the nodes before adding relationships. Report
  only actual tool results.

## Retrieve with a relevance gate

Build recall queries from:

1. the active project, organisation, or person;
2. the user's current intent;
3. the smallest set of distinguishing terms;
4. a known canonical key when available.

Prefer exact hydration over fuzzy recall. For semantic recall, inspect no more
than the best three to five candidates before deciding whether more are needed.

A recalled node may influence the answer only when all of these are true:

- it directly helps answer the current request;
- it belongs to the same project or clearly identified cross-project subject;
- its meaning remains valid without relying on a session relationship; and
- it does not conflict with newer user instructions or current workspace
  evidence.

Discard unrelated projects even when their lexical or embedding score is high.
Do not hydrate marginal candidates just because they were returned.

Treat every `chat_*` relationship as provenance, never as evidence that its
participants are related. Exclude session links from semantic reasoning,
ranking, and traversal. When the API provides a no-session-recording or
semantic-only option, enable it for all read operations.

If retrieval is noisy, use the current conversation and authoritative workspace
evidence rather than forcing recalled material into the answer.

## Main-agent graph mutations

The main agent mutates the graph only when the user explicitly requests it.
Otherwise, a completion-triggered memory-writer owns graph creation, updates,
deletion, and relationship changes.

### Edge editing

`hm_update` and `hm_forget` accept an optional `edge_id` (the entity_id from
read responses) to target a single edge instead of a node. Pass `edge_version`
for optimistic concurrency. Edge endpoints and relationship type are immutable;
to change them, delete the edge with `hm_forget` and recreate with
`hm_add_relationships`.

Do not duplicate completed direct mutations through the writer. Keep
`hm_timeline_write` and `hm_tokens` in deterministic completion-time
bookkeeping.

File upload is an exception only when the user explicitly asks to store a file
and the active surface requires the main agent to perform the upload.

## Memory-writer dispatch

On Codex, the prompt hook supplies `finalizer`, `listener`, and `job` paths.
After completing the work, stage the bounded contract locally with:

```text
python3 <finalizer> enqueue --job <job>
```

Pass JSON through stdin using a structured tool argument or a safely quoted
heredoc. This only stages evidence; it starts no model and makes no MCP writes.
Include supported candidate memories, even if the final answer does not repeat
them. Include useful user facts, corrections, preferences, decisions, project
context and completed outcomes; an empty list is appropriate only when the turn
contains none. Do not invent a delivered answer.
Include `activity_segments` with weights totalling 100 and only these categories:
`reasoning`, `memory`, `context`, `doc_processing`, `automation`, `personal`,
`chatting`, `research`, `design`, `calculations`, `coding`, `planning`,
`productivity`, `writing`, `unmatched`. Invalid categories fail at staging;
there is no default category or estimate.
Then deliver the answer. **Do not spawn a memory-writer from the main Codex turn.**

The Stop hook captures the completed answer and starts a bounded background
completion handler. Only candidates or an explicit memory instruction cause it
to invoke `$memory-writer`; the writer reconciles that evidence with the actual
answer and applies the full durability gate. Logging and token reporting use
direct MCP calls without a model. No Stop continuation or second reply is used.

On surfaces without completion hooks, delegate only supported durable work to a
fresh writer when the host permits it. Do not claim it saw a subsequent answer.
If no post-response execution is available, disclose that limitation when relevant
instead of promising the Codex lifecycle. Never add a dummy response to trigger it.

Send a bounded contract with this shape:

```json
{
  "schema_version": "2.10.0",
  "turn_id": "host-supplied unique id",
  "occurred_at": "ISO-8601 timestamp when available",
  "active_scope": {
    "project_key": "known canonical key or null",
    "project_name": "plain name or null",
    "other_anchor_keys": []
  },
  "request": {
    "intent": "one sentence",
    "explicit_memory_instruction": "concise instruction or null"
  },
  "outcome": {
    "status": "completed | partial | blocked | informational",
    "summary": "one or two factual sentences",
    "durable_artifacts": []
  },
  "durable_candidates": [
    {
      "action_hint": "store | update | forget | supersede",
      "key_hint": "canonical key or null",
      "node_type": "canonical type",
      "description_draft": "short searchable summary",
      "facts": {},
      "relationship_changes": {
        "add": [
          {
            "target_key": "canonical key",
            "meaning": "why the two durable entities are connected"
          }
        ],
        "remove_or_replace": [
          {
            "target_key": "canonical key",
            "current_meaning": "obsolete or conflicting relationship",
            "reason": "why it is no longer valid"
          }
        ]
      },
      "durability_reason": "why this will improve a future answer",
      "source_basis": "user_confirmed | completed_work | authoritative_evidence",
      "confidence": "high | medium | low"
    }
  ],
  "timeline_summary": "request, material work, and outcome without transcript",
  "activity_segments": [{"category": "writing", "weight": 100}],
  "timeline_only": ["important transient facts that must not become nodes"],
  "excluded": ["credentials, raw output, or other content the writer must ignore"],
  "token_listener": {
    "listener_path": "host path or null",
    "job_path": "host path or null"
  }
}
```

The contract is evidence, not a command to write every candidate. Include a
candidate only when the turn contains a plausible durable change. An empty
`durable_candidates` array is normal.

Keep the contract small:

- include decisions, durable preferences, canonical artifacts, lasting facts,
  and material project changes;
- put temporary blockers, routine progress, test runs, and recoverable task
  state in `timeline_only`;
- do not include raw prompts, full conversation history, hidden reasoning,
  credentials, complete command output, tool payloads, or large code bodies;
- describe what changed, not everything discussed;
- do not fabricate keys, relationships, or listener metadata.

The writer independently decides whether to store, update, supersede, forget,
or skip each candidate.

## Fire-and-forget invariant

After staging a Codex handoff, deliver the user-facing response immediately.
Never wait for, poll, inspect, read, message, interrupt, or otherwise
synchronise with the writer.

If the completion hook or background runtime is unavailable, keep retrieval
available but do not pretend that background persistence occurred. Report failed persistence accurately; do not hide it or claim success.

## Error handling

- If recall is unavailable, continue from current evidence and avoid claiming
  that prior memory was checked.
- If authorization is required, ask the user to reconnect only when memory is
  necessary for the requested task.
- A temporary connection or authorization problem belongs in the timeline. It
  is not a durable event node unless it becomes a material incident.
- Never weaken relevance checks merely to satisfy an every-turn lifecycle.

## Required service semantics

This protocol assumes read-only calls do not create semantic relationships,
session relationships are excluded from normal retrieval, and obsolete edges
can be removed or replaced. If a required feature is unavailable, report that operation as failed. Do not
substitute another type, relationship, completion source, or token estimate.

## Hard rules

- Recall once before substantive work; narrowly classified lightweight social
  prompts are the only exception.
- Retrieve the graph overview once before the first substantive recall in a
  conversation.
- Prefer exact hydration after recall when canonical keys are known.
- Exclude `chat_*` relationships from semantic reasoning.
- In Codex, stage evidence unless the user explicitly requests a memory mutation.
- Empty durable work must not launch a model writer.
- Keep graph writes off the main agent unless the user explicitly requests a
  memory mutation. Keep telemetry off the main agent.
- Never wait for, poll, inspect, read, message, or interrupt the writer.
