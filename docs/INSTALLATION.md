# Installation

This repository is a Git-backed Codex marketplace named `hypermemory-ai`.
Registering it makes the catalog available; it does not install either plugin.

## Prerequisites

- A current Codex CLI or Codex in the ChatGPT desktop app
- A HyperMemory account
- Python 3.10 or newer and `pipx` for the HyperColab local shim

## 1. Register the marketplace

```bash
codex plugin marketplace add hypermemory-ai/hm-plugins-openai
```

Confirm the source:

```bash
codex plugin marketplace list
```

The catalog exposes two plugin IDs:

- `hypermemory@hypermemory-ai`
- `hypercolab@hypermemory-ai`

## 2. Install HyperMemory

```bash
codex plugin add hypermemory@hypermemory-ai
```

The plugin connects to:

```text
https://api.hypermemory.io/mcp
```

Complete OAuth when prompted. No API key is stored in the plugin package. The
server handles authorization-code flow, PKCE, refresh tokens, and dynamic
client registration.

Start a new task after installation so Codex loads the MCP server and bundled
skill. In Codex CLI, run `/hooks`, review the HyperMemory hook definition, and
trust it to enable lifecycle recall enforcement and the exact local token
listener.

## 3. Install HyperColab

HyperColab resolves the current Git root, remote, branch, and worktree locally.
Install its CLI and stdio MCP shim before installing the plugin:

```bash
pipx install "git+https://github.com/hypermemory-ai/hm-plugins-openai.git#subdirectory=packages/hypercolab-cli"
hypercolab login
hypercolab doctor
```

Then install the plugin:

```bash
codex plugin add hypercolab@hypermemory-ai
```

Inside a repository enrolled in HyperColab, optionally install the non-blocking
Git activity hooks:

```bash
hypercolab hooks install
```

Start a new task and run `/hooks` to review and trust the HyperColab lifecycle
hooks. Repositories that are not registered with HyperColab remain unaffected.

## 4. Verify

```bash
codex plugin list
hypercolab status
```

Useful first prompts:

- `What do you remember about this project?`
- `Join this HyperColab project and show active work.`
- `Claim the files needed for this task before editing.`

## Updates

Refresh the Git catalog and reinstall the desired package:

```bash
codex plugin marketplace upgrade hypermemory-ai
codex plugin add hypermemory@hypermemory-ai
codex plugin add hypercolab@hypermemory-ai
pipx upgrade hypercolab
```

Start a new task after updating. If a hook changed, Codex will require review of
the new definition because hook trust is tied to its exact content.

When moving from HyperMemory 2.9.1 to 2.9.2, fully quit Codex before the update
and reopen it afterward. Version 2.9.1 stored a version-specific executable path
in running tasks. HyperMemory 2.9.2 resolves the currently installed version at
hook execution time so later updates do not retain that deleted cache path.

## Removal

```bash
codex plugin remove hypermemory --marketplace hypermemory-ai
codex plugin remove hypercolab --marketplace hypermemory-ai
codex plugin marketplace remove hypermemory-ai
pipx uninstall hypercolab
```

Removing the marketplace does not delete data already stored in HyperMemory or
HyperColab. Remove local HyperColab Git hooks before uninstalling the CLI if you
installed them:

```bash
hypercolab hooks uninstall
```

## ChatGPT surfaces

The Git marketplace is intended for Codex installation, local development, and
workspace testing. Public one-click installation for normal ChatGPT and Codex
users requires publishing through the universal Plugins Directory.

For pre-publication HyperMemory testing in ChatGPT developer mode, register
`https://api.hypermemory.io/mcp` and use the bundled HyperMemory main skill
plus its parent-only memory-writer skill. Since consumer ChatGPT does not expose
Codex rollout JSONL counters, HyperMemory uses an uncertainty-labelled token
estimate there.

HyperColab's full behavior depends on its local stdio shim and Git context. It
therefore requires a local coding surface that can execute `hypercolab`; a web
chat without access to that process cannot provide repository claims or Git
activity capture.

## Troubleshooting

### MCP tools are missing

Confirm the plugin is installed and enabled, then start a new task. For
HyperColab, also confirm `hypercolab` is on `PATH` with `hypercolab doctor`.

### Hooks do not run

Run `/hooks`, inspect the source and current hash, and trust the definition.
Hooks are non-managed and intentionally skipped until trusted.

### OAuth did not open

For HyperMemory, invoke a HyperMemory MCP tool and complete the connection flow.
For HyperColab, run `hypercolab login` directly in a terminal.

### A HyperColab write is blocked

Run `hypercolab sync` to inspect current ownership. Coordinate a handoff or wait
for the conflicting lease instead of bypassing a live claim.
