---
name: hypermemory
description: >-
  Use HyperMemory for relevant durable context and bounded post-response
  persistence when memory, recall, or saved project context applies.
---

# HyperMemory MCP — Main Agent Protocol

## READ THIS ENTIRE FILE BEFORE MEMORY WORK

**STOP: this summary is not the protocol. You MUST read the entire file,
including the complete schema, before staging or writing memory. Skipping it
produces incomplete, malformed, or useless memories.**

## WRITE THE FUCKING MEMORIES — critical summary

- Recall before substantive work; get the overview before the first recall.
- Execute and verify explicit requests to store, update, forget, or create graph
  entities. Otherwise stage every plausible durable fact, decision, preference,
  correction, outcome, artifact, and relationship for the completion writer.
- **If durable information exists, an empty `durable_candidates` list is a failure.**
- Never crush broad research into one vague node. When useful, stage an umbrella
  topic, independently reusable semantic units, and evidence-backed relationships.
- Meaning and retrieval value set breadth and depth. Never impose arbitrary node counts,
  child counts, or fixed hierarchy depth.
- Create nodes before relationships; never invent facts, keys, or edges.
- On Codex, stage the contract and answer immediately. Never spawn, wait for,
  poll, inspect, message, or synchronize with the memory-writer.

Everything below is mandatory.

## Recall

Before the first substantive answer, call `hm_get_overview` once, then recall for
the active scope and intent. Hooks request but do not execute these reads. Hydrate
known keys exactly; recall again only for a distinct necessary entity.

A greeting or acknowledgement that changes no task state is lightweight: do not
recall or stage candidates. Task-dependent confirmations are continuations.

Use recall only when relevant, in scope, valid without session links, and
consistent with newer instructions and workspace evidence. Treat `chat_*` links
only as provenance; exclude them from reasoning, ranking, and traversal. Inspect
only the best three to five semantic candidates unless more are necessary.

## Direct mutations

For explicit inspection or mutation, retrieve the exact entities, use the proper
graph or timeline tools, perform the request directly, and report verified results.
Create nodes before relationships. Do not restage direct mutations; upload files
directly only when requested.

To target an edge, pass its read-response `entity_id` as `edge_id` to `hm_update`
or `hm_forget`; use `edge_version` for optimistic concurrency. Endpoints and type
are immutable, so delete and recreate an edge to change them.

## Stage durable evidence

On every non-lightweight turn, stage independently reusable facts, decisions,
preferences, corrections, concepts, plans, project state, outcomes, artifacts,
and links without waiting for “remember.”

For broad material, use an umbrella candidate when useful plus distinct supporting
candidates and relationships. Combine only inseparable claims; split anything
independently retrievable, updateable, contradictable, supersedable, or relatable.
The source—not a quota—sets breadth and depth. Capture aggressively but truthfully;
a short answer does not justify omitting durable candidates.

Stage one candidate per thing a later session acts on, even when you mention it
while telling what you did:

- each rule the user stated: a `preference`, close to verbatim, with its scope;
- each choice: a `decision` with `facts` chosen, rejected, and rationale;
- each finding, open risk, or lesson: a `fact`; what happened: an `event`.

Write `request.intent` and each problem in the user's words for what they saw
or want. Hashes, IDs, paths, hosts, and ports go in `facts`, never in
`description_draft`. When a status or value changed (approved, deployed,
declined, superseded; a cutoff, a limit), stage an `update` or `supersede`
candidate whose `facts.changed` lists each `old → new`, such as
`"rerank cutoff 0.60 → 0.50"`: the writer rewrites every memory still saying
the old one. When this turn proved an earlier memory wrong, stage an `update`
or `forget` candidate with the evidence in `facts`.

## Codex handoff

The hook supplies `finalizer`, `listener`, and `job`. After work, pass the bounded
contract as JSON on stdin, then answer immediately:

```text
python3 <finalizer> enqueue --job <job>
```

This makes no MCP writes. The Stop hook adds the completed answer and invokes
`$memory-writer` only for candidates or an explicit memory instruction. Never
spawn it or run telemetry from the main Codex turn.

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

`activity_segments` weights total 100 and use only `reasoning`, `memory`,
`context`, `doc_processing`, `automation`, `personal`, `chatting`, `research`,
`design`, `calculations`, `coding`, `planning`, `productivity`, `writing`, or
`unmatched`; there is no default or estimate.

Put temporary blockers, routine progress, tests, and recoverable state in
`timeline_only`. Exclude credentials, prompts, transcripts, hidden reasoning,
raw outputs, tool payloads, and large code. Never fabricate anything. The writer
independently stores, updates, supersedes, forgets, or skips after checking the
answer; empty candidates are correct only when nothing plausibly durable exists.

## Failures and other hosts

Without completion hooks, delegate only when permitted and never claim the writer
saw a later answer. Disclose unavailable persistence; never add a dummy response.
If recall fails, continue without claiming success, request reconnection only when
necessary, and put temporary failures in the timeline rather than durable memory.

Reads must create no relationships, session links stay outside retrieval, and
obsolete edges must be removable. Report unsupported operations as failed.
**No fallbacks:** never substitute types, relationships, completion sources,
token estimates, or invented success.
