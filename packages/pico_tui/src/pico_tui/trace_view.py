"""Full-screen trace view overlay (ADR-0006, ticket 03).

One row per session node on the active branch (snapshot on open — not
live). Typing filters by summary text (``is:error`` shows only error rows);
``r`` re-snapshots, ``e`` toggles errors-only, ``Enter``/``Esc`` dismisses.

``r``/``e`` are screen bindings, so they fire while the row list (not the
filter bar) is focused — printable keys typed in the filter bar always go
to the filter (see ``modal.PickerScreen``). ``Tab`` moves between the two.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.markup import escape
from textual.binding import Binding
from textual.widgets import Input

from pico_sdk import TraceRow

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css


def trace_entry(row: TraceRow) -> dict:
    """Flatten a trace row to the dict shape the picker machinery indexes."""
    return {
        "node_id": row.node_id,
        "time": row.time,
        "kind": row.kind,
        "summary": row.summary,
        "status": row.status,
        "tokens": row.tokens,
        "duration_ms": row.duration_ms,
        "is_error": row.status.startswith("error"),
    }


def format_trace_option(row: TraceRow) -> str:
    """Render one fixed-width row; the summary (last) absorbs truncation."""
    tokens = f"{row.tokens:,}" if row.tokens is not None else "—"
    if row.duration_ms is None:
        duration = "—"
    elif row.duration_ms >= 1000:
        duration = f"{row.duration_ms / 1000:.1f}s"
    else:
        duration = f"{row.duration_ms:.0f}ms"
    status = (
        f"[red]{row.status}[/]"
        if row.status.startswith("error")
        else f"[green]{row.status}[/]"
    )
    prefix = f"{row.time}  {row.kind:<12}  {row.status}  {tokens}  {duration}  "
    summary = fit_text(row.summary, max(MAX_ROW_WIDTH - len(prefix), 8))
    return (
        f"{row.time}  [dim]{row.kind:<12}[/]  {status}  {tokens}  "
        f"{duration}  {escape(summary)}"
    )


class TraceViewScreen(PickerScreen[None]):
    """Snapshot table of trace rows; dismisses with ``None`` (view-only)."""

    CSS = picker_css(
        "TraceViewScreen",
        "trace-dialog",
        "trace-list",
        "trace-search",
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "errors_only", "Errors only"),
    ]

    dialog_id = "trace-dialog"
    list_id = "trace-list"
    search_id = "trace-search"
    search_placeholder = "Type to filter... (Tab to list, e = errors only)"

    def __init__(
        self,
        rows: list[TraceRow],
        on_refresh: Callable[[], list[TraceRow]] | None = None,
    ) -> None:
        super().__init__([trace_entry(row) for row in rows])
        self._rows = list(rows)
        self._errors_only = False
        self._on_refresh = on_refresh

    def options(self) -> list[str]:
        """One row per node, or ``(no nodes)`` when empty.

        Errors-only is baked in here (rather than in an
        ``on_input_changed`` override) because Textual dispatches message
        handlers along the MRO — a subclass override would run *in
        addition to* the base filter and the two would clobber each
        other. The base substring filter then composes on top of this.
        """
        visible = [
            row
            for row in self._rows
            if not self._errors_only or row.status.startswith("error")
        ]
        if not visible and not self._rows:
            return ["(no nodes)"]
        return [format_trace_option(row) for row in visible]

    def result_for(self, index: int) -> None:
        return None

    def _refilter(self) -> None:
        """Re-run the base substring filter over the current rows/flag."""
        search = self.query_one("#" + self.search_id, Input)
        # Mirror PickerScreen.on_input_changed (which reads options()).
        query = search.value.strip().lower()
        rows = self.options()
        self._visible = [
            i
            for i, row in enumerate(rows)
            if query in self._plain(row).lower()
        ]
        option_list = self._option_list()
        option_list.clear_options()
        if not rows and self._rows:
            option_list.add_option("(no matching rows)")
        elif not rows:
            option_list.add_option("(no nodes)")
        else:
            for i in self._visible:
                option_list.add_option(rows[i])
        if self._visible:
            option_list.highlighted = 0

    def action_refresh(self) -> None:
        """Re-snapshot rows from the provider (fork/undo-safe: read-only)."""
        if self._on_refresh is None:
            return
        self._rows = list(self._on_refresh())
        self._entries = [trace_entry(row) for row in self._rows]
        self._refilter()

    def action_errors_only(self) -> None:
        """Toggle the errors-only filter."""
        self._errors_only = not self._errors_only
        self._refilter()
