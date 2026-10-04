# pico

A Python CLI coding agent — autonomous, tool-using, and session-persistent. Inspired by Pi's modular, plugin-driven architecture.

`pico` operates on a repository on your behalf: reading, writing, and editing files, and running bash commands to build, test, and inspect the code. It runs in **yolo mode** (acts on its own without per-step approval), keeps an append-only, branchable session history, and automatically compacts context to stay within the model's token budget.

```text
pico_ai ─► pico_core ─► pico_sdk ─► pico_tui
 (LLM)      (agent        (library      (terminal UI)
            loop/session)  API)
```

## Features

- **Headless CLI** — `picocli-chat run "do a task"` completes a coding task end-to-end with a single prompt.
- **Interactive TUI** — `picocli` is a full terminal UI (Textual + Rich) for back-and-forth sessions.
- **Status bar** — the bottom bar always shows `provider | model`, a `thinking` indicator while streaming, and a color-coded context-window bar (`green < 70%`, `yellow < 90%`, `red ≥ 90%`) with the live token estimate.
- **Nine hardcoded core tools** — `read`, `write`, `edit`, `grep`, `fetch`, `websearch`, `bash`, `todo`, and `task` (see ADR-0003, ADR-0005).
- **Todo tracking** — the agent tracks multi-step work with a `todo` tool (add / update / list / clear); the TUI shows the in-memory list in a read-only side panel that appears once the first todo exists. A run only ends once every todo is completed — stopping early nudges the agent back in. When the run ends clean, the list is cleared for the next run (a run stopped by the stuck-model guard keeps its open todos).
- **Sub-agents** — the model delegates self-contained work via the `task` tool; each child runs isolated with its own session file, fresh todos, and restricted tools (overridable per spawn, never escalating past the parent), returning only a summary. Pure-delegation turns fan out in parallel; nesting is bounded (see ADR-0005).
- **One-way LLM gateway** — every provider is reached through one unified streaming "AI call" shape. Responses stream token-by-token.
- **Six native providers** — OpenRouter, OpenAI, Anthropic, Gemini, DeepSeek, and local Ollama, each a one-file adapter (`pico_ai/providers/`) normalizing to the same event shape. Switch with `/provider` (picker + per-provider setup form for API key, URL, model) or `--provider` (see ADR-0004).
- **Reasoning & usage** — thinking blocks stream live, then collapse to one clickable line (click to expand); token counts are estimated continuously for the status bar and compaction.
- **Filterable pickers** — `/history`, `/model`, `/provider`, and `/skills` all open modal pickers with a filter bar: type to narrow (case-insensitive substring), `↑/↓` to move, `Enter` to pick, `Esc` to cancel.
- **Session tree** — sessions are persisted as append-only trees of nodes; you can resume, rewind, and fork branches.
- **Auto-compaction** — context is summarised automatically at a token threshold, plus a manual override.
- **Curated extensions** — observe-only hooks (`session_start`, `pre_tool_use`, `post_tool_use`, `post_tool_failure`) and model-invoked `SKILL.md` skills from `~/.pico/skills/` + `~/.agents/skills/`; permission gating via `allowed_tools` (see ADR-0003).
- **Yolo mode** — no approval prompts: it self-corrects by looping between streaming and tool execution.

## Packages

| Package | Responsibility | ADR |
|---|---|---|
| `pico_ai` | LLM abstraction; unified "AI call" + per-provider adapters | ADR-0001, ADR-0004 |
| `pico_core` | The finite-state-machine agent loop + append-only session tree | ADR-0001, ADR-0002 |
| `pico_sdk` | The headless `AgentSession` API + curated hooks/skills | ADR-0001, ADR-0003 |
| `pico_tui` | The interactive terminal UI (Textual + Rich) | ADR-0001 |

Dependencies flow one way — `pico_ai` ← `pico_core` ← `pico_sdk` ← `pico_tui` (see [ADR-0001](docs/adr/0001-monorepo-package-split.md)). Sessions are a tree of immutable, append-only nodes (see [ADR-0002](docs/adr/0002-tree-based-session.md)).

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/) (workspace + dev tooling)
- An API key for your provider (or a local Ollama server — no key needed)

## Providers

| Provider | Default env var | Notes |
|---|---|---|
| OpenRouter (default) | `OPENROUTER_API_KEY` | Many models through one gateway; `openrouter/free` auto-resolves |
| OpenAI | `OPENAI_API_KEY` | GPT models |
| Anthropic | `ANTHROPIC_API_KEY` | Claude models; model list is curated (`/model <id>` for newer ones) |
| Gemini | `GOOGLE_API_KEY` | Google AI Studio |
| DeepSeek | `DEEPSEEK_API_KEY` | Chat + reasoner (reasoning streams as thinking blocks) |
| Ollama | — | Local server (`OLLAMA_HOST`, default `http://localhost:11434`) |

In the TUI, `/provider` opens a picker with ✓/✗ setup status, then a setup form for that provider's API key, base URL, model, and extras. Only changed values are stored (in `settings.json` — prefer env vars on shared machines); blanks fall back to env/defaults. Effective precedence: field default < environment < stored value. Switching providers resets the model to that provider's stored/default model. Headless: `picocli-chat run --provider ollama "..."`.

## Installation

Requires Python **3.12+**.

```bash
# recommended: isolated install — `picocli` + `picocli-chat` work in any
# directory, like `npm i -g`
pipx install pico-cli

# ... or via uv (same result)
uv tool install pico-cli

# plain pip also works (see the Windows PATH note below)
pip install pico-cli

# try without installing (npx-style)
uvx pico-cli --help
```

Then open a terminal **in any directory** and run `picocli`. On first launch (no API key yet) the provider setup opens automatically — pick a provider, paste the key, done. Keys can also come from environment variables (see Configuration below).

### Windows: `pip install` and PATH

`pip --user` installs (the default when site-packages isn't writable) drop the commands into `~/.local`-style user Scripts, which is often **not** on PATH — that's when `picocli` is "not recognized". Prefer `pipx`/`uv tool` above, or add the dir once (then open a fresh terminal):

```powershell
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$newDir = "$env:APPDATA\Python\Python314\Scripts"  # adjust version to yours
if ($userPath -split ";" -notcontains $newDir) {
  [Environment]::SetEnvironmentVariable("Path", "$userPath;$newDir", "User")
}
```

### From source (contributors)

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
  "api_key_env": "OPENROUTER_API_KEY",
  "provider": "openrouter",
  "providers": {},
  "skills_dir": "~/.pico/skills",
  "allowed_tools": null
}
```

`provider` is the active backend id; `providers` holds per-provider stored values (e.g. `{"openai": {"api_key": "sk-..."}}`) — only changed form values are stored, blanks fall back to env/defaults. `openrouter/free` is an alias: at startup it resolves to the first alphabetically-sorted free model with tool support.

## Usage

### Headless runs

```bash
# complete a task in one shot
uv run picocli-chat run "explain what this repo does"

# let the agent run shell commands (bash is on by default)
uv run picocli-chat run "run the tests and fix failures"

# work in another directory, pick a model
uv run picocli-chat run "summarize this code" --cwd D:\some\repo --model openai/gpt-4o-mini

# resume a previous session by id
uv run picocli-chat run "continue" --session <session-id>

# compact a session headlessly (with optional steering text)
uv run picocli-chat run "/compact focus on the auth refactor"
```

Flags for `picocli-chat run`:

| Flag | Purpose |
|---|---|
| `--no-bash` | Disable unsandboxed bash execution (on by default; ignored when `allowed_tools` is set without `bash`) |
| `--provider <id>` | Provider id (`openrouter`, `openai`, `anthropic`, `gemini`, `deepseek`, `ollama`) |
| `--allow-tools <csv>` | Tool allowlist, e.g. `--allow-tools read,grep,bash` (overrides `settings.allowed_tools`) |
| `--skills-dir <path>` | Override the configured skills directory |
| `--no-skills` | Disable `SKILL.md` loading |
| `--model <name>` | Override the configured model |
| `--cwd <path>` | Set the working directory |
| `--session <id>` | Resume an existing session |

### Interactive TUI

```bash
uv run picocli
```

`picocli` shares the same flags. Inside the prompt you can type a message or use:

| Slash command | Key | Action |
|---|---|---|
| `/help` | `F1` | Show help |
| `/history` | `Ctrl+H` | Browse session nodes — pick one to jump to (forks the session) |
| `/compact [text]` | `Ctrl+K` | Compact context (optionally with steering text) |
| `/model [name]` | — | Change model (`/model` alone opens the interactive model picker; persists to `settings.json`) |
| `/skills` | — | Pick a loaded `SKILL.md` skill — inserts it into the input bar |
| `/provider [id]` | — | Pick the LLM provider, then fill its setup form (key, URL, model) |
| `/fork <n or id>` | — | Rewind to a node and start a new branch |
| `/undo` | `Ctrl+Z` | Rewind to the previous user turn |
| `/quit` | `Ctrl+Q` | Save the session and exit (`/exit`, `/q` also work) |

Every picker has a filter bar at the top — typing narrows the list; `Enter` picks the highlighted row. Tool activity is rendered inline — bash commands echoed before running (green), and tool calls/results shown as color-coded panels (`read` blue, `write` yellow, `edit` magenta, `bash` green, `todo` cyan). `todo` calls stay hidden (only the `todo` result shows); thinking blocks stream in full then collapse to one `💭 thinking: …` line, and bash results collapse to a one-line success/error — click either to expand. Failed tool calls (denied, unknown, or errored) render with a red border so permission gating is visible. The agent's todos also appear in a read-only panel on the right side of the chat while any exist.

## Skills & permissions

Skills are model-invoked `SKILL.md` files — knowledge only, no code execution. Each skill is a directory with a `SKILL.md` (optional `name`/`description` frontmatter + markdown instructions):

```
~/.pico/skills/commit-helper/SKILL.md      # global
~/.agents/skills/commit-helper/SKILL.md    # shared (e.g. opencode/Matt Pocock), also loaded
<project>/.pico/skills/commit-helper/SKILL.md  # project-local, wins on name conflicts
```

```markdown
---
name: commit-helper
description: Use when the user wants to commit code.
---
Run `git status` first, then ...
```

All discovered skills (alphabetical, no cap) are inlined into the system prompt. List them with `/skills` in the TUI, or disable with `--no-skills` / `--skills-dir <path>`.

Permission gating via `allowed_tools` in `settings.json` (`null` = all tools, `[]` = none) or `--allow-tools read,grep,bash`. Denied tools return an `error: tool not allowed` result the model can react to. When `allowed_tools` is set it wins over `--no-bash`; otherwise `--no-bash` disables bash.

## Where sessions live

Sessions are persisted as JSONL under `~/.pico/sessions/<id>.jsonl` by default (configurable via `session_dir`). Both `picocli-chat run` and `picocli` accept `--session <id>` to resume; `/history`, `/fork`, and `/undo` rewind within the tree without deleting nodes.

## Development

```bash
# run all tests
uv run pytest

# typecheck every package
uv run mypy packages/pico_ai/src packages/pico_core/src packages/pico_sdk/src packages/pico_tui/src src/pico

# build all wheels into dist/ (root `pico-cli` is a meta-package: deps + entry points only)
uv build --package pico-cli --out-dir dist
uv build --package pico-cli-ai --out-dir dist
uv build --package pico-cli-core --out-dir dist
uv build --package pico-cli-sdk --out-dir dist
uv build --package pico-cli-tui --out-dir dist
```

The test suite is network-free: it drives the whole agent loop through a scripted fake provider (`FakeProvider`) and a temporary filesystem, exercising `pico_ai`, `pico_core`, `pico_sdk`, and `pico_tui`.

## Domain vocabulary

See [CONTEXT.md](CONTEXT.md) for the full glossary. Key terms: **session**, **node**, **payload**, **branch**, **fork**, **turn**, **tool** / **tool request** / **tool result**, **sub-agent**, **compaction**, **context window**,
**provider**, **AI call**, **hook**, **skill**.

## Documentation

- [Domain guide for agents](docs/agents/domain.md)
- [Architecture decision records](docs/adr/)
- [Issue-tracker conventions](docs/agents/issue-tracker.md)
- Milestone specs: [headless agent](.scratch/headless-agent/spec.md), [terminal UI](.scratch/pico-tui/spec.md)
