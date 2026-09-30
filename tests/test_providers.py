"""Native provider adapters: each backend normalizes to the AI-call shape."""

import json

import httpx
import pytest

from pico_ai.providers import PROVIDERS, create_provider, get_spec, provider_ids
from pico_ai.providers.anthropic import AnthropicProvider
from pico_ai.providers.deepseek import DeepSeekProvider
from pico_ai.providers.gemini import GeminiProvider
from pico_ai.providers.ollama import OllamaProvider
from pico_ai.providers.openai import OpenAIProvider
from pico_ai.types import AICallRequest, Message, ToolCall, ToolDefinition

from conftest import FakeProvider, make_session


def _mock_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _request(**kwargs) -> AICallRequest:  # type: ignore[no-untyped-def]
    return AICallRequest(
        model="test-model", messages=[Message(role="user", content="hi")], **kwargs
    )


async def _collect(provider, request):  # type: ignore[no-untyped-def]
    return [e async for e in provider.stream(request)]


# ── registry ──────────────────────────────────────────────────────────


def test_registry_lists_all_providers():
    assert provider_ids() == [
        "openrouter",
        "openai",
        "anthropic",
        "gemini",
        "deepseek",
        "ollama",
    ]


def test_registry_unknown_id_raises():
    with pytest.raises(KeyError):
        get_spec("bogus")


def test_every_spec_has_fields_and_factory():
    for pid in provider_ids():
        spec = get_spec(pid)
        assert spec.display_name
        assert spec.default_model
        adapter = create_provider(pid, {})
        assert getattr(adapter, "provider_id", None) == pid


def test_factories_build_correct_classes():
    assert isinstance(create_provider("openai", {}), OpenAIProvider)
    assert isinstance(create_provider("anthropic", {}), AnthropicProvider)
    assert isinstance(create_provider("gemini", {}), GeminiProvider)
    assert isinstance(create_provider("deepseek", {}), DeepSeekProvider)
    assert isinstance(create_provider("ollama", {}), OllamaProvider)
    assert len(PROVIDERS) == 6


# ── OpenAI ────────────────────────────────────────────────────────────


async def test_openai_streams_text_and_usage():
    sse = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}],'
        ' "usage":{"prompt_tokens":3,"completion_tokens":5,"total_tokens":8}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        return httpx.Response(200, content=sse)

    provider = OpenAIProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(provider, _request())
    assert "".join(e.text for e in events if e.kind == "text") == "Hello world"
    usage = [e.usage for e in events if e.kind == "usage"][0]
    assert (usage.input_tokens, usage.output_tokens) == (3, 5)


async def test_openai_streams_tool_call():
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":""}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":\\"a\\"}"}}]}}]}\n\n'
        'data: {"choices":[{"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        return httpx.Response(200, content=sse)

    provider = OpenAIProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(
        provider,
        _request(tools=[ToolDefinition(name="read", input_schema={"type": "object"})]),
    )
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert calls == [ToolCall(id="c1", name="read", arguments={"path": "a"})]


async def test_openai_surfaces_api_error():
    async def handler(request):
        return httpx.Response(401, content='{"error": {"message": "bad key"}}')

    provider = OpenAIProvider(api_key="bad", client=_mock_client(handler))
    with pytest.raises(RuntimeError, match="OpenAI error 401.*bad key"):
        await _collect(provider, _request())


async def test_openai_lists_models():
    async def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, content=json.dumps({"data": [{"id": "gpt-4o"}]}))

    provider = OpenAIProvider(api_key="k", client=_mock_client(handler))
    assert await provider.list_models() == [
        {"id": "gpt-4o", "name": "gpt-4o", "is_free": False, "supports_tools": True}
    ]


def test_openai_payload_has_system_and_tools():
    provider = OpenAIProvider(api_key="k")
    payload = provider._build_payload(
        _request(
            system="sys",
            tools=[ToolDefinition(name="read", input_schema={"type": "object"})],
        )
    )
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["tools"][0]["function"]["name"] == "read"


# ── DeepSeek (reasoning_content → thinking) ───────────────────────────


async def test_deepseek_reasoning_becomes_thinking():
    sse = (
        'data: {"choices":[{"delta":{"reasoning_content":"let me think"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request):
        return httpx.Response(200, content=sse)

    provider = DeepSeekProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(provider, _request())
    assert [e.thinking for e in events if e.kind == "thinking"] == ["let me think"]
    assert "".join(e.text for e in events if e.kind == "text") == "answer"


def test_deepseek_spec_fields():
    spec = get_spec("deepseek")
    assert [f.key for f in spec.fields] == ["api_key", "base_url", "model"]
    assert spec.fields[0].env_var == "DEEPSEEK_API_KEY"


# ── Anthropic ─────────────────────────────────────────────────────────


def _anthropic_stream(chunks) -> str:  # type: ignore[no-untyped-def]
    return "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in chunks)


async def test_anthropic_streams_text_tool_and_usage():
    body = _anthropic_stream(
        [
            ("message_start", {"message": {"usage": {"input_tokens": 10}}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hi" }}),
            (
                "content_block_start",
                {
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "read",
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
                },
            ),
            (
                "content_block_delta",
                {
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '"a"}'},
                },
            ),
            ("message_delta", {"usage": {"output_tokens": 7}}),
            ("message_stop", {}),
        ]
    )

    async def handler(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(
        provider,
        _request(tools=[ToolDefinition(name="read", input_schema={"type": "object"})]),
    )
    assert "".join(e.text for e in events if e.kind == "text") == "Hi"
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert calls == [ToolCall(id="tu1", name="read", arguments={"path": "a"})]
    usage = [e.usage for e in events if e.kind == "usage"][0]
    assert (usage.input_tokens, usage.output_tokens) == (10, 7)


async def test_anthropic_error_event_raises():
    body = _anthropic_stream([("error", {"message": "overloaded"})])

    async def handler(request):
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(api_key="k", client=_mock_client(handler))
    with pytest.raises(RuntimeError, match="overloaded"):
        await _collect(provider, _request())


async def test_anthropic_http_error_raises():
    async def handler(request):
        return httpx.Response(401, content='{"error": "bad key"}')

    provider = AnthropicProvider(api_key="bad", client=_mock_client(handler))
    with pytest.raises(RuntimeError, match="Anthropic error 401"):
        await _collect(provider, _request())


def test_anthropic_payload_shape():
    provider = AnthropicProvider(api_key="k")
    payload = provider._build_payload(
        AICallRequest(
            system="sys",
            model="m",
            messages=[
                Message(role="user", content="read it"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a"})],
                ),
                Message(role="tool", tool_call_id="t1", name="read", content="data"),
            ],
            tools=[ToolDefinition(name="read", input_schema={"type": "object"})],
        )
    )
    assert payload["system"] == "sys"
    assert payload["max_tokens"] == 4096
    assistant = payload["messages"][1]
    assert assistant["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "read",
        "input": {"path": "a"},
    }
    tool_msg = payload["messages"][2]
    assert tool_msg["content"][0]["type"] == "tool_result"
    assert payload["tools"][0]["input_schema"] == {"type": "object"}


async def test_anthropic_curated_models():
    models = await AnthropicProvider(api_key="k").list_models()
    assert models and all(m["supports_tools"] for m in models)


# ── Gemini ────────────────────────────────────────────────────────────


async def test_gemini_streams_text_call_and_usage():
    sse = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n'
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"read","args":{"path":"a"}}}]}}]}\n\n'
        'data: {"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":6,"totalTokenCount":10}}\n\n'
    )

    async def handler(request):
        assert "x-goog-api-key" in request.headers
        return httpx.Response(200, content=sse)

    provider = GeminiProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(
        provider,
        _request(tools=[ToolDefinition(name="read", input_schema={"type": "object"})]),
    )
    assert "".join(e.text for e in events if e.kind == "text") == "Hello"
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert len(calls) == 1
    assert (calls[0].name, calls[0].arguments) == ("read", {"path": "a"})
    usage = [e.usage for e in events if e.kind == "usage"][0]
    assert (usage.input_tokens, usage.output_tokens) == (4, 6)


def test_gemini_merges_consecutive_tool_messages():
    provider = GeminiProvider(api_key="k")
    payload = provider._build_payload(
        AICallRequest(
            model="m",
            messages=[
                Message(role="user", content="do two reads"),
                Message(role="tool", tool_call_id="t1", name="read", content="one"),
                Message(role="tool", tool_call_id="t2", name="read", content="two"),
            ],
        )
    )
    # Strict user/model alternation: the prompt and both tool results merge
    # into one user message (text part + two functionResponse parts).
    assert len(payload["contents"]) == 1
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "do two reads"}
    assert [p["functionResponse"]["response"]["result"] for p in parts[1:]] == [
        "one",
        "two",
    ]


async def test_gemini_http_error_raises():
    async def handler(request):
        return httpx.Response(400, content='{"error": "bad request"}')

    provider = GeminiProvider(api_key="k", client=_mock_client(handler))
    with pytest.raises(RuntimeError, match="Gemini error 400"):
        await _collect(provider, _request())


async def test_gemini_models_fallback_when_unreachable():
    async def handler(request):
        return httpx.Response(403, content="denied")

    provider = GeminiProvider(api_key="k", client=_mock_client(handler))
    models = await provider.list_models()
    assert [m["id"] for m in models] == ["gemini-2.0-flash", "gemini-1.5-pro"]


async def test_gemini_captures_thought_signature():
    sse = (
        'data: {"candidates":[{"content":{"parts":['
        '{"functionCall":{"name":"bash","args":{"command":"ls"}},'
        '"thoughtSignature":"SIG_A"}]}}]}\n\n'
    )

    async def handler(request):
        return httpx.Response(200, content=sse)

    provider = GeminiProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(provider, _request())
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert len(calls) == 1
    assert calls[0].thought_signature == "SIG_A"


def test_gemini_replays_thought_signature_verbatim():
    provider = GeminiProvider(api_key="k")
    payload = provider._build_payload(
        AICallRequest(
            model="m",
            messages=[
                Message(role="user", content="list files"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_0",
                            name="bash",
                            arguments={"command": "ls"},
                            thought_signature="SIG_A",
                        )
                    ],
                ),
                Message(
                    role="tool",
                    tool_call_id="call_0",
                    name="bash",
                    content="a.txt",
                ),
            ],
        )
    )
    model_msg = next(m for m in payload["contents"] if m["role"] == "model")
    part = model_msg["parts"][0]
    assert part["functionCall"]["name"] == "bash"
    assert part["thoughtSignature"] == "SIG_A"


def test_gemini_omits_signature_when_absent():
    provider = GeminiProvider(api_key="k")
    payload = provider._build_payload(
        AICallRequest(
            model="m",
            messages=[
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="c", name="bash", arguments={})],
                ),
            ],
        )
    )
    part = payload["contents"][0]["parts"][0]
    assert "thoughtSignature" not in part


async def test_gemini_signature_round_trip_closes_400_path():
    """Parse a signed call, then replay it: the follow-up carries the sig."""
    sse = (
        'data: {"candidates":[{"content":{"parts":['
        '{"functionCall":{"name":"bash","args":{"command":"ls"}},'
        '"thoughtSignature":"SIG_A"}]}}]}\n\n'
    )

    async def handler(request):
        return httpx.Response(200, content=sse)

    provider = GeminiProvider(api_key="k", client=_mock_client(handler))
    events = await _collect(provider, _request())
    call = next(e.tool_call for e in events if e.kind == "tool_call")
    follow_up = provider._build_payload(
        AICallRequest(
            model="m",
            messages=[
                Message(role="user", content="list files"),
                Message(role="assistant", content="", tool_calls=[call]),
                Message(
                    role="tool", tool_call_id=call.id, name="bash", content="a.txt"
                ),
            ],
        )
    )
    part = next(
        m for m in follow_up["contents"] if m["role"] == "model"
    )["parts"][0]
    assert part["thoughtSignature"] == "SIG_A"


def test_other_providers_ignore_thought_signature():
    """The new field is opaque: OpenAI payloads never render it."""
    provider = OpenAIProvider(api_key="k")
    payload = provider._build_payload(
        AICallRequest(
            model="m",
            messages=[
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c",
                            name="read",
                            arguments={},
                            thought_signature="SIG_X",
                        )
                    ],
                ),
            ],
            tools=[ToolDefinition(name="read", input_schema={"type": "object"})],
        )
    )
    assert "thoughtSignature" not in json.dumps(payload)
    assert "thought_signature" not in json.dumps(payload)


# ── Ollama ────────────────────────────────────────────────────────────


async def test_ollama_ndjson_text_call_and_usage():
    ndjson = (
        '{"message":{"role":"assistant","content":"Hello"}}\n'
        '{"message":{"role":"assistant","content":" world"}}\n'
        '{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":"read","arguments":{"path":"a"}}}]}}\n'
        '{"done":true,"prompt_eval_count":5,"eval_count":9}\n'
    )

    async def handler(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, content=ndjson)

    provider = OllamaProvider(client=_mock_client(handler))
    events = await _collect(
        provider,
        _request(tools=[ToolDefinition(name="read", input_schema={"type": "object"})]),
    )
    assert "".join(e.text for e in events if e.kind == "text") == "Hello world"
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert len(calls) == 1
    assert (calls[0].name, calls[0].arguments) == ("read", {"path": "a"})
    usage = [e.usage for e in events if e.kind == "usage"][0]
    assert (usage.input_tokens, usage.output_tokens) == (5, 9)


async def test_ollama_http_error_raises():
    async def handler(request):
        return httpx.Response(500, content="nope")

    provider = OllamaProvider(client=_mock_client(handler))
    with pytest.raises(RuntimeError, match="Ollama error 500"):
        await _collect(provider, _request())


async def test_ollama_lists_local_tags():
    async def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, content=json.dumps({"models": [{"name": "llama3.1"}]}))

    models = await OllamaProvider(client=_mock_client(handler)).list_models()
    assert models[0]["id"] == "llama3.1"
    assert models[0]["is_free"] is True


def test_ollama_needs_no_key():
    assert get_spec("ollama").required_fields == ()


# ── SDK wiring: config, switching, helpers ────────────────────────────


def test_effective_config_precedence(tmp_path, monkeypatch):
    from pico_sdk.config import Settings
    from pico_sdk.providers import effective_config

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    settings = Settings(providers={"openai": {"api_key": "stored-key"}})
    assert effective_config("openai", settings)["api_key"] == "stored-key"
    del settings.providers["openai"]
    assert effective_config("openai", settings)["api_key"] == "env-key"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert effective_config("openai", settings)["api_key"] == ""
    assert (
        effective_config("openai", settings)["base_url"]
        == "https://api.openai.com/v1"
    )


def test_missing_required_and_describe(tmp_path):
    from pico_sdk.config import Settings
    from pico_sdk.providers import describe_providers, missing_required

    settings = Settings(provider="ollama")
    assert missing_required("openai", settings) == ["API key"]
    assert missing_required("ollama", settings) == []
    rows = {r["id"]: r for r in describe_providers(settings)}
    assert rows["ollama"]["active"] is True
    assert rows["ollama"]["configured"] is True
    assert rows["openai"]["active"] is False
    assert rows["openai"]["configured"] is False


def test_create_provider_per_settings_and_unknown_fallback(tmp_path):
    import warnings

    from pico_sdk.config import Settings
    from pico_sdk.providers import create_provider as build

    assert isinstance(build(Settings(provider="ollama")), OllamaProvider)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        adapter = build(Settings(provider="nope"))
    from pico_ai.openrouter import OpenRouterProvider

    assert isinstance(adapter, OpenRouterProvider)
    assert any("nope" in str(w.message) for w in caught)


def test_session_set_provider_swaps_adapter_model_and_settings(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    adapter = session.set_provider("openai", {"api_key": "sk-x"})
    assert isinstance(adapter, OpenAIProvider)
    assert session.loop.provider is adapter
    assert session.provider_id == "openai"
    assert session.provider_name == "OpenAI"
    assert session.model == "gpt-4o-mini"
    assert session.settings.provider == "openai"
    assert session.settings.model == "gpt-4o-mini"
    assert session.settings.providers["openai"]["api_key"] == "sk-x"


def test_provider_and_model_survive_settings_round_trip(tmp_path):
    """Simulates reopening the TUI: switch, save, reload, rebuild."""
    from pico_sdk.config import load_settings, save_settings
    from pico_sdk.providers import create_provider as build

    session = make_session(FakeProvider([]), tmp_path)
    session.set_provider("ollama", {"model": "qwen2.5-coder"})
    settings_path = tmp_path / "settings.json"
    save_settings(session.settings, settings_path)

    reopened = load_settings(settings_path)
    assert reopened.provider == "ollama"
    assert reopened.model == "qwen2.5-coder"
    assert reopened.providers["ollama"]["model"] == "qwen2.5-coder"
    rebuilt = build(reopened)
    assert isinstance(rebuilt, OllamaProvider)


def test_session_set_provider_unknown_raises(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    with pytest.raises(KeyError):
        session.set_provider("bogus")


async def test_resolve_free_model_tolerates_sparse_entries():
    from pico_sdk.providers import resolve_free_model

    class Sparse:
        async def list_models(self):
            # Missing optional keys (supports_tools) must not crash.
            return [
                {"id": "b", "name": "Beta", "is_free": True},
                {"id": "a", "name": "Alpha", "is_free": True},
            ]

    assert await resolve_free_model(Sparse()) == "a"


# ── TUI: /provider command, picker, form ──────────────────────────────


def test_parse_line_provider():
    from pico_tui.commands import Command, parse_line

    assert parse_line("/provider") == Command("provider", "")
    assert parse_line("/provider ollama") == Command("provider", "ollama")


def test_provider_picker_format_markers():
    from pico_tui.provider_picker import format_provider_option

    plain = format_provider_option("OpenAI", "GPT models.")
    assert "OpenAI" in plain and "current" not in plain
    marked = format_provider_option("Ollama", "Local.", configured=True, active=True)
    assert "current" in marked


def test_manager_apply_provider_message(tmp_path):
    from pico_tui.app import _SessionManager

    mgr = _SessionManager(make_session(FakeProvider([]), tmp_path))
    assert mgr.apply_provider("ollama", {}) == (
        "switched to Ollama (model: llama3.1)"
    )


async def test_provider_picker_selects_id():
    from typing import Optional

    from textual.app import App
    from textual.widgets import Label, OptionList

    from pico_sdk.config import Settings
    from pico_sdk.providers import describe_providers
    from pico_tui.provider_picker import ProviderPickerScreen

    class Host(App[Optional[str]]):
        def compose(self):
            yield Label("host")

    app = Host()
    async with app.run_test() as pilot:
        captured: list[str | None] = []
        entries = describe_providers(Settings(provider="ollama"))
        screen = ProviderPickerScreen(entries)
        app.push_screen(screen, callback=captured.append)
        await pilot.pause()

        option_list = screen.query_one("#provider-picker-list", OptionList)
        option_list.highlighted = 0
        option_list.action_select()
        await pilot.pause()

    assert captured == ["openrouter"]


async def test_provider_form_save_and_cancel():
    from typing import Optional

    from textual.app import App
    from textual.widgets import Input, Label

    from pico_ai.providers import get_spec
    from pico_tui.provider_form import ProviderFormScreen

    spec = get_spec("openai")
    initial = {"api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"}

    class Host(App[Optional[dict]]):
        def compose(self):
            yield Label("host")

    # Save with an edited key.
    app = Host()
    async with app.run_test() as pilot:
        captured: list[dict | None] = []
        screen = ProviderFormScreen(spec, initial)
        app.push_screen(screen, callback=captured.append)
        await pilot.pause()
        screen.query_one("#fld-api_key", Input).value = "sk-new"
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert captured[0] is not None
    assert captured[0]["api_key"] == "sk-new"
    assert captured[0]["model"] == "gpt-4o-mini"

    # Cancel dismisses with None.
    app = Host()
    async with app.run_test() as pilot:
        captured = []
        app.push_screen(
            ProviderFormScreen(spec, initial), callback=captured.append
        )
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert captured == [None]
