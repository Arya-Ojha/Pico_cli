"""Tests for stream ordering, thinking labels, and the free-model alias."""

import pytest

from pico_ai.types import AICallRequest, StreamEvent
from pico_core.fsm import LoopEvent
from pico_sdk.config import Settings
from pico_sdk.providers import FREE_MODEL_ALIAS, resolve_free_model
from pico_sdk.session import AgentSession
from pico_tui.app import _SessionManager, ThinkingSegment


class ScriptedProvider:
    """Yields one scripted list of StreamEvents per stream() call."""

    def __init__(self, turns):
        self._turns = [list(t) for t in turns]

    async def stream(self, request: AICallRequest):
        for event in self._turns.pop(0):
            yield event


def _to_loop_events(events):
    for e in events:
        if e.kind == "text":
            yield LoopEvent(kind="text", text=e.text)
        elif e.kind == "thinking":
            yield LoopEvent(kind="thinking", thinking=e.thinking)
        else:
            yield LoopEvent(kind="usage")


@pytest.mark.asyncio
async def test_stream_preserves_wire_order(tmp_path):
    """text → thinking → text must render in that exact order."""

    class LoopAdapter:
        """Bridges the ScriptedProvider into AgentSession's stream seam."""

        def __init__(self, provider):
            self.provider = provider
            self.session = None
            self.system_prompt = ""
            self.tools = None

        # AgentSession.stream delegates to loop.stream; we bypass the FSM and
        # call the provider directly through a minimal shim.
        def stream(self, prompt):
            async def _gen():
                request = AICallRequest(model="m", messages=[])
                async for ev in self.provider.stream(request):
                    for le in _to_loop_events([ev]):
                        yield le
            return _gen()

    provider = ScriptedProvider([
        [
            StreamEvent(kind="text", text="answer!"),
            StreamEvent(kind="thinking", text="", thinking="hmm"),
            StreamEvent(kind="text", text=" more"),
        ]
    ])
    shim = LoopAdapter(provider)
    session = AgentSession(
        provider=provider,
        settings=Settings(session_dir=str(tmp_path)),
        working_dir=tmp_path,
        allow_bash=False,
    )
    mgr = _SessionManager(session)

    captured: list = []
    # Patch the session's stream to use our shim so we control wire order.
    session.stream = shim.stream  # type: ignore[method-assign]
    await mgr.stream("hi", captured.append)

    assert len(captured) == 3
    first, second, third = captured
    # Wire order preserved: answer, then thinking, then more answer text.
    assert first.plain == "answer!"
    assert isinstance(second, ThinkingSegment)
    assert second.text == "hmm"
    assert not second.final  # still streaming; app collapses it later
    assert third.plain == " more"


@pytest.mark.asyncio
async def test_resolve_free_model_prefers_free_with_tools():
    class Provider:
        async def list_models(self):
            return [
                {"id": "a/free-notools", "name": "A Free NoTools",
                 "is_free": True, "supports_tools": False},
                {"id": "z/free-tools", "name": "Z Free Tools",
                 "is_free": True, "supports_tools": True},
                {"id": "paid/x", "name": "Paid", "is_free": False,
                 "supports_tools": True},
            ]

    assert await resolve_free_model(Provider()) == "z/free-tools"


@pytest.mark.asyncio
async def test_resolve_free_model_falls_back_to_any_free():
    class Provider:
        async def list_models(self):
            return [
                {"id": "b/free", "name": "B Free", "is_free": True,
                 "supports_tools": False},
                {"id": "paid/x", "name": "Paid", "is_free": False,
                 "supports_tools": True},
            ]

    assert await resolve_free_model(Provider()) == "b/free"


@pytest.mark.asyncio
async def test_resolve_free_model_returns_none_on_error():
    class Provider:
        async def list_models(self):
            raise RuntimeError("network down")

    assert await resolve_free_model(Provider()) is None


def test_default_model_is_free_alias():
    settings = Settings()
    assert settings.model == FREE_MODEL_ALIAS
