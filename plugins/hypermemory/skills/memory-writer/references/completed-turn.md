# Completed Codex turns

Stop is the sole completion source. The main agent stages a bounded contract;
Stop captures the delivered answer and dispatches the completion handler.
The handler holds the exclusive claim. The writer performs graph work only.
Its evidence is the staged `contract`, the `completion` excerpt, and the
parent session's `session_id`, which the writer passes on `hm_recall` and
`hm_store`.
There is no pre-answer, transcript-based, or alternate execution path.

Each memory, timeline, and exact token operation records its own receipt or
explicit error. A failure in one does not suppress the other two. Unconfirmed
remote effects require review and are not automatically replayed.
