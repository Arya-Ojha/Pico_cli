"""Textual-based interactive terminal UI for pico."""

from __future__ import annotations

import argparse
import asyncio
import sys

from collections.abc import Callable
from dataclasses import dataclass

from rich.markup import escape

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

from pico_sdk import (
    AgentSession,
    AssistantPayload,
    CompactionSummaryPayload,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)
from pico_sdk.config import load_settings, save_settings
from pico_sdk.providers import FREE_MODEL_ALIAS, create_provider, resolve_free_model

from .commands import Command, Prompt, parse_line
from .history_picker import HistoryPickerScreen
from .model_picker import ModelPickerScreen
from .render import _truncate, render_event
from .status_bar import ContextStatusBar

HELP_TEXT = """\
[bold]Commands (slash or key binding):[/]
  [cyan]/help, F1[/]        show this help
  [cyan]/history, Ctrl+H[/] list session nodes — pick one to jump to
  [cyan]/compact, Ctrl+K[/] summarise older turns (optionally with steering text)
  [cyan]/model <name>[/]   change the LLM model for this session
                          (/model alone opens an interactive model picker)
  [cyan]/fork <n|id>[/]     rewind to a node and start a new branch
  [cyan]/undo, Ctrl+Z[/]    rewind to the previous user turn
  [cyan]/quit, Ctrl+Q[/]    save and exit
💭 thinking streams in full while the model thinks, then collapses to one
line — click '▸ show thinking' to expand or collapse it again.\
"""


@dataclass
class ThinkingSegment:
    """A live thinking block in the chat transcript.

    While the model is thinking, ``text`` grows chunk by chunk and is
    rendered in full (streaming). Once the segment ends it is marked
    ``final`` and collapses to a single clickable line; clicking toggles
    it back to the full text.
    """

    text: str
    id: int | None = None
    final: bool = False


def thinking_preview(full: str) -> tuple[str, bool]:
    """Return (first non-empty line, was_truncated) for a thinking block."""
    for line in full.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped, full != stripped
    return "", bool(full.strip())


def _snippet(payload: object) -> str:
    """Return a short, human-readable summary of a node payload."""
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


class _SessionManager:
    """Holds the AgentSession and provides command methods called by the app."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session

    async def stream(
        self, prompt: str, on_event: Callable[[object], None]
    ) -> None:
        """Consume the agent stream, calling on_event for each renderable.

        Text chunks are buffered so adjacent same-kind chunks merge into one
        renderable (avoiding one-chunk-per-line spam in RichLog), while the
        stream's true order is preserved — thinking that arrives after text
        stays after text.

        Thinking is streamed *live* as ThinkingSegment events: the on_event
        consumer accumulates them and collapses the segment to one clickable
        line once it ends (see PicoApp._write_thinking).
        """
        segments: list[list] = []  # ordered [kind, [chunks]] entries

        def _append(kind: str, chunk: str) -> None:
            if segments and segments[-1][0] == kind:
                segments[-1][1].append(chunk)
            else:
                segments.append([kind, [chunk]])

        def _flush() -> None:
            for kind, chunks in segments:
                if kind == "text":
                    on_event(Text("".join(chunks)))
            segments.clear()

        async for event in self.session.stream(prompt):
            if event.kind == "text":
                _append("text", event.text)
            elif event.kind == "thinking":
                _flush()
                on_event(ThinkingSegment(event.thinking))
            else:
                _flush()
                rendered = render_event(event)
                if rendered is not None:
                    on_event(rendered)
        _flush()

    def history_entries(self) -> list[dict]:
        """Return one dict per node on the active branch for the picker."""
        branch = self.session.session.active_branch()
        entries: list[dict] = []
        for i, node in enumerate(branch):
            entries.append(
                {
                    "index": i,
                    "node_id": node.id,
                    "kind": node.payload.kind,
                    "summary": _truncate(_snippet(node.payload), 80),
                    "is_current": node.id == self.session.session.active_leaf_id,
                }
            )
        return entries

    def history_text(self) -> Table | None:
        """Return a Rich Table of nodes, or None if session is empty."""
        branch = self.session.session.active_branch()
        if not branch:
            return None
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("", width=2)
        table.add_column("#", width=5, justify="right")
        table.add_column("kind", width=12)
        table.add_column("summary")
        for i, node in enumerate(branch):
            marker = "\u25b6" if node.id == self.session.session.active_leaf_id else ""
            table.add_row(
                marker,
                str(i),
                node.payload.kind,
                _truncate(_snippet(node.payload), 80),
            )
        return table

    def undo(self) -> str | None:
        """Return a message or None."""
        branch = self.session.session.active_branch()
        users = [n for n in branch if isinstance(n.payload, UserPayload)]
        if len(users) < 2:
            return "nothing to undo"
        target = users[-2]
        self.session.fork(target.id)
        return f"rewound to user turn [{branch.index(target)}]"

    async def compact(self, arg: str) -> str:
        await self.session.compact(arg)
        return "compacted context"

    def model(self, arg: str) -> str:
        if not arg:
            return f"current model: {self.session.model}"
        self.session.model = arg
        self.session.loop.model = arg
        return f"switched to model: {arg}"

    def fork(self, arg: str) -> str:
        branch = self.session.session.active_branch()
        node_id = arg
        if arg.isdigit():
            idx = int(arg)
            if not (0 <= idx < len(branch)):
                return f"error: no node at index {arg}"
            node_id = branch[idx].id
        try:
            self.session.fork(node_id)
            return f"forked to {node_id}"
        except KeyError:
            return f"error: unknown node id: {arg}"



class PicoApp(App[None]):
    """The pico interactive agent TUI."""

    CSS = """
    #chat-log {
        height: 1fr;
        border: none;
    }
    #chat-log:focus {
        border: none;
    }
    #input-bar {
        margin: 0 1;
    }
    #status-bar {
        height: 1;
        width: 100%;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+h", "show_history", "History"),
        ("ctrl+z", "undo", "Undo"),
        ("ctrl+k", "compact", "Compact"),
        ("f1", "show_help", "Help"),
    ]

    def __init__(self, mgr: _SessionManager) -> None:
        super().__init__()
        self._mgr = mgr
        self._streaming = False
        # Everything written to the chat log, in order. Thinking blocks are
        # stored as ThinkingSegment so they can collapse/expand on click.
        self._transcript: list[object] = []
        self._thinking_seq = 0
        self._thinking_expanded: set[int] = set()
        self._thinking_rerender_pending = False

    def _placeholder(self) -> str:
        return "pico>  (type a prompt, or /help for commands)"

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
        yield Input(
            id="input-bar",
            placeholder=self._placeholder(),
        )
        yield ContextStatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Focus the input bar on start and initialize status bar."""
        self.query_one("#input-bar", Input).focus()
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        """Update the status bar with current session info."""
        try:
            status_bar = self.query_one("#status-bar", ContextStatusBar)
            status_bar.update_info(
                provider=self._mgr.session.provider_name,
                model=self._mgr.session.model,
                tokens=self._mgr.session.estimate_tokens(),
                context_window=self._mgr.session.context_window,
                thinking=self._streaming,
            )
        except Exception as e:
            # If status bar update fails, don't crash the app
            # Just log it to the chat log for debugging
            try:
                self._write_chat(
                    Text(f"[Status bar error: {e}]", style="red")
                )
            except Exception:
                pass

    # -- input handling --

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle a line submitted in the input bar."""
        input_widget = self.query_one("#input-bar", Input)
        input_widget.clear()

        if self._streaming:
            self._write_chat(
                Text("(still streaming - please wait)", style="dim italic")
            )
            return

        parsed = parse_line(event.value)
        if isinstance(parsed, Command):
            await self._dispatch_command(parsed)
        elif parsed.text:  # non-empty prompt
            await self._run_prompt(parsed.text)

    # -- command dispatch --

    async def _dispatch_command(self, cmd: Command) -> None:
        if cmd.kind == "quit":
            self._mgr.session.save()
            self.exit()
        elif cmd.kind == "help":
            self._write_chat(Panel(HELP_TEXT, title="Help"))
        elif cmd.kind == "history":
            await self._show_history_picker()
        elif cmd.kind == "undo":
            msg = self._mgr.undo()
            self._write_chat(Text(msg or "(undo)", style="dim"))
        elif cmd.kind == "compact":
            msg = await self._mgr.compact(cmd.arg)
            self._write_chat(Text(msg, style="dim"))
            self._update_status_bar()
        elif cmd.kind == "model":
            if not cmd.arg:
                await self._show_model_picker()
            else:
                msg = self._mgr.model(cmd.arg)
                self._write_chat(Text(msg, style="dim"))
                self._update_status_bar()
                self._persist_model(cmd.arg)
        elif cmd.kind == "fork":
            msg = self._mgr.fork(cmd.arg)
            self._write_chat(Text(msg, style="dim"))

    # -- history picker --

    async def _show_history_picker(self) -> None:
        """Show the interactive history picker; the pick forks the session."""
        entries = self._mgr.history_entries()
        if not entries:
            self._write_chat(Text("(empty session)", style="dim"))
            return

        def _on_selected(index: int | None) -> None:
            if index is None:
                return
            msg = self._mgr.fork(str(index))
            self._write_chat(Text(msg, style="dim"))
            self._update_status_bar()

        self.push_screen(HistoryPickerScreen(entries), callback=_on_selected)

    # -- model picker --

    def _persist_model(self, model_id: str) -> None:
        """Remember the user's model choice so the next launch opens with it.

        Never aliases: we store the concrete id the user picked. Failures are
        non-fatal — model persistence is a convenience, not a requirement.
        """
        try:
            self._mgr.session.settings.model = model_id
            save_settings(self._mgr.session.settings)
        except Exception as exc:
            self._write_chat(
                Text(f"(could not save model preference: {exc})", style="dim")
            )

    async def _show_model_picker(self) -> None:
        """Fetch available models and show the interactive picker."""
        self._write_chat(Text("fetching models...", style="dim"))
        try:
            models = await self._mgr.session.provider.list_models()
        except Exception as exc:
            self._write_chat(
                Panel(f"could not fetch models: {exc}", title="error",
                      border_style="red")
            )
            return
        if not models:
            self._write_chat(Text("(no models available)", style="dim"))
            return

        def _on_selected(model_id: str | None) -> None:
            if model_id:
                msg = self._mgr.model(model_id)
                self._write_chat(Text(msg, style="dim"))
                self._update_status_bar()
                self._persist_model(model_id)

        self.push_screen(
            ModelPickerScreen(models, current=self._mgr.session.model),
            callback=_on_selected,
        )

    # -- prompt to agent streaming --

    async def _run_prompt(self, text: str) -> None:
        """Send a user prompt to the agent and stream it."""
        self._write_chat(Text(f"> {text}", style="bold"))
        self._streaming = True
        self._update_status_bar()
        asyncio.create_task(self._stream_worker(text))

    async def _stream_worker(self, prompt: str) -> None:
        """Background task that consumes the agent stream."""
        try:
            def _write(renderable: object) -> None:
                if isinstance(renderable, ThinkingSegment):
                    self._write_thinking(renderable)
                else:
                    self._write_chat(renderable)

            await self._mgr.stream(prompt, _write)
        except Exception as exc:
            self._write_chat(
                Panel(str(exc), title="error", border_style="red")
            )
        finally:
            self._finalize_thinking()
            self._streaming = False
            self._update_status_bar()

    # -- helpers --

    def _write_chat(self, renderable: object) -> None:
        """Append a renderable to the transcript and the chat log.

        Any open (still-streaming) thinking segment is finalized first so
        the log order matches the transcript order.
        """
        self._finalize_thinking()
        self._transcript.append(renderable)
        self.query_one("#chat-log", RichLog).write(renderable)

    def _write_thinking(self, segment: ThinkingSegment) -> None:
        """Grow the open thinking segment with a streamed chunk.

        The full text is shown live while the segment streams; the caller
        finalizes it later (collapsing it to one clickable line). Rather
        than writing each chunk (RichLog starts a new line per write,
        which would print one word per line), the accumulated text is
        re-rendered from the transcript, coalesced to one redraw per
        event-loop tick.
        """
        last = self._transcript[-1] if self._transcript else None
        if isinstance(last, ThinkingSegment) and not last.final:
            last.text += segment.text
        else:
            self._thinking_seq += 1
            segment.id = self._thinking_seq
            self._transcript.append(segment)
        self._schedule_thinking_rerender()

    def _schedule_thinking_rerender(self) -> None:
        """Coalesce thinking redraws to at most one per event-loop tick."""
        if self._thinking_rerender_pending:
            return
        self._thinking_rerender_pending = True
        self.call_later(self._flush_thinking_rerender)

    def _flush_thinking_rerender(self) -> None:
        self._thinking_rerender_pending = False
        self._rerender_chat()

    def _finalize_thinking(self) -> None:
        """Collapse an open thinking segment to a single clickable line."""
        last = self._transcript[-1] if self._transcript else None
        if isinstance(last, ThinkingSegment) and not last.final:
            last.final = True
            self._rerender_chat()

    def _thinking_renderable(self, segment: ThinkingSegment) -> object:
        """Live (full text), collapsed one-line, or expanded text block."""
        if not segment.final:
            # Still streaming: show the full accumulated text.
            return Text(segment.text, style="dim italic")
        if segment.id is not None and segment.id in self._thinking_expanded:
            return Text(segment.text, style="dim italic")
        preview, truncated = thinking_preview(segment.text)
        ellipsis = " …" if truncated else ""
        return (
            f"[dim italic]💭 thinking: {escape(preview)}{ellipsis} "
            f"[@click=app.toggle_thinking({segment.id})]▸ show thinking[/][/]"
        )

    def _rerender_chat(self) -> None:
        """Redraw the whole chat log from the transcript (thinking blocks
        rendered collapsed or expanded per current toggle state)."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.clear()
        for item in self._transcript:
            if isinstance(item, ThinkingSegment):
                chat_log.write(self._thinking_renderable(item))
            else:
                chat_log.write(item)

    async def action_toggle_thinking(self, block_id: int) -> None:
        """Expand or collapse a thinking block when its line is clicked."""
        if block_id in self._thinking_expanded:
            self._thinking_expanded.discard(block_id)
        else:
            self._thinking_expanded.add(block_id)
        self._rerender_chat()

    # -- key binding actions --

    async def action_quit_app(self) -> None:
        self._mgr.session.save()
        self.exit()

    async def action_show_history(self) -> None:
        await self._dispatch_command(Command("history"))

    async def action_undo(self) -> None:
        await self._dispatch_command(Command("undo"))

    async def action_compact(self) -> None:
        await self._dispatch_command(Command("compact", ""))

    async def action_show_help(self) -> None:
        await self._dispatch_command(Command("help"))


# -- CLI entry point --


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="picoCLI-chat", description="Interactive pico session (Textual TUI)."
    )
    parser.add_argument(
        "--no-bash",
        action="store_true",
        help="Disable unsandboxed bash (on by default).",
    )
    parser.add_argument(
        "--model", default=None, help="Override the configured model."
    )
    parser.add_argument(
        "--cwd", default=None, help="Working directory (default: current)."
    )
    parser.add_argument(
        "--session", default=None, help="Resume an existing session by id."
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    model = args.model or settings.model
    provider = create_provider(settings)
    if model == FREE_MODEL_ALIAS:
        # Resolve the alias to a concrete free model available right now;
        # fall back to the alias itself (surfaced as an API error later)
        # if the lookup fails.
        resolved = asyncio.run(resolve_free_model(provider))
        if resolved:
            model = resolved
            print(f"using free model: {model}")
    if args.session:
        session = AgentSession.load(
            args.session,
            provider=provider,
            model=model,
            settings=settings,
            working_dir=args.cwd,
            allow_bash=not args.no_bash,
        )
    else:
        session = AgentSession(
            provider=provider,
            model=model,
            settings=settings,
            working_dir=args.cwd,
            allow_bash=not args.no_bash,
        )
    mgr = _SessionManager(session)
    app = PicoApp(mgr)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
