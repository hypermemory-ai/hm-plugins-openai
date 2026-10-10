# HyperMemory quality quick reference

The main protocol is `../SKILL.md`; the writer's sole detailed contract is
`../../memory-writer/SKILL.md`.

- Scope recall to the active project and current intent.
- Ignore `chat_*` relationships for semantic reasoning.
- Prefer exact hydration when canonical keys are known.
- Send the writer a bounded versioned contract, not conversation history.
- Empty durable-candidate lists are normal.
- Stage one candidate per user rule (`preference`), choice (`decision`), and
  finding, risk, or lesson (`fact`); never bury one inside an event.
- Put each changed status or value in the candidate's `facts.changed` as
  `old → new`, and machine details in `facts`, never in the description draft.
- Keep temporary blockers, task mechanics, command output, and recoverable
  repository state in the timeline rather than the durable graph.
- Never wait for, poll, inspect, read, message, or reuse the writer.
