"""pico_sdk: the headless library API and curated hooks/skills."""

from pico_core.fsm import AgentState, LoopEvent, RunResult
from pico_core.session import (
    AssistantPayload,
    CompactionSummaryPayload,
    Node,
    Session,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)

from .config import Settings, load_settings
from .extensions import ALLOWED_HOOKS, ExtensionManager
from .session import DEFAULT_SYSTEM_PROMPT, AgentSession
from .skills import Skill, discover_skills, merge_skills, parse_skill_file, render_skills_prompt

__all__ = [
    "AgentSession",
    "ExtensionManager",
    "ALLOWED_HOOKS",
    "Settings",
    "load_settings",
    "Skill",
    "discover_skills",
    "merge_skills",
    "parse_skill_file",
    "render_skills_prompt",
    "DEFAULT_SYSTEM_PROMPT",
    "AgentState",
    "LoopEvent",
    "RunResult",
    "AssistantPayload",
    "CompactionSummaryPayload",
    "Node",
    "Session",
    "ToolRequestPayload",
    "ToolResultPayload",
    "UserPayload",
]


