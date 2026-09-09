"""Slash-command parser (pure, no Textual/Rich dependency)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Command:
    """A slash command parsed from a line of input."""

    kind: str  # "compact" | "fork" | "help" | "history" | "model" | "undo" | "quit"
    arg: str = ""


@dataclass
class Prompt:
    """A plain prompt to send to the agent."""

    text: str


def parse_line(line: str) -> Command | Prompt:
    """Parse a line of input into a command or a prompt."""
    line = line.strip()
    if not line:
        return Prompt("")  # empty → no-op, caller should skip
    if line in ("/quit", "/exit", "/q"):
        return Command("quit")
    if line in ("/help", "/h", "/?"):
        return Command("help")
    if line in ("/history", "/nodes"):
        return Command("history")
    if line in ("/undo", "/back"):
        return Command("undo")
    if line.startswith("/compact"):
        return Command("compact", line[len("/compact") :].strip())
    if line.startswith("/model"):
        return Command("model", line[len("/model") :].strip())
    if line.startswith("/fork"):
        return Command("fork", line[len("/fork") :].strip())
    return Prompt(line)
