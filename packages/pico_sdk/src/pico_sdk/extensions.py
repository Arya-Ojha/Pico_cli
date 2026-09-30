"""Curated extension hooks (Claude Code-style).

The core (tools, loop, provider, session) is hardcoded and non-replaceable
(see ADR-0003). Extensions are guests: they observe lifecycle events through
a small fixed hook vocabulary. Hooks cannot mutate arguments/results or veto
execution.

Fixed vocabulary:
- ``session_start`` — fires once per ``AgentSession.stream`` run.
- ``pre_tool_use`` — fires before a tool runs (observe-only).
- ``post_tool_use`` — fires after a tool succeeds.
- ``post_tool_failure`` — fires after a tool returns an error result or raises.

Legacy aliases (``on_session_start``, ``tool.before.*``, ``tool.after.*``)
are accepted and mapped onto the fixed vocabulary.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Callable

from pico_core.session import Session, ToolResultPayload

Hook = Callable[..., Any]

#: The only hook names ``on()`` accepts (after alias normalisation).
ALLOWED_HOOKS = frozenset(
    {
        "session_start",
        "pre_tool_use",
        "post_tool_use",
        "post_tool_failure",
    }
)

_LEGACY_ALIASES = {
    "on_session_start": "session_start",
}


def _normalize_event(event: str) -> str:
    """Map legacy hook names onto the fixed vocabulary."""
    if event in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[event]
    if event.startswith("tool.before."):
        return "pre_tool_use"
    if event.startswith("tool.after."):
        return "post_tool_use"
    return event


class ExtensionManager:
    """Fixed-vocabulary, observe-only hook surface.

    Implements the loop's ``HookSink``. There is no provider registry and no
    plugin-directory loader: the provider is built by
    ``pico_sdk.providers.create_provider`` and tools are wired directly in
    ``AgentSession._register_core_tools``.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = defaultdict(list)

    # -- hooks --------------------------------------------------------------

    def on(self, event: str, callback: Hook) -> Callable[[], None]:
        """Subscribe ``callback`` to a fixed hook. Returns an unsubscribe fn."""
        name = _normalize_event(event)
        if name not in ALLOWED_HOOKS:
            raise ValueError(
                f"unknown hook: {event!r}; allowed: {sorted(ALLOWED_HOOKS)}"
            )
        self._hooks[name].append(callback)

        def _off() -> None:
            try:
                self._hooks[name].remove(callback)
            except ValueError:
                pass

        return _off

    async def _fire(self, event: str, **kwargs: Any) -> None:
        for callback in list(self._hooks.get(event, [])):
            result = callback(**kwargs)
            if inspect.isawaitable(result):
                await result

    # HookSink implementation ----------------------------------------------

    async def on_session_start(self, session: Session) -> None:
        await self._fire("session_start", session=session)

    async def tool_before(self, name: str, arguments: dict) -> None:
        await self._fire("pre_tool_use", name=name, arguments=arguments)

    async def tool_after(
        self, name: str, arguments: dict, result: ToolResultPayload
    ) -> None:
        await self._fire(
            "post_tool_use", name=name, arguments=arguments, result=result
        )
        if result.is_error:
            await self._fire(
                "post_tool_failure", name=name, arguments=arguments, result=result
            )
