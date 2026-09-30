"""ADR-0003 — hardcoded core: curated hooks, skills, permission gating."""

import pytest

from pico_ai.types import StreamEvent, ToolCall
from pico_sdk.config import Settings

from conftest import FakeProvider, make_session


async def test_core_tools_are_hardcoded(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    assert session.tools.names() == [
        "read",
        "write",
        "edit",
        "grep",
        "fetch",
        "websearch",
        "bash",
        "todo",
    ]


async def test_curated_hooks_fire_and_failure_hook(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c2", name="read", arguments={"path": "missing.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = make_session(provider, tmp_path)
    calls = []

    async def on_start(session):
        calls.append("session_start")

    async def before(name, arguments):
        calls.append(f"pre:{name}")

    async def after(name, arguments, result):
        calls.append(f"post:{name}")

    async def failed(name, arguments, result):
        calls.append(f"failed:{name}")

    session.on("session_start", on_start)
    session.on("pre_tool_use", before)
    session.on("post_tool_use", after)
    session.on("post_tool_failure", failed)
    await session.run("read files")
    assert "session_start" in calls
    assert "pre:read" in calls
    assert "post:read" in calls
    # missing.txt errors, so the failure hook fires
    assert "failed:read" in calls


async def test_legacy_hook_aliases_still_work(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = make_session(provider, tmp_path)
    calls = []
    session.on("on_session_start", lambda session: calls.append("start"))
    session.on("tool.before.*", lambda name, arguments: calls.append("before"))
    session.on("tool.after.*", lambda name, arguments, result: calls.append("after"))
    await session.run("read a.txt")
    assert calls == ["start", "before", "after"]


def test_unknown_hook_rejected(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    with pytest.raises(ValueError, match="unknown hook"):
        session.on("register_anything", lambda: None)


def test_hook_unsubscribe(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    calls = []
    off = session.on("pre_tool_use", lambda name, arguments: calls.append(name))
    off()
    assert session.extensions._hooks["pre_tool_use"] == []


def test_no_generic_plugin_api(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    for attr in ("register_tool", "register_provider", "use_provider", "load_plugins"):
        assert not hasattr(session, attr), attr
    assert not hasattr(session.extensions, "register_provider")
    assert not hasattr(session.extensions, "load_plugins")


def test_skills_discovered_into_prompt(tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "commit-helper").mkdir(parents=True)
    (skills_root / "commit-helper" / "SKILL.md").write_text(
        "---\nname: commit-helper\ndescription: Use when committing code.\n---\nRun git status first.\n",
        encoding="utf-8",
    )
    settings = Settings(
        session_dir=str(tmp_path), skills_dir=str(skills_root)
    )
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    assert "commit-helper" in session.system_prompt
    assert "Use when committing code." in session.system_prompt


async def test_allowed_tools_gating(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    settings = Settings(session_dir=str(tmp_path), allowed_tools=["write"])
    session = make_session(provider, tmp_path, settings=settings)
    result = await session.run("read a.txt")
    assert result.text == "done"
    tool_results = [
        n.payload
        for n in session.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert tool_results[0].is_error is True
    assert "not allowed" in tool_results[0].content


def test_project_skill_overrides_global(tmp_path):
    global_root = tmp_path / "global-skills"
    (global_root / "helper").mkdir(parents=True)
    (global_root / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: Global version.\n---\nGlobal content.\n",
        encoding="utf-8",
    )
    # project-local skill with the same name wins; tmp_path is the working dir
    project_root = tmp_path / ".pico" / "skills"
    (project_root / "helper").mkdir(parents=True)
    (project_root / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: Project version.\n---\nProject content.\n",
        encoding="utf-8",
    )
    settings = Settings(
        session_dir=str(tmp_path), skills_dir=str(global_root)
    )
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    assert "Project version." in session.system_prompt
    assert "Global version." not in session.system_prompt


def test_skills_all_loaded_no_cap(tmp_path):
    skills_root = tmp_path / "many-skills"
    for i in range(25):
        d = skills_root / f"skill-{i:02d}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: skill-{i:02d}\ndescription: Skill {i}.\n---\nContent {i}.\n",
            encoding="utf-8",
        )
    settings = Settings(
        session_dir=str(tmp_path), skills_dir=str(skills_root)
    )
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=True
    )
    # no cap: every skill is inlined, alphabetical
    assert "skill-24" in session.system_prompt
    assert "skill-00" in session.system_prompt
    tmp_skills = [
        s for s in session.skills if str(s.path).startswith(str(skills_root))
    ]
    assert len(tmp_skills) == 25


def test_agents_layer_loses_to_skills_dir(tmp_path):
    from pico_sdk.skills import discover_skills, merge_skills

    agents_root = tmp_path / "agents-skills"
    (agents_root / "helper").mkdir(parents=True)
    (agents_root / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: Agents version.\n---\nAgents content.\n",
        encoding="utf-8",
    )
    pico_root = tmp_path / "pico-skills"
    (pico_root / "helper").mkdir(parents=True)
    (pico_root / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: Pico version.\n---\nPico content.\n",
        encoding="utf-8",
    )
    merged = merge_skills(
        [discover_skills([agents_root]), discover_skills([pico_root])]
    )
    assert len(merged) == 1
    assert merged[0].description == "Pico version."


async def test_unknown_tool_fires_pre_and_failure_hooks(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="nope", arguments={}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = make_session(provider, tmp_path)
    calls = []
    session.on("pre_tool_use", lambda name, arguments: calls.append(f"pre:{name}"))
    session.on(
        "post_tool_failure",
        lambda name, arguments, result: calls.append(f"failed:{name}"),
    )
    await session.run("try unknown")
    assert "pre:nope" in calls
    assert "failed:nope" in calls


async def test_denied_tool_fires_pre_and_failure_hooks(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    settings = Settings(session_dir=str(tmp_path), allowed_tools=[])
    session = make_session(provider, tmp_path, settings=settings)
    calls = []
    session.on("pre_tool_use", lambda name, arguments: calls.append(f"pre:{name}"))
    session.on(
        "post_tool_failure",
        lambda name, arguments, result: calls.append(f"failed:{name}"),
    )
    await session.run("read a.txt")
    assert "pre:read" in calls
    assert "failed:read" in calls


async def test_allowed_tools_empty_blocks_everything(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    settings = Settings(session_dir=str(tmp_path), allowed_tools=[])
    session = make_session(provider, tmp_path, settings=settings)
    await session.run("read a.txt")
    tool_results = [
        n.payload
        for n in session.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert tool_results[0].is_error is True
    assert "not allowed" in tool_results[0].content


def test_unknown_allowed_tool_name_warns(tmp_path):
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = Settings(
            session_dir=str(tmp_path), allowed_tools=["read", "bogus"]
        )
        make_session(FakeProvider([]), tmp_path, settings=settings)
    assert any("bogus" in str(w.message) for w in caught)
