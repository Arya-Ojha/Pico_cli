"""The headless library API: ``AgentSession``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pico_core.fsm import AgentLoop, LoopEvent, RunResult
from pico_core.session import Session
from pico_core.tools import BashTool, EditTool, ReadTool, ToolRegistry, WriteTool

from .config import Settings, load_settings
from .extensions import ExtensionManager

DEFAULT_SYSTEM_PROMPT = (
    "You are pico, a coding agent. You can read, write, and edit files, and run "
    "bash commands. Work autonomously to complete the user's task, then report "
    "what you did."
)


class AgentSession:
    """A headless agent session: provider + tools + session tree + extensions."""

    def __init__(
        self,
        *,
        provider: Any,
        model: str | None = None,
        settings: Settings | None = None,
        working_dir: str | Path | None = None,
        allow_bash: bool = True,
        session_id: str | None = None,
        session: Session | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.settings = settings or load_settings()
        self.model = model or self.settings.model
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.extensions = ExtensionManager()

        self.system_prompt = system_prompt

        self.session = session or (Session(id=session_id) if session_id else Session())
        self.tools = ToolRegistry()
        self._register_core_tools(allow_bash)

        self.loop = AgentLoop(
            provider=provider,
            session=self.session,
            tools=self.tools,
            system_prompt=self.system_prompt,
            model=self.model,
            context_window=self.settings.context_window,
            reserve_tokens=self.settings.reserve_tokens,
            hooks=self.extensions,
        )

    @property
    def provider(self) -> Any:
        return self.loop.provider

    @property
    def provider_name(self) -> str:
        """Return a human-readable provider name."""
        provider_class = type(self.loop.provider).__name__
        # Convert CamelCase to readable name
        if "OpenRouter" in provider_class:
            return "OpenRouter"
        return provider_class.replace("Provider", "")

    @property
    def context_window(self) -> int:
        """Return the context window size."""
        return self.loop.context_window

    def estimate_tokens(self) -> int:
        """Return the current estimated token count."""
        return self.loop.estimate_tokens()

    # -- core tools ---------------------------------------------------------

    def _register_core_tools(self, allow_bash: bool) -> None:
        for tool in (
            ReadTool(self.working_dir),
            WriteTool(self.working_dir),
            EditTool(self.working_dir),
            BashTool(self.working_dir, enabled=allow_bash),
        ):
            self.tools.register(tool)

    # -- running ------------------------------------------------------------

    async def run(self, prompt: str) -> RunResult:
        return await self.loop.run(prompt)

    def stream(self, prompt: str) -> AsyncIterator[LoopEvent]:
        return self.loop.stream(prompt)

    def fork(self, node_id: str) -> None:
        self.session.fork(node_id)

    async def compact(self, instructions: str = "") -> None:
        await self.loop.compact(instructions)

    # -- extension binding --------------------------------------------------

    def register_tool(self, tool: Any) -> None:
        self.tools.register(tool)

    def register_provider(self, name: str, provider: Any) -> None:
        self.extensions.register_provider(name, provider)

    def use_provider(self, name: str) -> None:
        provider = self.extensions.get_provider(name)
        if provider is None:
            raise KeyError(f"unknown provider: {name}")
        self.loop.provider = provider

    def on(self, event: str, callback: Any) -> None:
        self.extensions.on(event, callback)

    def load_plugins(self, directory: str | Path) -> None:
        self.extensions.load_plugins(directory, self)

    # -- persistence --------------------------------------------------------

    def session_path(self) -> Path:
        directory = Path(self.settings.session_dir).expanduser()
        return directory / f"{self.session.id}.jsonl"

    def save(self) -> Path:
        path = self.session_path()
        self.session.save(path)
        return path

    @classmethod
    def load(
        cls,
        session_id: str,
        *,
        provider: Any,
        model: str | None = None,
        settings: Settings | None = None,
        working_dir: str | Path | None = None,
        allow_bash: bool = True,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> "AgentSession":
        """Resume an existing session persisted under ``session_dir``."""
        settings = settings or load_settings()
        directory = Path(settings.session_dir).expanduser()
        session = Session.load(directory / f"{session_id}.jsonl")
        return cls(
            provider=provider,
            model=model,
            settings=settings,
            working_dir=working_dir,
            allow_bash=allow_bash,
            session=session,
            system_prompt=system_prompt,
        )
