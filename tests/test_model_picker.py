"""Tests for the interactive model picker."""

import pytest

from pico_ai.openrouter import OpenRouterProvider
from pico_tui.model_picker import format_model_option, sort_models


def _m(id: str, name: str, is_free: bool) -> dict:
    return {"id": id, "name": name, "is_free": is_free}


def test_sort_models_free_first_then_alphabetical():
    models = [
        _m("z/paid", "Zeta Paid", False),
        _m("b/free", "Beta Free", True),
        _m("a/paid", "Alpha Paid", False),
        _m("a/free", "Alpha Free", True),
    ]
    result = sort_models(models)
    assert [m["id"] for m in result] == ["a/free", "b/free", "a/paid", "z/paid"]


def test_format_model_option_free_and_current():
    text = format_model_option(_m("a/free", "Alpha Free", True), current="a/free")
    assert "Alpha Free" in text
    assert "a/free" in text
    assert "FREE" in text
    assert "current" in text


def test_format_model_option_paid():
    text = format_model_option(_m("a/paid", "Alpha Paid", False))
    assert "FREE" not in text
    assert "Alpha Paid" in text


def test_format_model_option_single_line():
    import re

    from pico_tui.modal import MAX_ROW_WIDTH

    text = format_model_option(
        _m("vendor/" + "y" * 60, " ".join(["Model"] * 30), True),
        current="vendor/" + "y" * 60,
    )
    plain = re.sub(r"\[.*?\]", "", text)
    assert "\n" not in plain
    assert len(plain) <= MAX_ROW_WIDTH


@pytest.mark.asyncio
async def test_list_models_parses_and_flags_free():
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "id": "vendor/free-model",
                        "name": "Free Model",
                        "pricing": {"prompt": "0", "completion": "0"},
                    },
                    {
                        "id": "vendor/paid-model",
                        "name": "Paid Model",
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                    },
                    {
                        "id": "vendor/no-pricing",
                        "name": "No Pricing",
                    },
                ]
            }

    class FakeClient:
        async def get(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

        async def aclose(self) -> None:
            pass

    provider = OpenRouterProvider(api_key="test", client=FakeClient())
    models = await provider.list_models()

    assert len(models) == 3
    by_id = {m["id"]: m for m in models}
    assert by_id["vendor/free-model"]["is_free"] is True
    assert by_id["vendor/paid-model"]["is_free"] is False
    assert by_id["vendor/no-pricing"]["is_free"] is False
