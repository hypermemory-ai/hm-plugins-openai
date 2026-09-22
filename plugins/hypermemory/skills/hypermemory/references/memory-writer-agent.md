# Memory-writer role reference

Use the skill at `../../memory-writer/SKILL.md`. It is the sole
detailed writer contract and includes durability, relationship, validation,
timeline, and token-reporting rules. Do not copy the parent conversation
history.

In Codex, the main agent stages the bounded contract locally and delivers its
answer. Do not call `collaboration.spawn_agent` for memory. The Stop hook owns
post-response dispatch; it supplies completed evidence to a fresh ephemeral
writer only when durable candidates or an explicit memory instruction exist.
The handler performs timeline and token bookkeeping directly without a model.
Never wait for, poll, inspect, message, or reuse the background writer.
