# HyperMemory plugin for ChatGPT and Codex

This universal plugin bundles a ChatGPT/Codex main-agent skill, a post-response
memory-writer skill with a strict quality gate, the OAuth-protected HyperMemory
MCP server, and Codex lifecycle enforcement. Token reporting requires an exact supported usage source.

## Behavior

- Main agent: project-scoped recall for substantive prompts, exact hydration
  when keys are known, and a relevance gate that rejects unrelated projects and
  session-only relationships. Narrow standalone greetings and acknowledgements
  skip retrieval.
  Overview (once per conversation) and recall must finish before substantive
  task work and the answer. `PreToolUse` denies other tools until hook-observed,
  successful MCP receipts prove those reads occurred in order.
- Codex main agent: stages a bounded local contract and delivers its answer.
  It does not launch a memory-writing agent.
- Completion hook: after the final answer exists, starts a bounded background
  handler. It invokes the memory writer only for durable candidates or an
  explicit memory instruction. The writer reconciles the actual answer, applies
  the durability gate, writes justified changes, and verifies them.
  Its recall is limited to checking existing memories and conflicts before a
  write. Overview is excluded from the completion transport's enabled tools.
- Bookkeeping: timeline and exact token reports use direct authenticated MCP
  calls through `codex app-server`; they do not invoke a model. The private
  ephemeral session reuses Codex OAuth without extracting credentials. Recursive
  hooks and unrelated tools are disabled in that session only.
- Five Codex hook events enforce the lifecycle. `SessionStart` and
  `UserPromptSubmit` inject the complete skill, `PreToolUse` gates substantive
  tools, `PostToolUse` records successful overview/recall receipts, and `Stop`
  rejects missing reads or an invalid handoff with a continuation prompt.
- Codex hooks remain subject to normal trust. A compliant Stop never resumes the
  parent or adds a second response. Version-safe hook commands continue to
  resolve the currently installed scripts.
- A surface without exact usage reports that limitation; no token estimate is
  substituted.

The token listener parses only `token_count` records. It does not return or
upload prompts, model responses, tool arguments, or tool results.

Cached reads are reported separately and never included in the fresh-token
total. Stable rollout identities prevent archived transcripts from being
counted twice, and an implausible fresh-token spike is rejected before it can
reach `hm_tokens`.

The finalizer consumes a bounded public-answer excerpt from Stop, separately
from token counters. Local handoffs are private (0600); successful completion
removes the excerpt and contract. Duplicate completion events cannot start two
handlers. Missing handoffs produce no invented memories. Unknown completion
formats remain pending, and partial or ambiguous writes require review.

The completion handler is a finite per-turn process, not a permanent daemon.
It requires the native `codex` executable and existing HyperMemory OAuth login.
Only the worker receives the completed evidence. The parent never waits or
polls. Stop is the sole completion source; enforcement uses hook receipts and
local queue state, never the unstable transcript format or a model claim.

Memory, timeline and token writes record independent receipts or exact errors.
A failure cannot suppress the other operations. Missing exact counters fail the
token operation; no estimates, invented handoffs or substitute categories are used.
The writer reports native model usage under its own session and actual model.
Partial or ambiguous results are retained for review and are not replayed.

## Install from the public Git marketplace

Install directly from GitHub:

```bash
codex plugin marketplace add hypermemory-ai/hm-plugins-openai
codex plugin add hypermemory@hypermemory-ai
```

Restart the ChatGPT desktop app or start a new Codex session. Complete the
OAuth sign-in when prompted. In Codex CLI, open `/hooks`, review the bundled
hook definition, and trust it; Codex does not run non-managed plugin hooks until
the user explicitly trusts their current hash.

ChatGPT web local testing requires registering
`https://api.hypermemory.io/mcp` in developer mode. A public directory
submission should use the **With MCP** flow and submit this MCP server directly;
it does not require a checked-in `.app.json`.

## Validate

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/hypermemory
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/hypermemory/skills/hypermemory
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/hypermemory/skills/memory-writer
pytest -q tests/test_hypermemory_plugin.py
```
