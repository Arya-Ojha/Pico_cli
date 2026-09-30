"""Curated skills: model-invoked ``SKILL.md`` markdown files (Claude Code-style).

A skill is a directory containing a ``SKILL.md`` file with optional YAML
frontmatter (``name`` / ``description``) followed by markdown instructions::

    ---
    name: commit-helper
    description: Use when the user wants to commit code.
    ---
    # Commit helper
    Run ``git status`` first ...

Skills are discovered from ``~/.pico/skills/*/SKILL.md`` plus an optional
project-local directory, and their descriptions are inlined into the system
prompt so the model knows when to apply them. Skills carry knowledge only —
they execute no code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    content: str
    path: Path


def parse_skill_file(path: Path) -> Skill:
    """Parse a single ``SKILL.md`` file (frontmatter optional)."""
    text = path.read_text(encoding="utf-8")
    name = path.parent.name
    description = ""
    content = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            front = text[3:end].strip()
            content = text[end + 3 :].strip()
            for line in front.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip().strip("\"'")
                if key == "name" and value:
                    name = value
                elif key == "description" and value:
                    description = value
    return Skill(name=name, description=description, content=content, path=path)


def discover_skills(directories: list[Path | str]) -> list[Skill]:
    """Collect skills from ``<dir>/*/SKILL.md`` across ``directories``."""
    skills: list[Skill] = []
    for directory in directories:
        root = Path(directory).expanduser()
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/SKILL.md")):
            try:
                skills.append(parse_skill_file(candidate))
            except OSError:
                continue
    return skills


#: Maximum skills inlined into the system prompt (alphabetical, capped so a
#: bloated skills dir cannot blow the context window).
MAX_SKILLS = 20


def merge_skills(layers: list[list[Skill]]) -> list[Skill]:
    """Merge skill layers; later layers override earlier ones by name.

    Returns skills sorted by name and capped at ``MAX_SKILLS``.
    """
    merged: dict[str, Skill] = {}
    for layer in layers:
        for skill in layer:
            merged[skill.name] = skill
    return sorted(merged.values(), key=lambda s: s.name)[:MAX_SKILLS]


def render_skills_prompt(skills: list[Skill]) -> str:
    """Render the system-prompt section describing available skills."""
    if not skills:
        return ""
    lines = ["Available skills (apply when relevant):"]
    for skill in skills:
        trigger = skill.description or f"skill '{skill.name}'"
        lines.append(f"- {skill.name}: {trigger}")
        if skill.content:
            lines.append(f"  {skill.content[:500]}".rstrip())
    return "\n".join(lines)
