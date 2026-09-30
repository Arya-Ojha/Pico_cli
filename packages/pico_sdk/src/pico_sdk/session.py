"""The headless library API: ``AgentSession`` (hardcoded core, ADR-0003)."""

from __future__ import annotations

import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pico_core.fsm import AgentLoop, LoopEvent, RunResult
from pico_core.session import Session
from pico_core.todos import TodoList, TodoTool
from pico_core.tools import BashTool, EditTool, FetchTool, GrepTool, ReadTool, ToolRegistry, WebSearchTool, WriteTool

from .config import Settings, load_settings
from .extensions import ExtensionManager
from .skills import Skill, discover_skills, merge_skills, render_skills_prompt

DEFAULT_SYSTEM_PROMPT = (
    "You are pico, a coding agent. You can read, write, and edit files, search "
    "file contents with grep, fetch URLs, search the web, and run "
    "bash commands. Work autonomously to complete the user's task, then report "
    "what you did. For multi-step tasks, track progress with the todo tool "
    "(add one todo per step, mark each in_progress while you work on it and "
    "completed when done). The run only ends once every todo is completed — "
    "do not stop early with unfinished todos."
)


class AgentSession:
    """A headless agent session: hardcoded provider + tools + session tree.

    The core is non-replaceable (ADR-0003). Curated extensions only: fixed
    lifecycle hooks via :meth:`on` and model-invoked ``SKILL.md`` skills.
    """

    #: Core tool names in registration order (permission-gating vocabulary).
    CORE_TOOLS = (
        "read",
        "write",
        "edit",
        "grep",
        "fetch",
        "websearch",
        "bash",
        "todo",
    )

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
        load_skills: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        self.model = model or self.settings.model
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.extensions = ExtensionManager()
        self.provider_id: str = (
            getattr(provider, "provider_id", None)
            or self.settings.provider
            or "openrouter"
        )

        if self.settings.allowed_tools is not None:
            unknown = set(self.settings.allowed_tools) - set(self.CORE_TOOLS)
            if unknown:
                warnings.warn(
                    f"unknown tools in allowed_tools (ignored): "
                    f"{sorted(unknown)}; valid: {list(self.CORE_TOOLS)}",
                    stacklevel=2,
                )

        self.system_prompt = system_prompt
        self.skills: list[Skill] = []
        if load_skills:
            # Global skills plus project-local override
            # (<cwd>/.pico/skills wins on name conflicts), merged and capped.
            self.skills = merge_skills(
                [
                    discover_skills([self.settings.skills_dir]),
                    discover_skills([self.working_dir / ".pico" / "skills"]),
                ]
            )
            skills_section = render_skills_prompt(self.skills)
            if skills_section:
                self.system_prompt = f"{system_prompt}\n\n{skills_section}"

        self.session = session or (Session(id=session_id) if session_id else Session())
        self.tools = ToolRegistry()
        self.todos = TodoList()
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
            allowed_tools=self.settings.allowed_tools,
        )

    @property
    def provider(self) -> Any:
        return self.loop.provider

    @property
    def provider_name(self) -> str:
        """Return a human-readable provider name."""
        display = getattr(self.loop.provider, "display_name", None)
        if display:
            return str(display)
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

    # -- core tools (hardcoded, non-replaceable) ------------------------------

    def _register_core_tools(self, allow_bash: bool) -> None:
        # Precedence: the loop-level ``allowed_tools`` gate (when set) rejects
        # before any tool runs, so it wins over this per-tool flag. When
        # ``allowed_tools`` is None, ``allow_bash`` decides for bash.
        for tool in (
            ReadTool(self.working_dir),
            WriteTool(self.working_dir),
            EditTool(self.working_dir),
            GrepTool(self.working_dir),
            FetchTool(),
            WebSearchTool(),
            BashTool(self.working_dir, enabled=allow_bash),
            TodoTool(self.todos),
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

    # -- curated extensions -------------------------------------------------
    # Hooks are observe-only; see ``pico_sdk.extensions.ALLOWED_HOOKS``.

    def on(self, event: str, callback: Any) -> Any:
        return self.extensions.on(event, callback)

    # -- providers ----------------------------------------------------------

    def set_provider(
        self, provider_id: str, values: dict[str, str] | None = None
    ) -> Any:
        """Switch the active provider, storing ``values`` into settings.

        Only the given ``values`` are stored (merged over existing stored
        config); effective config still falls back to env vars and field
        defaults. The model resets to the stored/default model for the new
        provider — model ids are provider-specific — and is written back to
        settings so the next launch reopens on this provider + model.
        Returns the adapter.
        """
        from pico_ai.providers import get_spec

        from .providers import effective_config

        spec = get_spec(provider_id)  # KeyError on unknown id
        stored = dict(self.settings.providers.get(provider_id, {}))
        if values:
            stored.update(values)
        self.settings.providers[provider_id] = stored
        self.settings.provider = provider_id
        self.provider_id = provider_id
        config = effective_config(provider_id, self.settings)
        self.loop.provider = spec.create(config)
        self.model = config.get("model") or spec.default_model
        self.loop.model = self.model
        self.settings.model = self.model
        return self.loop.provider

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
        load_skills: bool = True,
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
            load_skills=load_skills,
        )
