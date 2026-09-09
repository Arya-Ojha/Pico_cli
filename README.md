# pico

A Python CLI coding agent — autonomous, tool-using, and session-persistent. Inspired by Pi's modular, plugin-driven architecture.

`pico` operates on a repository on your behalf: reading, writing, and editing files, and running bash commands to build, test, and inspect the code. It runs in **yolo mode** (acts on its own without per-step approval), keeps an append-only, branchable session history, and automatically compacts context to stay within the model's token budget.

```text
pico_ai ─► pico_core ─► pico_sdk ─► pico_tui
 (LLM)      (agent        (library      (terminal UI)
            loop/session)  API)
```

## Features

- **Headless CLI** — `picoCLI run "do a task"` completes a coding task end-to-end with a single prompt.
- **Interactive TUI** — `picoCLI-chat` is a full terminal UI (Textual + Rich) for back-and-forth sessions.
- **Four core tools** — `read`, `write`, `edit` (search/replace patches), and `bash`.
- **One-way LLM gateway** — all models reached through a single streaming OpenRouter client behind one unified "AI call" shape. Responses stream token-by-token.
- **Reasoning & usage** — thinking blocks are preserved in the transcript; token counts are tracked.
- **Session tree** — sessions are persisted as append-only trees of nodes; you can resume, rewind, and fork branches.
- **Auto-compaction** — context is summarised automatically at a token threshold, plus a manual override.
- **Extension hooks** — register providers, tools, and lifecycle hooks; load plugins from a directory.
- **Yolo mode** — no approval prompts: it self-corrects by looping between streaming and tool execution.

## Packages

| Package | Responsibility | ADR |
|---|---|---|
| `pico_ai` | LLM abstraction; unified "AI call" + OpenRouter client | ADR-0001 |
| `pico_core` | The finite-state-machine agent loop + append-only session tree | ADR-0001, ADR-0002 |
| `pico_sdk` | The headless `AgentSession` API + extension/plugin binding | ADR-0001 |
| `pico_tui` | The interactive terminal UI (Textual + Rich) | ADR-0001 |

Dependencies flow one way — `pico_ai` ← `pico_core` ← `pico_sdk` ← `pico_tui` (see [ADR-0001](docs/adr/0001-monorepo-package-split.md)). Sessions are a tree of immutable, append-only nodes (see [ADR-0002](docs/adr/0002-tree-based-session.md)).

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/) (workspace + dev tooling)
- An [OpenRouter](https://openrouter.ai/) API key (the current provider gateway)

## Installation

```bash
uv sync
```

## Configuration

### 1. Set your API key

The CLI reads the key from an environment variable (default `OPENROUTER_API_KEY`):

```powershell
# temporary (current shell)
$env:OPENROUTER_API_KEY = "sk-or-v1-..."

# persistent (Windows, survives new shells)
setx OPENROUTER_API_KEY "sk-or-v1-..."
```

### 2. Optional `settings.json`

Create `~/.pico/settings.json` to override defaults:

```json
{
  "model": "openrouter/free",
  "context_window": 128000,
  "reserve_tokens": 16384,
  "session_dir": "~/.pico/sessions",
  "api_key_env": "OPENROUTER_API_KEY"
}
```

## Usage

### Headless runs

```bash
# complete a task in one shot
uv run picoCLI run "explain what this repo does"

# let the agent run shell commands (bash is on by default)
uv run picoCLI run "run the tests and fix failures"

# work in another directory, pick a model
uv run picoCLI run "summarize this code" --cwd D:\some\repo --model openai/gpt-4o-mini

# resume a previous session by id
uv run picoCLI run "continue" --session <session-id>
```

Flags for `picoCLI run`:

| Flag | Purpose |
|---|---|
| `--no-bash` | Disable unsandboxed bash execution (on by default) |
| `--model <name>` | Override the configured model |
| `--cwd <path>` | Set the working directory |
| `--session <id>` | Resume an existing session |

### Interactive TUI

```bash
uv run picoCLI-chat
```

`picoCLI-chat` shares the same flags. Inside the prompt you can type a message or use:

| Slash command | Key | Action |
|---|---|---|
| `/help` | `F1` | Show help |
| `/history` | `Ctrl+H` | Browse session nodes — pick one to jump to |
| `/compact [text]` | `Ctrl+K` | Compact context (optionally with steering text) |
| `/fork <n or id>` | — | Rewind to a node and start a new branch |
| `/undo` | `Ctrl+Z` | Rewind to the previous user turn |
| `/quit` | `Ctrl+Q` | Save the session and exit |

Tool activity is rendered inline — bash commands echoed before running (green), and tool calls/results shown as color-coded panels (`read` blue, `write` yellow, `edit` magenta, `bash` green).

## Where sessions live

Sessions are persisted as JSONL under `~/.pico/sessions/<id>.jsonl` by default (configurable via `session_dir`).

## Development

```bash
# run all tests
uv run pytest

# typecheck every package
uv run mypy packages/pico_ai/src packages/pico_core/src packages/pico_sdk/src packages/pico_tui/src
```

The test suite is network-free: it drives the whole agent loop through a scripted fake provider (`FakeProvider`) and a temporary filesystem, exercising `pico_ai`, `pico_core`, `pico_sdk`, and `pico_tui`.

## Domain vocabulary

See [CONTEXT.md](CONTEXT.md) for the full glossary. Key terms: **session**, **node**, **payload**, **branch**, **fork**, **turn**, **tool** / **tool request** / **tool result**, **compaction**, **context window**, **provider**, **AI call**, **extension** (plugin).

## Documentation

- [Domain guide for agents](docs/agents/domain.md)
- [Architecture decision records](docs/adr/)
- [Issue-tracker conventions](docs/agents/issue-tracker.md)
- Milestone specs: [headless agent](.scratch/headless-agent/spec.md), [terminal UI](.scratch/pico-tui/spec.md)
