# 0006 — Trace view durations: computed tool spans vs persisted assistant spans

- Status: Accepted
- Date: 2026-10-03

## Context

The trace view (see CONTEXT.md: **trace view** / **trace row**) shows one row
per session node with time, kind, summary, status, tokens, and duration.
Time, kind, and summary already exist on every node (`Node.timestamp`,
payload kinds, 80-char truncation as in the history picker). Status is derived
(`is_error` → ok/error, plus bash exit code). Tokens exist only on assistant
nodes (`Usage`). Duration existed nowhere, and the two row kinds that need it
have opposite constraints:

- A `tool_result` node can be paired with its `tool_request` node by
  `tool_call_id`, so its duration is computable as
  `result.timestamp − request.timestamp` — no schema change, and it works on
  sessions persisted before the trace view existed.
- An `assistant` node is appended once, after streaming completes
  (`pico_core/fsm.py`: `stream()`), so there is no start timestamp to diff
  against. Its duration (provider stream wall-time) must be measured live.

The alternatives were computed-everywhere (impossible for assistant rows
without a start time), persisted-everywhere (a `duration_ms` on
`ToolResultPayload` too — a second schema change for a number timestamps
already answer), and in-memory-only assistant timing (durations missing for
resumed/old sessions, inconsistent blanks).

## Decision

- Tool durations are **computed** at render time from node timestamps paired
  by `tool_call_id`. No `Session` model change; old JSONL files render
  tool durations correctly.
- Assistant (LLM call) durations are **measured**: the loop records stream
  start/end wall-time and persists `duration_ms: float | None` on
  `AssistantPayload` (default `None`). Old files validate via the default
  and render `—`.
- Every other row kind (`user`, `tool_request`, `compaction_summary`) shows
  `—` for duration. No row ever invents a number: missing data renders as
  a blank, never an estimate.
- Row assembly is a pure function (node list → trace rows) so the TUI
  overlay, tests, and any future `--trace` export share one semantic.

## Consequences

- One additive, backward-compatible schema change (`AssistantPayload`
  gains an optional field). Pydantic validation of pre-existing session
  files is unaffected.
- Tool durations inherit timestamp resolution (ISO-8601 seconds) — coarse
  for sub-second tools, but honest. If sub-second precision becomes load-
  bearing, revisit with measured tool timing instead of computed diffs.
- The trace view stays TUI-only in v1; a headless export reuses the same
  row-assembly function with no new contract to design.
