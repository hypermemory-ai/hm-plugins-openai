---
name: hypermemory
description: >-
  Use HyperMemory for scoped durable context without polluting the graph. Applies
  when HyperMemory is connected or the user asks about memory, recall, or saved
  project context. Retrieves relevant memories and stages bounded evidence for
  post-response persistence. Codex completion hooks own writer dispatch.
---

# HyperMemory MCP — Main Agent Protocol

## READ THIS ENTIRE FILE BEFORE MEMORY WORK

**STOP: the summary below is not the protocol. You MUST read this entire file,
including the complete contract schema, before staging or writing memory. Skipping
the rest produces incomplete, malformed, or useless memories.**

## WRITE THE FUCKING MEMORIES — critical summary

- Recall relevant memory before substantive work; get the overview once before
  the conversation's first substantive recall.
- If the user explicitly asks to remember, store, update, forget, or create graph
  entities, perform and verify that mutation now.
- Otherwise, stage every plausible durable fact, decision, preference, correction,
  outcome, artifact, and relationship for the completion writer. **If durable
  information exists, an empty `durable_candidates` list is a failure.**
- Never crush broad research into one vague node. When useful, stage an umbrella
  topic and separate candidates for each independently reusable semantic unit,
  connected by evidence-backed relationships.
- Let meaning and future retrieval value determine breadth and depth. Never impose
  arbitrary node counts, child counts, or fixed hierarchy depth.
- Create nodes before their relationships. Never invent facts, keys, or edges.
- On Codex, stage the contract and answer immediately. Do not spawn, wait for,
  poll, inspect, message, or synchronize with the memory-writer.

The rules and schema below are mandatory, not optional detail.

## Classify and recall

Before the first substantive answer, call `hm_get_overview` once, then recall
for the active scope and intent. Hooks request but do not execute these reads.
Hydrate known keys exactly; recall again only for a distinct necessary entity.

A greeting, thanks, or acknowledgement that changes no task state is lightweight:
do not recall or stage candidates. Task-dependent confirmations are continuations.

Use recall only when relevant, in scope, valid without session links, and
consistent with newer instructions and workspace evidence. Treat `chat_*` links
only as provenance and exclude them from reasoning, ranking, and traversal.
Inspect only the best three to five semantic candidates unless more are needed.

## Explicit memory management

For explicit inspection or mutation, retrieve the exact entities, use the
appropriate graph or timeline tools, perform the request directly, and report
only verified results.

Create nodes before relationships. `hm_update` and `hm_forget` target an edge
with its read-response `entity_id` as `edge_id`; use `edge_version` for optimistic
concurrency. To change immutable endpoints or type, delete and recreate the edge.
Do not restage direct mutations. Upload files directly only when requested.

## Capture durable evidence

On every non-lightweight turn, identify durable information without waiting for
“remember.” Stage independently reusable facts, decisions, preferences,
corrections, concepts, plans, project state, outcomes, artifacts, and links.

Decompose broad material semantically: use an umbrella candidate when useful,
plus distinct supporting candidates and relationships. Combine only inseparable
claims; split anything independently retrievable, updateable, contradictable,
supersedable, or relatable. The source—not a quota—sets breadth and depth.

Capture aggressively but truthfully. Preserve specificity and let the writer
apply the final gate; a short answer does not justify omitting candidates.

## Codex completion dispatch

The hook supplies `finalizer`, `listener`, and `job`. After work, pass the bounded
contract as JSON on stdin:

```text
python3 <finalizer> enqueue --job <job>
```

This makes no MCP writes. Answer immediately; the Stop hook adds the completed
answer and invokes `$memory-writer` only for candidates or an explicit memory
instruction. **Never spawn it from the main Codex turn.**

Use this complete schema:

```json
{
  "schema_version": "2.10.0",
  "turn_id": "host-supplied unique id",
  "occurred_at": "ISO-8601 timestamp when available",
  "active_scope": {"project_key": "known canonical key or null", "project_name": "plain name or null", "other_anchor_keys": []},
  "request": {"intent": "one sentence", "explicit_memory_instruction": "concise instruction or null"},
  "outcome": {"status": "completed | partial | blocked | informational", "summary": "one or two factual sentences", "durable_artifacts": []},
  "durable_candidates": [
    {
      "action_hint": "store | update | forget | supersede",
      "key_hint": "canonical key or null",
      "node_type": "canonical type",
      "description_draft": "short searchable summary",
      "facts": {},
      "relationship_changes": {
        "add": [{"target_key": "canonical key", "meaning": "why the two durable entities are connected"}],
        "remove_or_replace": [{"target_key": "canonical key", "current_meaning": "obsolete or conflicting relationship", "reason": "why it is no longer valid"}]
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
  "token_listener": {"listener_path": "host path or null", "job_path": "host path or null"}
}
```

`activity_segments` weights total 100 and use only: `reasoning`, `memory`,
`context`, `doc_processing`, `automation`, `personal`, `chatting`, `research`,
`design`, `calculations`, `coding`, `planning`, `productivity`, `writing`, or
`unmatched`; there is no default or estimate.

Put temporary blockers, routine progress, tests, and recoverable state in
`timeline_only`. Exclude credentials, prompts, transcripts, hidden reasoning,
raw outputs, tool payloads, and large code. Never fabricate anything.

The contract is evidence. The writer independently stores, updates, supersedes,
forgets, or skips after checking the answer. Empty candidates are correct only
when no plausible durable information exists.

## Other hosts and failures

Without completion hooks, delegate only when permitted and never claim the writer
saw a later answer. Disclose unavailable persistence; never add a dummy response.

If recall fails, continue without claiming it succeeded. Ask for reconnection
only when necessary. Put temporary failures in the timeline, not durable memory.

Required semantics: reads create no relationships, session links stay outside
retrieval, and obsolete edges are removable. Report unsupported operations as
failed. **No fallbacks:** never substitute types, relationships, completion
sources, token estimates, or invented success.

## Non-negotiable rules

- Read the entire file; the summary is insufficient.
- Recall before substantive work; get the overview first.
- Directly execute explicit memory mutations; otherwise stage every plausible
  durable semantic unit for the completion writer.
- Never collapse broad knowledge or impose arbitrary counts.
- Exclude `chat_*` links; keep telemetry off the main agent.
- Stage, answer immediately, and never synchronize with the writer.
