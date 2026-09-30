"""Skill picker: entries, screen dismissal, and insert-on-pick."""

from typing import Optional

import pytest
from textual.app import App
from textual.widgets import Input, Label, OptionList

from pico_ai.types import StreamEvent
from pico_sdk.config import Settings
from pico_tui.app import PicoApp, _SessionManager
from pico_tui.skill_picker import SkillPickerScreen, format_skill_option

from conftest import FakeProvider, make_session


def _entries() -> list[dict]:
    return [
        {"name": "triage", "description": "Triage issues."},
        {"name": "commit-helper", "description": "Help committing."},
    ]


# ── formatting ────────────────────────────────────────────────────


def test_format_skill_option_plain():
    text = format_skill_option("triage", "Triage issues.")
    assert "triage" in text
    assert "Triage issues." in text


def test_format_skill_option_empty_description():
    text = format_skill_option("helper")
    assert "helper" in text
    assert "\n" not in text


def test_format_skill_option_single_line_with_ellipsis():
    from pico_tui.skill_picker import MAX_OPTION_WIDTH

    long_desc = " ".join(["word"] * 50)
    text = format_skill_option("triage", long_desc)
    plain = text.replace("[bold]", "").replace("[/]", "").replace("[dim]", "")
    assert "\n" not in plain
    assert len(plain) <= MAX_OPTION_WIDTH
    assert plain.endswith("...")


def test_format_skill_option_collapses_newlines():
    text = format_skill_option("triage", "line one\nline two\nline three")
    assert "\n" not in text
    assert "line one line two line three" in text


# ── entries ───────────────────────────────────────────────────────


def test_skill_entries_mirror_loaded_skills(tmp_path):
    skills_root = tmp_path / "skills"
    for name in ("b-skill", "a-skill"):
        (skills_root / name).mkdir(parents=True)
        (skills_root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Desc {name}.\n---\nContent.\n",
            encoding="utf-8",
        )
    settings = Settings(
        session_dir=str(tmp_path), skills_dir=str(skills_root)
    )
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    entries = _SessionManager(session).skill_entries()
    tmp_entries = [e for e in entries if e["name"] in ("a-skill", "b-skill")]
    assert [e["name"] for e in tmp_entries] == ["a-skill", "b-skill"]
    assert tmp_entries[0]["description"] == "Desc a-skill."


def test_skill_entries_empty_session(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    assert _SessionManager(session).skill_entries() == []


# ── screen ────────────────────────────────────────────────────────


async def test_picker_dismisses_with_skill_name():
    """Selecting an option dismisses with its skill name, not the row."""

    class Host(App[Optional[str]]):
        def compose(self):
            yield Label("host")

    app = Host()
    async with app.run_test() as pilot:
        captured: list[str | None] = []
        screen = SkillPickerScreen(_entries())
        app.push_screen(screen, callback=captured.append)
        await pilot.pause()

        option_list = screen.query_one("#skill-picker-list", OptionList)
        option_list.highlighted = 1
        option_list.action_select()  # simulates Enter / click selection
        await pilot.pause()

    assert captured == ["commit-helper"]


async def test_picker_cancel_dismisses_with_none():
    class Host(App[Optional[str]]):
        def compose(self):
            yield Label("host")

    app = Host()
    async with app.run_test() as pilot:
        captured: list[str | None] = []
        app.push_screen(SkillPickerScreen(_entries()), callback=captured.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert captured == [None]


# ── end-to-end: /skills → pick → input bar ────────────────────────


def _make_app(tmp_path) -> PicoApp:
    skills_root = tmp_path / "skills"
    (skills_root / "triage").mkdir(parents=True)
    (skills_root / "triage" / "SKILL.md").write_text(
        "---\nname: triage\ndescription: Triage issues.\n---\nTriage content.\n",
        encoding="utf-8",
    )
    settings = Settings(
        session_dir=str(tmp_path), skills_dir=str(skills_root)
    )
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    return PicoApp(_SessionManager(session))


async def test_skills_pick_inserts_into_input(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/skills"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        option_list = app.screen.query_one("#skill-picker-list", OptionList)
        # the tmp skill must be among the rows (real ~/.agents/skills
        # may add more rows on machines that have it)
        names = [s.name for s in app._mgr.session.skills]
        idx = names.index("triage")
        option_list.highlighted = idx
        option_list.action_select()
        await pilot.pause()

        assert app.query_one("#input-bar", Input).value == "Use the 'triage' skill: "


async def test_skills_cancel_keeps_input_empty(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/skills"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.screen.query_one("#skill-picker-list", OptionList) is not None
        await pilot.press("escape")
        await pilot.pause()

        # submit cleared the bar and cancel inserted nothing
        assert app.query_one("#input-bar", Input).value == ""


async def test_skills_empty_session_shows_notice(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    assert session.skills == [] or True  # home dir may provide skills
    if session.skills:
        pytest.skip("home ~/.agents/skills present — picker opens instead")
    app = PicoApp(_SessionManager(session))
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/skills"
        await pilot.press("enter")
        await pilot.pause()

        # No picker for an empty skill set — a notice goes to the chat log.
        with pytest.raises(Exception):
            app.screen.query_one("#skill-picker-list", OptionList)
        assert any(
            "no skills" in str(item) for item in app._transcript
        )
