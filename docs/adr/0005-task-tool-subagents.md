# 0005 — Sub-agents via the hardcoded `task` tool

- Status: Accepted
- Date: 2026-09-30

## Context

Long delegated work (parallel research, isolated chunks) pollutes the
parent's context when done inline: every file read and tool result competes
for the same token budget. The headless-agent spec deferred sub-agents as
"future plugins". The alternative designs were branch-based delegation (the
child runs as a forked branch inside the parent session tree) and an
orchestrator/worker API with no model-invoked spawning.

We chose model-invoked spawning through a hardcoded core tool, consistent
with ADR-0003: no plugin kernel, no new extension surface.

## Decision

- A 9th hardcoded core tool, `task`, lives in `pico_core/subagents.py`
  (`SpawnTool` + `ChildSpec` + `ChildFactory`). The model delegates with a
  description and a full prompt; a fresh child `AgentLoop` runs on its own
  `Session` to completion, and only the child's final summary returns as
  the tool result.
- Each child persists as its own session file (`<id>.jsonl` in
  `session_dir`); the parent tree holds only the summary. No `Session`
  model change — a new payload kind would ripple through context assembly
  and every renderer.
- Least privilege by default: children get `read`, `grep`, `fetch`,
  `websearch`, fresh todos, and no nested spawning. The per-spawn
  `allowed_tools` overrides the default but is intersected with the
  parent's own gate, so a parent can never escalate. `task` is granted to
  a child only explicitly, and nesting is capped (`MAX_DEPTH = 2`).
- Bounding: per-child `max_turns` (new additive `AgentLoop.run/stream`
  parameter; `RunResult.truncated` flags capped runs) plus a wall-clock
  timeout. Overruns, crashes, and invalid arguments surface as error tool
  results — the parent always survives.
- Parallelism: a turn of pure `task` calls fans out via `asyncio.gather`;
  requests record first, results append in call order (deterministic).
  Mixed turns stay sequential, preserving the existing event order.
- Hooks: no vocabulary change. Spawning is a tool call, so
  `pre/post_tool_use` fire automatically, and the child shares the
  parent's `ExtensionManager`. The TUI renders delegation through the
  existing tool panels (plus a `task` color).

## Consequences

- Delegation costs one tool round-trip of context instead of the whole
  transcript; children compact independently under the same threshold.
- Children share the parent's provider object, working directory, and
  system prompt/skills; concurrent children share the filesystem exactly
  as sequential tool calls already do.
- Live child progress in the TUI is deferred — v1 shows the call and the
  returned summary. `settings.json` knobs for sub-agent defaults are
  deferred; per-spawn arguments cover configuration.
- If deeper nesting or cross-session child resume becomes common, revisit
  with explicit child-session management (see deferred multi-session work).
