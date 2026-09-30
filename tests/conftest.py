"""Shared test fixtures: the scripted fake provider at the provider seam."""

from __future__ import annotations

from pico_ai.types import AICallRequest, StreamEvent
from pico_sdk.config import Settings
from pico_sdk.session import AgentSession


class FakeProvider:
    """A scripted provider that yields predetermined turns, one per stream() call."""

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        self._turns = [list(t) for t in turns]
        self.calls: list[AICallRequest] = []

    async def stream(self, request: AICallRequest):
        self.calls.append(request)
        if self._turns:
            for event in self._turns.pop(0):
                yield event


def make_session(provider, tmp_path, **kwargs) -> AgentSession:
    """Build an AgentSession whose sessions are persisted under ``tmp_path``."""
    kwargs.setdefault("load_skills", False)
    kwargs.setdefault("settings", Settings(session_dir=str(tmp_path)))
    return AgentSession(
        provider=provider,
        working_dir=tmp_path,
        **kwargs,
    )

