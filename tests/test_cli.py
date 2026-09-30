"""The ``pico`` CLI's event rendering."""

from pico_ai.types import ToolCall
from pico_core.fsm import LoopEvent
from pico_core.session import ToolRequestPayload, ToolResultPayload
from pico_sdk.cli import build_parser, format_event


def test_format_event_prints_text():
    assert format_event(LoopEvent(kind="text", text="hello")) == "hello"


def test_format_event_echoes_bash_before_execution():
    request = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})
        ),
    )
    assert format_event(request) == "$ echo hi\n"


def test_format_event_prints_bash_result():
    result = LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(
            tool_call_id="c1", name="bash", content="hi\n[exit code: 0]"
        ),
    )
    assert format_event(result) == "hi\n[exit code: 0]\n"


def test_format_event_ignores_non_bash_tools():
    result = LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(tool_call_id="c1", name="read", content="data"),
    )
    assert format_event(result) is None


def test_bash_enabled_by_default():
    args = build_parser().parse_args(["run", "hi"])
    assert args.no_bash is False


def test_no_bash_flag_disables_bash():
    args = build_parser().parse_args(["run", "hi", "--no-bash"])
    assert args.no_bash is True


def test_provider_flag_defaults_none():
    args = build_parser().parse_args(["run", "hi"])
    assert args.provider is None
    args = build_parser().parse_args(["run", "hi", "--provider", "ollama"])
    assert args.provider == "ollama"


def test_allow_tools_flag_parses_csv():
    args = build_parser().parse_args(["run", "hi", "--allow-tools", "read,grep,bash"])
    assert args.allow_tools == "read,grep,bash"


def test_skills_flags_default():
    args = build_parser().parse_args(["run", "hi"])
    assert args.skills_dir is None
    assert args.no_skills is False


def test_format_event_shows_error_results():
    result = LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(
            tool_call_id="c1", name="read", content="error: tool not allowed: read",
            is_error=True,
        ),
    )
    rendered = format_event(result)
    assert rendered is not None
    assert "not allowed" in rendered


def test_apply_cli_overrides_sets_settings():
    from pico_sdk.cli import apply_cli_overrides
    from pico_sdk.config import Settings

    settings = Settings()
    args = build_parser().parse_args(
        ["run", "hi", "--allow-tools", "read, grep", "--skills-dir", "/tmp/sk"]
    )
    assert apply_cli_overrides(args, settings) is True
    assert settings.allowed_tools == ["read", "grep"]
    assert settings.skills_dir == "/tmp/sk"


def test_apply_cli_overrides_no_skills():
    from pico_sdk.cli import apply_cli_overrides
    from pico_sdk.config import Settings

    settings = Settings()
    args = build_parser().parse_args(["run", "hi", "--no-skills"])
    assert apply_cli_overrides(args, settings) is False
