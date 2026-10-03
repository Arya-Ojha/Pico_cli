# 03 — Trace view overlay (`Ctrl+T`, `/trace`)

**What to build:** a full-screen TUI overlay (`TraceViewScreen`) rendering
rows from 02 for the active branch: snapshot on open (not live), `r`
re-snapshots, `e` toggles errors-only, typing filters (substring over the
summary, reusing the `PickerScreen` filter bar), `↑/↓` moves, `Enter`/`Esc`
dismisses. Open via `Ctrl+T` binding plus `/trace` slash command (parsed in
`commands.py`, dispatched in `app.py`, listed in `HELP_TEXT`).

**Blocked by:** 02 — Trace row assembly.

**Status:** ready-for-agent

- [ ] `Ctrl+T` opens, `Esc`/`Ctrl+T` closes; `/trace` opens the same overlay; `/help` documents both.
- [ ] Overlay shows a snapshot taken on open; `r` refreshes from the current active branch (fork/undo-safe: no nodes are mutated).
- [ ] `e` toggles errors-only (`is_error` rows); the filter bar narrows by summary text; the two compose.
- [ ] Empty sessions render a `(no nodes)` state; 80-col terminals show all six columns without wrapping.
- [ ] TUI tests drive it with `FakeProvider` sessions (open, filter, errors-only, refresh, dismiss).
