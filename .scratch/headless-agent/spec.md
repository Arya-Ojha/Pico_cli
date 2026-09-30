---
title: Headless agent runtime
labels:
  - ready-for-agent
---

# Headless Agent Runtime

## Problem Statement

The user wants a Python CLI coding agent — inspired by Pi's modular, plugin-driven architecture — that can autonomously (yolo mode) perform coding tasks in a repository: reading, writing, and editing files, and running bash commands, while keeping a resumable, branchable session history and automatically compacting context to stay within the model's token budget.

Today the codebase is only a scaffold: four empty packages (`pico_ai`, `pico_core`, `pico_sdk`, `pico_tui`) with no agent loop, no LLM integration, no session persistence, and no tools. There is nothing runnable yet.

## Solution

Build the **headless agent runtime** across three packages, so that `pico run "…"` works end-to-end without a UI:

- **`pico_ai`** — a unified "AI call" type that normalises every provider behind a single request/response shape, with a streaming, async OpenRouter client as the first (and only) gateway.
- **`pico_core`** — an explicit finite-state-machine agent loop that maintains an append-only session tree and auto-compacts context.
- **`pico_sdk`** — a headless `AgentSession` API plus the extension/plugin binding, exposed through a `pico run "…"` command.

The TUI (`pico_tui`) is a later milestone.

## User Stories

1. As a developer, I want to run pico headlessly with a single prompt (`pico run "…"`), so that I can automate a coding task without an interactive UI.
2. As a developer, I want pico to read files in my repo, so that the agent can understand the codebase before acting.
3. As a developer, I want pico to write (create or overwrite) files, so that it can produce new code.
4. As a developer, I want pico to edit files surgically with search/replace patches, so that it can make targeted changes without rewriting whole files.
5. As a developer, I want pico to run bash commands, so that it can build, test, and inspect the repo.
6. As a developer, I want pico to operate in yolo mode with no per-step approval, so that it can complete multi-step tasks autonomously.
7. As a developer, I want to see each bash command echoed before it runs, so that I can audit what the agent is doing.
8. As a developer, I want an opt-in flag that permits unsandboxed bash execution, so that I explicitly consent to the risk.
9. As a developer, I want pico to loop between streaming and tool execution until the task is done, so that it can self-correct (e.g. run tests, then fix failures).
10. As a developer, I want tool results fed back into the conversation, so that the agent can act on command output and file contents.
11. As a developer, I want pico to handle tool errors (non-zero exit, missing file) gracefully, so that it can recover or report rather than crash.
12. As a developer, I want to reach multiple LLM providers through one gateway, so that I can switch models without code changes.
13. As a developer, I want streaming responses, so that I can watch output as it is generated.
14. As a developer, I want thinking (reasoning) blocks preserved in the transcript, so that I can review the agent's reasoning.
15. As a developer, I want assistant responses to carry usage/token counts, so that I can track cost.
16. As a developer, I want my session persisted as an append-only tree of nodes, so that I can resume and review it later.
17. As a developer, I want to fork from an earlier node, so that I can rewind after a broken change without starting over.
18. As a developer, I want only the active branch sent to the model, so that abandoned branches do not waste tokens.
19. As a developer, I want automatic context compaction, so that long sessions stay within the model's context window.
20. As a developer, I want to manually trigger compaction with steering instructions, so that I can control what gets summarised.
21. As a developer, I want reserve tokens held back for the model's response, so that compaction leaves room for output.
22. As a developer, I want a config file for settings, so that I can tune the agent without editing code.
23. As a developer, I want sessions stored per-session in a known location, so that I can find and inspect them.
24. As a developer, I want an explicit, observable state machine, so that I can reason about and test the agent's behaviour.
25. As a developer, I want failures surfaced as a clear error state, so that I can diagnose what went wrong.
26. As a developer, I want a headless library API (`AgentSession`), so that I can embed pico in my own scripts and tools.
27. As a developer, I want to feed input and harvest output streams programmatically, so that I can build automation on top of pico.
28. As a plugin author, I want to register custom tools, so that I can extend the agent's capabilities.
29. As a plugin author, I want to register custom providers, so that I can add LLM backends beyond the gateway.
30. As a plugin author, I want lifecycle hooks (session start, before/after tool), so that I can intercept and react to agent events.
31. As a plugin author, I want plugins loaded from a plugins directory and via explicit registration, so that I can choose how to distribute them.
32. As a developer, I want the unified "AI call" shape used for every provider, so that provider-specific details are normalised away.

## Implementation Decisions

- **Monorepo split (ADR-0001).** Four packages with a one-way dependency chain `pico_ai ← pico_core ← pico_sdk ← pico_tui`. This spec covers the first three; `pico_tui` is out of scope.
- **Tree-based append-only session (ADR-0002).** A session is a tree of immutable nodes; nodes are never edited or deleted, only built upon. Branches are root-to-leaf timelines; forking rewinds to an earlier node and starts a new branch. Only the active branch is sent to the model.
- **Unified "AI call".** A single provider-agnostic request/response shape. OpenRouter is the single gateway (one HTTP client, one key, all models). Native adapters are deferred to plugins.
- **Streaming + async.** The core is `asyncio`-based; responses stream token-by-token.
- **Explicit FSM.** The agent loop is a finite state machine. States (from the design):

  ```
  idle → streaming ⇄ tool_executing → done
           ↓
        compacting          (triggered by token threshold)
  error                    (reachable from any state)
  ```

  Yolo mode means there is no approval/confirmation state.
- **Node payload vocabulary.** Each node's payload is one of: user, assistant (block-granular: text / thinking / tool-call), tool request, tool result, compaction summary. Node shape (from the design):

  ```
  Node { id, parent_id, timestamp, payload }
  payload: User | Assistant | ToolRequest | ToolResult | CompactionSummary
  ```

- **Four core tools.** `read` (file), `write` (create/overwrite), `edit` (surgical search/replace patch), `bash` (shell). Bash runs unsandboxed with a visible command echo and an opt-in flag.
- **Compaction.** Auto-triggered when `contextTokens > contextWindow − reserveTokens` (reserve default 16384), plus a manual `/compact [instructions]` override. Compaction summarises older turns into a compaction-summary node, keeping the system prompt and a recent window.
- **Config & sessions.** Settings in `~/.pico/settings.json`; sessions persisted as `.jsonl` under `~/.pico/sessions/<id>.jsonl`.
- **Extension binding.** `register_tool`, `register_provider`, and lifecycle hooks (`on_session_start`, `tool.before.*`, `tool.after.*`). Plugins load from a plugins directory and via explicit registration.
  > **Superseded by ADR-0003 (2026-09-30):** the generic binding was removed in favour of a hardcoded core with curated extensions (fixed observe-only hooks + `SKILL.md` skills). See `docs/adr/0003-hardcoded-core.md`.
- **Models in pydantic v2.**
- **Deferred as plugins.** MCP, sub-agents, plan mode, and local to-do tracking are explicitly out of the core and will be added later as plugins.

## Testing Decisions

- **What makes a good test.** Assert on external behaviour — the observable session tree, the tool calls made, and the FSM outcome — not on internal implementation. Drive everything through the public `AgentSession` API.
- **Primary seam — the provider boundary.** Inject a scripted fake "AI call" (predetermined responses and tool requests), and the whole loop becomes deterministic and network-free. This is the single high seam.
- **Supporting seam — the filesystem boundary.** The four tools are tested against a temporary working directory, since they touch the disk.
- **Modules under test.** `pico_ai` (provider normalisation, streaming parsing), `pico_core` (FSM transitions, session-tree append/fork, compaction trigger), `pico_sdk` (`AgentSession` behaviour, extension registration and hooks).
- **Prior art.** None — this is a fresh codebase. Use `pytest` with `pytest-asyncio`; the fake provider returns scripted streams.

## Out of Scope

- `pico_tui` (the terminal UI) — a follow-up spec.
- MCP, sub-agents, plan mode, and local to-do tracking — future plugins.
- Native provider adapters beyond the OpenRouter gateway — future plugins.
- Sandboxing / containerization of bash execution.
- Telemetry.
- Multi-session management beyond single-session persistence.

## Further Notes

- Build order: `pico_ai` → `pico_core` → `pico_sdk`.
- First milestone: headless `pico run "…"` works end-to-end (prompt → loop → tools → persisted session).
- Respect ADR-0001 (monorepo split) and ADR-0002 (tree-based session) throughout.
