"""Offline tests for task-tiered LLM model routing and bounded model fallback."""

from __future__ import annotations

import json

import httpx
import pytest

from applypilot import llm

_ENV = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "LLM_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MODEL_FAST",
    "LLM_MODEL_STANDARD",
    "LLM_MODEL_QUALITY",
    "LLM_MODEL_SCORE",
    "LLM_MODEL_EXTRACT",
    "APPLYPILOT_LLM_MAX_RETRIES",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "_instances", {})


def _ok(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "fixture", "choices": [{"message": {"content": f"from {model}"}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 1},
    })


def _serve(monkeypatch, responses: dict[str, httpx.Response]) -> list[str]:
    """Answer by requested model; return the models requested in order."""
    requested: list[str] = []

    def send(self, request, **_kwargs):
        model = json.loads(request.content)["model"]
        requested.append(model)
        response = responses.get(model) or _ok(model)
        response.request = request
        return response

    monkeypatch.setattr(httpx.Client, "send", send)
    return requested


def test_every_task_maps_to_a_known_tier_and_every_provider_covers_every_tier() -> None:
    assert set(llm.TASK_TIERS.values()) <= set(llm.TIERS)
    for provider, tiers in llm.PROVIDER_TIER_MODELS.items():
        assert set(tiers) == set(llm.TIERS), provider
        assert all(models for models in tiers.values()), provider


def test_provider_defaults_follow_the_task_tier(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")

    assert llm.resolve_model_route("extract").models == ("deepseek-flash",)
    assert llm.resolve_model_route("score").models == ("deepseek-v4-pro",)
    route = llm.resolve_model_route("tailor")
    assert (route.tier, route.provider, route.source) == ("quality", "deepseek", "provider_default")
    assert llm.resolve_model_route(None).tier == "standard"


def test_most_specific_model_setting_wins(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "global-model")
    assert llm.resolve_model_route("score").models == ("global-model",)

    monkeypatch.setenv("LLM_MODEL_STANDARD", "tier-a, tier-b ,tier-a")
    route = llm.resolve_model_route("score")
    assert (route.models, route.source) == (("tier-a", "tier-b"), "LLM_MODEL_STANDARD")
    assert llm.resolve_model_route("extract").models == ("global-model",)

    monkeypatch.setenv("LLM_MODEL_SCORE", "task-model")
    assert llm.resolve_model_route("score").models == ("task-model",)
    assert llm.resolve_model_route("judge").models == ("tier-a", "tier-b")


def test_unknown_task_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    with pytest.raises(ValueError, match="unknown LLM task"):
        llm.resolve_model_route("summarize")


def test_legacy_detect_provider_keeps_the_standard_first_model(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")

    base_url, model, key = llm._detect_provider()
    assert model == llm.DEFAULT_GEMINI_MODEL == "gemini-3.8-flash"
    assert key == "test-only" and base_url.endswith("/openai")
    assert llm._detect_provider("extract")[1] == "gemini-3.5-flash-lite"


def test_retired_model_falls_back_once_and_stays_on_the_fallback(monkeypatch) -> None:
    requested = _serve(monkeypatch, {"old": httpx.Response(404, json={"error": "model not found"})})
    client = llm.LLMClient("https://fixture.example/v1", "old", "k", fallback_models=("new",))

    assert client.chat([{"role": "user", "content": "hi"}]) == "from new"
    assert client.model == "new"
    assert client.chat([{"role": "user", "content": "again"}]) == "from new"
    assert requested == ["old", "new", "new"]


def test_unavailable_model_without_a_chain_raises(monkeypatch) -> None:
    _serve(monkeypatch, {"old": httpx.Response(404, json={"error": "model not found"})})
    client = llm.LLMClient("https://fixture.example/v1", "old", "k")

    with pytest.raises(httpx.HTTPStatusError):
        client.chat([{"role": "user", "content": "hi"}])
    assert client.model == "old"


@pytest.mark.parametrize(
    ("body", "falls_back"),
    [
        ({"error": {"message": "The model `old` does not exist"}}, True),
        ({"error": {"message": "max_tokens is too large"}}, False),
    ],
)
def test_bad_request_falls_back_only_when_it_names_an_unavailable_model(monkeypatch, body, falls_back) -> None:
    _serve(monkeypatch, {"old": httpx.Response(400, json=body)})
    client = llm.LLMClient("https://fixture.example/v1", "old", "k", fallback_models=("new",))

    if falls_back:
        assert client.chat([{"role": "user", "content": "hi"}]) == "from new"
    else:
        with pytest.raises(httpx.HTTPStatusError):
            client.chat([{"role": "user", "content": "hi"}])
        assert client.model == "old"


def test_quota_exhausted_after_retries_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("APPLYPILOT_LLM_MAX_RETRIES", "1")
    requested = _serve(monkeypatch, {"old": httpx.Response(429, json={"error": "quota"})})
    client = llm.LLMClient("https://fixture.example/v1", "old", "k", fallback_models=("new",))

    assert client.chat([{"role": "user", "content": "hi"}]) == "from new"
    assert requested == ["old", "new"]


def test_chat_once_never_falls_back(monkeypatch) -> None:
    _serve(monkeypatch, {"old": httpx.Response(404, json={"error": "model not found"})})
    client = llm.LLMClient("https://fixture.example/v1", "old", "k", fallback_models=("new",))

    with pytest.raises(httpx.HTTPStatusError):
        client.chat_once([{"role": "user", "content": "hi"}])
    assert client.model == "old"


def test_get_client_shares_one_client_per_model_chain(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")

    assert llm.get_client(task="score") is llm.get_client(task="judge") is llm.get_client()
    extract = llm.get_client(task="extract")
    assert extract is not llm.get_client(task="score")
    assert extract.model == "gemini-3.5-flash-lite"
    assert extract._fallback_models == ["gemini-3.1-flash-lite"]


def test_create_client_returns_a_fresh_client_with_the_task_chain(monkeypatch) -> None:
    monkeypatch.setenv("LLM_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("LLM_MODEL_QUALITY", "big,small")

    first = llm.create_client(task="writing")
    second = llm.create_client(task="writing")
    assert first is not second
    assert (first.model, first._fallback_models) == ("big", ["small"])
    first.close()
    second.close()
