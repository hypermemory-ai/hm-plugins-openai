---
name: memory-writer
description: >-
  Post-response HyperMemory writer for bounded durable candidates and a completed
  answer. Applies durability and graph-quality checks; Codex logs separately.
---

# HyperMemory memory-writer agent

Invoke `$memory-writer` and follow
`../skills/memory-writer/SKILL.md` as the sole detailed operating contract.
Do not substitute parent conversation history for the bounded versioned contract.

In Codex `completed_hook` mode, the runner supplies an exclusive completed-answer
claim. Perform only durable graph work and validation. Return the structured
result; the runner handles timeline, tokens, and claim completion. Do not wait
for the parent, delegate again, or send another user-facing response.
