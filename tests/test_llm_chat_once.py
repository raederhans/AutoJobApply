from __future__ import annotations

import httpx
import pytest

from applypilot.llm import LLMClient


def _client_with_transport(base_url: str, handler) -> LLMClient:
    client = LLMClient(base_url, "test-model", "test-key")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_chat_once_compat_returns_content_usage_and_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"x-request-id": "req-123"},
            json={
                "id": "body-id",
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    client = _client_with_transport("https://api.openai.com/v1", handler)
    try:
        result = client.chat_once([{"role": "user", "content": "hi"}])
    finally:
        client.close()

    assert result == {
        "content": "hello",
        "provider": "openai",
        "model": "test-model",
        "request_id": "req-123",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def test_chat_once_native_gemini_uses_one_native_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "responseId": "native-456",
                "candidates": [{"content": {"parts": [{"text": "native answer"}]}}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
            },
        )

    client = _client_with_transport("https://generativelanguage.googleapis.com/v1beta/openai", handler)
    client._use_native_gemini = True
    try:
        result = client.chat_once([{"role": "user", "content": "hi"}])
    finally:
        client.close()

    assert len(seen) == 1
    assert ":generateContent" in str(seen[0].url)
    assert result["content"] == "native answer"
    assert result["provider"] == "gemini"
    assert result["request_id"] == "native-456"
    assert result["usage"] == {"promptTokenCount": 4, "candidatesTokenCount": 2}


@pytest.mark.parametrize(
    ("base_url", "response"),
    [
        ("https://api.openai.com/v1", httpx.Response(429, json={"error": "limited"})),
        ("https://api.openai.com/v1", httpx.Response(503, json={"error": "unavailable"})),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            httpx.Response(403, json={"error": "compat forbidden"}),
        ),
    ],
)
def test_chat_once_http_errors_are_single_attempt_without_fallback(base_url, response) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    client = _client_with_transport(base_url, handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.chat_once([{"role": "user", "content": "hi"}])
        assert len(requests) == 1
        assert client._use_native_gemini is False
    finally:
        client.close()


def test_chat_once_timeout_is_single_attempt() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    client = _client_with_transport("https://api.openai.com/v1", handler)
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.chat_once([{"role": "user", "content": "hi"}])
        assert attempts == 1
    finally:
        client.close()


def test_chat_keeps_compat_retry_behavior(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": "limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setenv("APPLYPILOT_LLM_MAX_RETRIES", "2")
    monkeypatch.setattr("applypilot.llm.time.sleep", lambda _: None)
    client = _client_with_transport("https://api.openai.com/v1", handler)
    try:
        assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
    finally:
        client.close()

    assert attempts == 2
