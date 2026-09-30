"""Picker search bar: live filtering, keyboard flow, and cancel safety."""

from typing import Optional

from textual.app import App
from textual.widgets import Input, Label, OptionList

from pico_tui.app import PicoApp, _SessionManager
from pico_tui.skill_picker import SkillPickerScreen

from conftest import FakeProvider, make_session


def _entries() -> list[dict]:
    return [
        {"name": "triage", "description": "Triage issues."},
        {"name": "commit-helper", "description": "Help committing."},
        {"name": "teach", "description": "Teach a concept."},
    ]


class Host(App[Optional[str]]):
    def compose(self):
        yield Label("host")


async def _open(entries: list[dict], pilot_host: Host):
    """Push a skill picker; return (screen, captured)."""
    captured: list[str | None] = []
    screen = SkillPickerScreen(entries)
    pilot_host.push_screen(screen, callback=captured.append)
    return screen, captured


async def test_typing_filters_rows():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_entries(), app)
        await pilot.pause()

        search = screen.query_one("#skill-picker-search", Input)
        search.value = "tri"
        await pilot.pause()

        option_list = screen.query_one("#skill-picker-list", OptionList)
        assert option_list.option_count == 1
        assert screen._visible == [0]


async def test_filter_matches_description_case_insensitive():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_entries(), app)
        await pilot.pause()

        screen.query_one("#skill-picker-search", Input).value = "TEACH"
        await pilot.pause()

        assert screen.query_one("#skill-picker-list", OptionList).option_count == 1
        assert screen._visible == [2]


async def test_clearing_filter_restores_all_rows():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_entries(), app)
        await pilot.pause()

        search = screen.query_one("#skill-picker-search", Input)
        search.value = "tri"
        await pilot.pause()
        search.value = ""
        await pilot.pause()

        assert screen.query_one("#skill-picker-list", OptionList).option_count == 3
        assert screen._visible == [0, 1, 2]


async def test_enter_picks_filtered_row():
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(_entries(), app)
        await pilot.pause()

        screen.query_one("#skill-picker-search", Input).value = "commit"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert captured == ["commit-helper"]


async def test_click_maps_through_filter():
    entries = [
        {"name": "triage", "description": "Triage issues."},
        {"name": "commit", "description": "Commit code."},
        {"name": "nap", "description": "Nap time."},
    ]
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(entries, app)
        await pilot.pause()

        # "m" matches commit (idx 1) and nap (idx 2), not triage.
        screen.query_one("#skill-picker-search", Input).value = "m"
        await pilot.pause()
        assert screen._visible == [1, 2]

        option_list = screen.query_one("#skill-picker-list", OptionList)
        option_list.highlighted = 1
        option_list.action_select()
        await pilot.pause()

    assert captured == ["nap"]


async def test_typing_q_does_not_cancel():
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(_entries(), app)
        await pilot.pause()

        await pilot.press("q")
        await pilot.pause()

        # "q" is filter text (matches nothing), not a cancel binding.
        assert captured == []
        assert screen.query_one("#skill-picker-search", Input).value == "q"
        assert screen.query_one("#skill-picker-list", OptionList).option_count == 0


async def test_no_match_enter_keeps_picker_open():
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(_entries(), app)
        await pilot.pause()

        screen.query_one("#skill-picker-search", Input).value = "zzz"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert captured == []
        # still open
        assert screen.query_one("#skill-picker-list", OptionList) is not None


async def test_arrows_move_highlight_from_search():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_entries(), app)
        await pilot.pause()

        # focus is on the search bar by default
        assert screen.query_one("#skill-picker-search", Input).has_focus
        option_list = screen.query_one("#skill-picker-list", OptionList)
        assert option_list.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert option_list.highlighted == 1

        await pilot.press("up")
        await pilot.pause()
        assert option_list.highlighted == 0


async def test_escape_from_search_cancels():
    app = Host()
    async with app.run_test() as pilot:
        _, captured = await _open(_entries(), app)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

    assert captured == [None]


# ── end-to-end: /skills → filter → Enter → input bar ──────────────


def _make_app(tmp_path) -> PicoApp:
    from pico_sdk.config import Settings

    skills_root = tmp_path / "skills"
    for name in ("triage", "commit-helper"):
        (skills_root / name).mkdir(parents=True)
        (skills_root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Desc {name}.\n---\nContent.\n",
            encoding="utf-8",
        )
    settings = Settings(session_dir=str(tmp_path), skills_dir=str(skills_root))
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    return PicoApp(_SessionManager(session))


async def test_skills_filter_then_enter_inserts(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/skills"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        names = [s.name for s in app._mgr.session.skills]
        target = names.index("commit-helper")
        search = app.screen.query_one("#skill-picker-search", Input)
        # exact skill name: matches only the tmp skill (a shorter filter
        # like "commit" also hits e.g. code-review's description)
        search.value = "commit-helper"
        await pilot.pause()
        assert app.screen.query_one("#skill-picker-list", OptionList).option_count == 1

        await pilot.press("enter")
        await pilot.pause()

        assert (
            app.query_one("#input-bar", Input).value
            == "Use the 'commit-helper' skill: "
        )
        assert target >= 0  # sanity: the tmp skill was among loaded skills
