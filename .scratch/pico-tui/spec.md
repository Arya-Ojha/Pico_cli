---
title: Terminal UI (interactive chat)
labels:
  - ready-for-agent
---

> **Status note (2026-09-30):** implemented and since exceeded — the TUI is now
> a full Textual + Rich app (`/help`, `/history`, `/compact`, `/model`,
> `/skills`, `/fork`, `/undo`, `/quit`, todo panel, status bar, pickers).
> The "empty scaffold / deferred rendering" framing below is historical.
> Kept for history — do not re-implement.

# Terminal UI (pico_tui)

## Problem Statement

The headless `pico run "…"` command works end-to-end, but every invocation is a single prompt with no interactive loop. A developer who wants to explore a repo interactively — ask follow-ups, watch tool calls happen, rewind after a bad turn, compact when context grows — needs an interactive terminal interface, not one-shot commands.

Today `pico_tui` is an empty scaffold: no REPL, no streaming view, no command handling.

## Solution

Build the **terminal UI view** in `pico_tui` on top of the existing `AgentSession` API, so that `pico-chat` launches an interactive, streaming session:

- A prompt loop (`pico> `) that streams the agent's response token-by-token.
- Visible tool activity: bash commands echoed before execution, other tool calls shown with name + arguments.
- Slash commands: `/help`, `/compact [instructions]`, `/fork <node-id>`, `/quit`.
- The session is persisted on exit and resumable via `--session`.

The TUI is a thin view over `pico_sdk`: it renders `LoopEvent`s and dispatches slash commands, and does **not** reimplement the loop, tools, or persistence. Per ADR-0001 it sits at the top of the one-way chain `pico_ai ← pico_core ← pico_sdk ← pico_tui`.

## User Stories

1. As a developer, I want an interactive prompt loop, so that I can have a back-and-forth conversation with the agent.
2. As a developer, I want responses to stream token-by-token, so that I can watch output as it is generated.
3. As a developer, I want bash commands echoed before they run, so that I can audit what the agent is doing.
4. As a developer, I want other tool calls (read/write/edit) shown with their arguments, so that I can see what the agent is doing.
5. As a developer, I want a `/help` command, so that I can discover the available commands.
6. As a developer, I want `/compact [instructions]`, so that I can manually compact the context with steering text.
7. As a developer, I want `/fork <node-id>`, so that I can rewind to an earlier node and start a new branch interactively.
8. As a developer, I want `/quit`, so that I can save the session and exit cleanly.
9. As a developer, I want to resume a prior session with `--session <id>`, so that I can continue where I left off.
10. As a developer, I want the same flags as `pico run` (`--model`, `--allow-bash`, `--cwd`), so that I can configure an interactive session the same way.

## Implementation Decisions

- **Thin view.** The TUI renders `LoopEvent`s and dispatches slash commands; the agent loop, tools, session tree, and persistence all come from `pico_sdk` unchanged. Interactive features belong here, not in the core.
- **Shared provider factory.** `pico_sdk.providers.create_provider()` builds the OpenRouter provider from the configured env var; both `pico run` and `pico-chat` use it (one source of truth for provider wiring).
- **Streaming.** The REPL iterates `AgentSession.stream(prompt)` and writes text chunks immediately (no buffering).
- **Blocking input on a thread.** `input()` runs via `asyncio.to_thread`, so the event loop is not blocked while a response streams.
- **Command vocabulary.** `/help` (aliases `/h`, `/?`), `/compact [instructions]`, `/fork <node-id>`, and `/quit` (aliases `/exit`, `/q`). Any other non-empty line is sent to the agent as a prompt.
- **Separate entry point.** `pico-chat` console script, distinct from `pico run`, preserving the one-way dependency (pico_sdk does not import pico_tui).

## Testing Decisions

- **Pure functions first.** `parse_line` (input → command/prompt) and `render_event` (`LoopEvent` → display string) are unit-tested directly.
- **REPL seam.** `TUI` accepts injected `input_fn` and `write` callables, so the interactive loop is tested with a scripted input sequence and a captured output buffer, driven by the same scripted fake provider used for the headless agent.
- **Live smoke.** A piped-input run (`"say hi"` then `/quit`) against a real fast model confirms the end-to-end loop.

## Out of Scope

- Rich terminal rendering (colors, spinners, curses/Textual) — deferred.
- A `/history` or node-browser command for discovering node ids — `/fork` currently takes an id directly.
- Multi-panel or split layouts.
- Any change to the agent loop, tools, or persistence — those live in `pico_core`/`pico_sdk`.

## Further Notes

- `pico run` remains the headless path; `pico-chat` is the interactive path.
- Follow-up specs may add richer rendering and node discovery without touching the core.
