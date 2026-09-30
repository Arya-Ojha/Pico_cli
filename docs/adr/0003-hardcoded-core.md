# 0003 — Hardcoded core with curated extensions (Claude Code-style)

- Status: Accepted
- Date: 2026-09-30

## Context

Pico had a generic plugin binding (`register_tool`, `register_provider`,
lifecycle hooks, `load_plugins` directory) modelled on Pi / DeepSeek Harness
"everything is a plugin". The alternative is Claude Code's model: a hardcoded
host that keeps sovereignty over the loop, tools, provider, session and prompt,
with a small curated extension surface (skills, hooks, MCP) that observes
rather than governs.

We chose the hardcoded core: simpler, predictable, supportable, smaller blast
radius. No plugin kernel, no replaceable loop/session/registry.

## Decision

- The core is non-replaceable: the eight tools (`read`, `write`, `edit`,
  `grep`, `fetch`, `websearch`, `bash`, `todo`), the `AgentLoop` FSM, the
  OpenRouter provider factory, session persistence, compaction, and the system
  prompt live in the host and are wired directly in `AgentSession`.
- Generic plugin APIs are removed: no plugins directory, no
  `register_tool` / `register_provider` / `use_provider` / `load_plugins`.
- Curated extensions only:
  - **Hooks** — fixed vocabulary, observe-only: `session_start`,
    `pre_tool_use`, `post_tool_use`, `post_tool_failure`. Legacy
    `on_session_start` / `tool.before.*` / `tool.after.*` names are accepted
    as aliases. Hooks cannot mutate args/results or veto execution.
  - **Skills** — `SKILL.md` markdown files discovered from
    `~/.pico/skills/*/SKILL.md` (plus project-local override) and inlined
    into the system prompt by trigger description.
  - **MCP** — deferred; when added it will be a tool source, not a kernel.
- **Permission gating** — `Settings.allowed_tools` (default: all core tools)
  is enforced in `AgentLoop._execute_tool`; disallowed tools return an error
  result instead of running. `BashTool(enabled)` remains the sandbox flag.

## Consequences

- Predictable behaviour and security: extensions are guests, never
  co-governors of the loop.
- Advanced users who need custom tools/providers fork the host instead of
  writing a plugin. If that need becomes common, revisit with a real kernel
  (see rejected DeepSeek-style alternative).
- `ToolRegistry.unregister` stays internal for tests only, not a public
  extension point.
- Tests assert on the hardcoded core, not on generic registration.
