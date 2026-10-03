"""Full-screen trace view overlay (ADR-0006, ticket 03).

One row per session node on the active branch (snapshot on open — not
live). Typing filters by summary text; ``r`` re-snapshots, ``e`` toggles
errors-only, ``Enter``/``Esc`` dismisses.

``r``/``e`` are screen bindings, so they fire while the row list (not the
filter bar) is focused — printable keys typed in the filter bar always go
to the filter (see ``modal.PickerScreen``). ``Tab`` moves between the two.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Input, Label, OptionList

from pico_sdk import TraceRow

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css

#: Fixed column widths (measured on plain text, so markup never shifts
#: alignment). Layout: TIME KIND STATUS TOKENS DURATION SUMMARY, separated
#: by two spaces; the summary (last) absorbs all truncation.
TIME_W = 8
KIND_W = 12
STATUS_W = 14
TOKENS_W = 8
DURATION_W = 7
_SEP = "  "
#: Plain-text length of the fixed columns + separators (summary starts here).
PREFIX_LEN = TIME_W + KIND_W + STATUS_W + TOKENS_W + DURATION_W + len(_SEP) * 5


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


def _tokens_text(tokens: int | None) -> str:
    """Right-aligned token cell (blank when unknown)."""
    text = f"{tokens:,}" if tokens is not None else "—"
    return f"{fit_text(text, TOKENS_W):>{TOKENS_W}}"


def _duration_text(duration_ms: float | None) -> str:
    """Right-aligned duration cell (blank when unknown)."""
    if duration_ms is None:
        text = "—"
    elif duration_ms >= 1000:
        text = f"{duration_ms / 1000:.1f}s"
    else:
        text = f"{duration_ms:.0f}ms"
    return f"{fit_text(text, DURATION_W):>{DURATION_W}}"


def _row_prefix(
    time_text: str, kind_text: str, status_text: str, tokens_text: str, duration_text: str
) -> str:
    """Assemble the fixed-width columns (length is always ``PREFIX_LEN``)."""
    return (
        f"{fit_text(time_text, TIME_W):<{TIME_W}}{_SEP}"
        f"{fit_text(kind_text, KIND_W):<{KIND_W}}{_SEP}"
        f"{fit_text(status_text, STATUS_W):<{STATUS_W}}{_SEP}"
        f"{tokens_text}{_SEP}"
        f"{duration_text}{_SEP}"
    )


def format_trace_header() -> str:
    """The header line, aligned with :func:`format_trace_option` rows."""
    return (
        _row_prefix("TIME", "KIND", "STATUS", f"{'TOKENS':>{TOKENS_W}}", f"{'DUR':>{DURATION_W}}")
        + "SUMMARY"
    )


def format_trace_option(row: TraceRow) -> str:
    """Render one fixed-width row; the summary (last) absorbs truncation.

    Color markup wraps already-padded cells, so it never shifts alignment;
    the summary is markup-escaped (tool output contains ``[...]``).
    """
    status_plain = f"{fit_text(row.status, STATUS_W):<{STATUS_W}}"
    status = (
        f"[red]{status_plain}[/]"
        if row.status.startswith("error")
        else f"[green]{status_plain}[/]"
    )
    prefix = _row_prefix(
        row.time, row.kind, row.status, _tokens_text(row.tokens),
        _duration_text(row.duration_ms),
    )
    summary = fit_text(row.summary, max(MAX_ROW_WIDTH - len(prefix), 8))
    kind = f"{fit_text(row.kind, KIND_W):<{KIND_W}}"
    tokens = _tokens_text(row.tokens)
    duration = _duration_text(row.duration_ms)
    return (
        f"{fit_text(row.time, TIME_W):<{TIME_W}}{_SEP}"
        f"[dim]{kind}[/]{_SEP}"
        f"{status}{_SEP}"
        f"{tokens}{_SEP}"
        f"{duration}{_SEP}"
        f"{escape(summary)}"
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

    def compose(self) -> ComposeResult:
        """Base picker layout plus a fixed column header above the rows."""
        with Vertical(id=self.dialog_id):
            yield Input(placeholder=self.search_placeholder, id=self.search_id)
            yield Label(
                Text(format_trace_header(), style="bold", no_wrap=True),
                id="trace-header",
            )
            option_list = OptionList(id=self.list_id)
            for row in self.options():
                option_list.add_option(row)
            option_list.highlighted = self.initial_highlight
            yield option_list
        yield Footer()

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
