# 02 — Trace row assembly (pure function)

**What to build:** a pure function mapping the active branch (`list[Node]`)
to trace rows: `time (HH:MM:SS from node.timestamp)` | `kind` |
`summary (one line, ~80ch, same truncation as the history picker)` |
`status (ok/error from is_error, plus bash exit code where parseable)` |
`tokens (assistant total, else blank)` | `duration (computed
result.timestamp − request.timestamp paired by tool_call_id; assistant
duration_ms; else blank)`.

**Blocked by:** 01 — Assistant stream timing (for the assistant-duration branch).

**Status:** ready-for-agent

- [ ] One tested function, no Textual/Rich dependency (like `commands.parse_line`).
- [ ] Tool durations pair request/result by `tool_call_id`; orphan results (missing request, e.g. old/odd trees) show blank, never crash.
- [ ] Bash exit codes reuse the existing result-parsing rule (`[exit code: N]` suffix).
- [ ] Non-assistant token cells and non-duration rows are blank (`—`/`—`), never estimated.
