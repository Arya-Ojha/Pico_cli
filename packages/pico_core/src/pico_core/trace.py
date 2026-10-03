"""Trace rows for the trace view (ADR-0006).

Pure function: session nodes -> display rows. No Textual/Rich dependency,
so the TUI overlay and any future headless export share one semantic.

Missing data renders as blanks (``None``), never estimates: only assistant
nodes carry token usage, only assistant nodes carry a measured duration,
and only tool results paired with a request carry a computed duration.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel

from .session import (
    AssistantPayload,
    CompactionSummaryPayload,
    Node,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)

#: Summary width, matching the history picker's 80-char rows.
SUMMARY_LIMIT = 80

#: Same rule as the TUI's bash collapse: trailing ``[exit code: N]``.
_BASH_EXIT = re.compile(r"\[exit code: (-?\d+)\]\s*$")


class TraceRow(BaseModel):
    """One display row in the trace view, derived from a single node."""

    node_id: str
    time: str  # HH:MM:SS of the node timestamp (raw fallback when unparsable)
    kind: str
    summary: str  # one line, truncated to SUMMARY_LIMIT
    status: str  # "ok" | "error", bash rows add "(exit N)"
    tokens: int | None  # assistant total; None elsewhere
    duration_ms: float | None  # measured (assistant) or computed (paired tool result)


def _short_time(timestamp: str) -> str:
    """Format an ISO-8601 timestamp as HH:MM:SS; never raises."""
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return timestamp[:8] if timestamp else "--:--:--"


def _truncate(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Collapse whitespace and cut to one line (mirrors the TUI renderer)."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _summary(payload: object) -> str:
    """One-line human-readable summary per payload kind."""
    if isinstance(payload, UserPayload):
        return payload.content
    if isinstance(payload, AssistantPayload):
        return payload.text
    if isinstance(payload, ToolRequestPayload):
        return f"{payload.tool_call.name} {payload.tool_call.arguments}"
    if isinstance(payload, ToolResultPayload):
        return payload.content
    if isinstance(payload, CompactionSummaryPayload):
        return payload.summary
    return ""


def _status(payload: object) -> str:
    """Derived status: ok/error from is_error, plus bash exit code."""
    if isinstance(payload, ToolResultPayload):
        base = "error" if payload.is_error else "ok"
        if payload.name == "bash":
            match = _BASH_EXIT.search(payload.content)
            if match is not None:
                return f"{base} (exit {match.group(1)})"
        return base
    return "ok"


def _tool_duration_ms(request_ts: str | None, result_ts: str) -> float | None:
    """Computed tool span: result.timestamp - request.timestamp in ms."""
    if request_ts is None:
        return None
    try:
        delta = datetime.fromisoformat(result_ts) - datetime.fromisoformat(request_ts)
    except ValueError:
        return None
    return max(delta.total_seconds() * 1000.0, 0.0)


def assemble_trace_rows(branch: list[Node]) -> list[TraceRow]:
    """Map one branch (root-to-leaf nodes) to one trace row per node."""
    request_times: dict[str, str] = {}
    for node in branch:
        if isinstance(node.payload, ToolRequestPayload):
            request_times.setdefault(node.payload.tool_call.id, node.timestamp)
    rows: list[TraceRow] = []
    for node in branch:
        payload = node.payload
        tokens: int | None = None
        duration_ms: float | None = None
        if isinstance(payload, AssistantPayload):
            if payload.usage is not None:
                tokens = payload.usage.total_tokens
            duration_ms = payload.duration_ms
        elif isinstance(payload, ToolResultPayload):
            duration_ms = _tool_duration_ms(
                request_times.get(payload.tool_call_id), node.timestamp
            )
        rows.append(
            TraceRow(
                node_id=node.id,
                time=_short_time(node.timestamp),
                kind=payload.kind,
                summary=_truncate(_summary(payload)),
                status=_status(payload),
                tokens=tokens,
                duration_ms=duration_ms,
            )
        )
    return rows
