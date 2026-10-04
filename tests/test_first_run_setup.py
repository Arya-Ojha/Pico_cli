"""First-run onboarding: missing provider config auto-opens setup."""

import pytest

from pico_sdk.config import Settings
from pico_sdk.providers import create_provider, missing_required
from pico_tui.app import PicoApp, _SessionManager
from pico_tui.provider_picker import ProviderPickerScreen

from conftest import FakeProvider, make_session


@pytest.fixture
def fresh_settings(monkeypatch):
    """Settings as on a brand-new machine: no keys anywhere."""
    for var in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = Settings()
    assert missing_required(settings.provider, settings), "expected missing config"
    return settings


def _app(tmp_path, settings, auto_setup=False):
    session = make_session(
        FakeProvider([]), tmp_path, settings=settings, load_skills=False
    )
    return PicoApp(_SessionManager(session), auto_setup=auto_setup)


async def test_missing_config_auto_opens_provider_picker(tmp_path, fresh_settings):
    app = _app(tmp_path, fresh_settings, auto_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ProviderPickerScreen)


async def test_auto_setup_off_by_default(tmp_path, fresh_settings):
    app = _app(tmp_path, fresh_settings)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, ProviderPickerScreen)


async def test_configured_never_auto_opens(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings = Settings()
    assert not missing_required(settings.provider, settings)
    session = make_session(
        create_provider(settings), tmp_path, settings=settings, load_skills=False
    )
    app = PicoApp(_SessionManager(session), auto_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, ProviderPickerScreen)


def test_main_requests_auto_setup():
    import inspect

    from pico_tui.app import main

    assert "auto_setup=True" in inspect.getsource(main)
