"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini
  OPENAI_API_KEY  -> OpenAI
  DEEPSEEK_API_KEY -> DeepSeek OpenAI-compatible API
  LLM_URL         -> Local llama.cpp / Ollama compatible endpoint

Each task (see TASK_TIERS) runs on a tier: fast, standard or quality. A tier
resolves to an ordered model chain, most specific setting first:

  LLM_MODEL_<TASK>  ->  LLM_MODEL_<TIER>  ->  LLM_MODEL  ->  provider default

Values may list several comma-separated models. When the provider reports that
a model is unavailable (unknown, retired, or quota exhausted after retries),
chat() moves to the next model in the chain for the rest of the process and
``client.model`` names the model that actually answered.
"""

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

TIERS = ("fast", "standard", "quality")

# Task -> tier. Extraction is high-volume and structured; judgment tasks use the
# standard tier; candidate-facing writing uses the quality tier.
TASK_TIERS = {
    "extract": "fast",
    "score": "standard",
    "judge": "standard",
    "interview": "standard",
    "tailor": "quality",
    "cover": "quality",
    "answers": "quality",
    "writing": "quality",
    "career": "quality",
}

# Provider defaults per tier, checked against the providers' model pages on
# 2026-10-25. gemini-2.0-flash was shut down on 2026-06-01; gemini-4-argon has
# no free tier, so it is opt-in through LLM_MODEL_QUALITY rather than a default.
PROVIDER_TIER_MODELS = {
    "gemini": {
        "fast": ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"),
        "standard": ("gemini-3.8-flash", "gemini-3.6-flash"),
        "quality": ("gemini-3.8-flash", "gemini-3.6-flash"),
    },
    "openai": {
        "fast": ("gpt-4o-mini",),
        "standard": ("gpt-4o-mini",),
        "quality": ("gpt-4o-mini",),
    },
    "deepseek": {
        "fast": ("deepseek-flash",),
        "standard": ("deepseek-v4-pro",),
        "quality": ("deepseek-v4-pro",),
    },
    "local": {
        "fast": ("local-model",),
        "standard": ("local-model",),
        "quality": ("local-model",),
    },
}

# Defaults shown by `doctor` and offered by the init wizard.
DEFAULT_GEMINI_MODEL = PROVIDER_TIER_MODELS["gemini"]["standard"][0]
DEFAULT_OPENAI_MODEL = PROVIDER_TIER_MODELS["openai"]["standard"][0]
DEFAULT_DEEPSEEK_MODEL = PROVIDER_TIER_MODELS["deepseek"]["standard"][0]

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRoute:
    """The resolved model chain for one task."""

    task: str
    tier: str
    provider: str
    models: tuple[str, ...]
    source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "task": self.task,
            "tier": self.tier,
            "provider": self.provider,
            "models": list(self.models),
            "source": self.source,
        }


def _provider_credentials(environ: Mapping[str, str]) -> tuple[str, str, str]:
    """Return (provider, base_url, api_key) from environment variables."""
    local_url = environ.get("LLM_URL", "")
    if environ.get("GEMINI_API_KEY") and not local_url:
        return "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", environ["GEMINI_API_KEY"]
    if environ.get("OPENAI_API_KEY") and not local_url:
        return "openai", "https://api.openai.com/v1", environ["OPENAI_API_KEY"]
    if environ.get("DEEPSEEK_API_KEY") and not local_url:
        base_url = environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        return "deepseek", base_url, environ["DEEPSEEK_API_KEY"]
    if local_url:
        return "local", local_url.rstrip("/"), environ.get("LLM_API_KEY", "")
    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, or LLM_URL in your environment."
    )


def _model_chain(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))


def resolve_model_route(task: str | None = None, *, environ: Mapping[str, str] | None = None) -> ModelRoute:
    """Resolve the model chain for ``task`` (None means the standard tier)."""
    environment = os.environ if environ is None else environ
    name = (task or "default").strip().casefold()
    if task is None:
        tier = "standard"
    elif name in TASK_TIERS:
        tier = TASK_TIERS[name]
    else:
        raise ValueError(f"unknown LLM task {task!r}; expected one of {', '.join(sorted(TASK_TIERS))}")
    provider, _base_url, _api_key = _provider_credentials(environment)
    variables = [f"LLM_MODEL_{tier.upper()}", "LLM_MODEL"]
    if task is not None:
        variables.insert(0, f"LLM_MODEL_{name.upper()}")
    for variable in variables:
        models = _model_chain(environment.get(variable, ""))
        if models:
            return ModelRoute(name, tier, provider, models, variable)
    return ModelRoute(name, tier, provider, PROVIDER_TIER_MODELS[provider][tier], "provider_default")


def _detect_provider(task: str | None = None) -> tuple[str, str, str]:
    """Return (base_url, first model, api_key) for ``task``.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.
    """
    _provider, base_url, api_key = _provider_credentials(os.environ)
    return base_url, resolve_model_route(task).models[0], api_key


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.

    ``fallback_models`` are tried in order by chat() when the provider reports
    the current model unavailable; the switch also lasts for the process.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        fallback_models: tuple[str, ...] = (),
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._fallback_models: list[str] = [item for item in fallback_models if item != model]
        request_timeout = float(os.environ.get("APPLYPILOT_LLM_TIMEOUT_SECONDS", str(_TIMEOUT)))
        self._client = httpx.Client(timeout=request_timeout)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)
        self.last_response_meta: dict = {}

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        *,
        include_metadata: bool = False,
    ) -> str | tuple[str, dict]:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        if include_metadata:
            usage = data.get("usageMetadata")
            meta = {
                "request_id": data.get("responseId"),
                "usage": usage if isinstance(usage, dict) else None,
            }
            self.last_response_meta = {**self.last_response_meta, **meta}
            return content, meta
        return content

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
        thinking: dict | None = None,
        reasoning_effort: str | None = None,
        *,
        include_metadata: bool = False,
    ) -> str | tuple[str, dict]:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        if thinking and self.model.casefold().startswith("deepseek"):
            payload["thinking"] = thinking
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        content = self._handle_compat_response(resp)
        if include_metadata:
            data = resp.json()
            headers = resp.headers
            usage = data.get("usage")
            meta = {
                "request_id": headers.get("x-request-id") or headers.get("request-id") or data.get("id"),
                "usage": usage if isinstance(usage, dict) else None,
            }
            self.last_response_meta = {**self.last_response_meta, **meta}
            return content, meta
        return content

    def _handle_compat_response(self, resp: httpx.Response) -> str:
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        usage = data.get("usage") or {}
        self.last_response_meta = {
            "finish_reason": choice.get("finish_reason"),
            "content_chars": len(content),
            "reasoning_chars": len(reasoning),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        return content

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        thinking: dict | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        while True:
            try:
                return self._chat_current_model(
                    messages,
                    temperature,
                    max_tokens,
                    response_format=response_format,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                )
            except httpx.HTTPStatusError as exc:
                if not self._fallback_models or not _model_unavailable(exc.response):
                    raise
                unavailable = self.model
                self.model = self._fallback_models.pop(0)
                self._use_native_gemini = False
                log.warning(
                    "LLM model '%s' is unavailable (HTTP %s). Falling back to '%s' for this process.",
                    unavailable, exc.response.status_code, self.model,
                )

    def _chat_current_model(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: dict | None,
        thinking: dict | None,
        reasoning_effort: str | None,
    ) -> str:
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        max_retries = max(1, int(os.environ.get("APPLYPILOT_LLM_MAX_RETRIES", str(_MAX_RETRIES))))
        for attempt in range(max_retries):
            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    return self._chat_native_gemini(messages, temperature, max_tokens)

                return self._chat_compat(
                    messages,
                    temperature,
                    max_tokens,
                    response_format=response_format,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                )

            except _GeminiCompatForbidden:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.)",
                    self.model,
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens)
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if resp.status_code in (429, 503) and attempt < max_retries - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s). Waiting %ds before retry %d/%d. "
                        "Tip: Gemini free tier = 15 RPM. Consider a paid account "
                        "or switching to a local model.",
                        resp.status_code, wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < max_retries - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def chat_once(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        thinking: dict | None = None,
        reasoning_effort: str | None = None,
    ) -> dict:
        """Send one chat request and return content with provider metadata.

        This entry point deliberately makes a single HTTP request: it does not
        retry rate limits/timeouts or switch from Gemini compat to native.
        """
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        if self._use_native_gemini:
            content, meta = self._chat_native_gemini(
                messages, temperature, max_tokens, include_metadata=True
            )
        else:
            try:
                content, meta = self._chat_compat(
                    messages,
                    temperature,
                    max_tokens,
                    response_format=response_format,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                    include_metadata=True,
                )
            except _GeminiCompatForbidden as exc:
                # Preserve the HTTP error contract without triggering chat()'s
                # stateful switch to native Gemini.
                exc.response.raise_for_status()

        return {
            "content": content,
            "provider": self._provider_name(),
            "model": self.model,
            "request_id": meta["request_id"],
            "usage": meta["usage"],
        }

    def _provider_name(self) -> str:
        if self._is_gemini:
            return "gemini"
        lowered = self.base_url.casefold()
        if "deepseek" in lowered:
            return "deepseek"
        if "openai.com" in lowered:
            return "openai"
        return "local" if "localhost" in lowered or "127.0.0.1" in lowered else "openai-compatible"

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


_UNAVAILABLE_MODEL_MARKERS = (
    "not found",
    "does not exist",
    "not supported",
    "deprecated",
    "decommissioned",
    "no longer available",
    "invalid model",
)


def _model_unavailable(resp: httpx.Response) -> bool:
    """Return whether ``resp`` says this model cannot serve requests now.

    chat() retries 429 before raising it, so a raised 429 means the model's
    quota is exhausted rather than a momentary burst.
    """
    if resp.status_code in (404, 410, 429):
        return True
    if resp.status_code != 400:
        return False
    try:
        text = resp.text.casefold()
    except httpx.ResponseNotRead:
        return False
    return "model" in text and any(marker in text for marker in _UNAVAILABLE_MODEL_MARKERS)


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instances: dict[tuple[str, tuple[str, ...]], LLMClient] = {}


def create_client(task: str | None = None) -> LLMClient:
    """Create a new client for ``task`` from the current environment.

    Callers that may run under several workspaces in one process use this
    instead of get_client() and close the client themselves.
    """
    base_url, model, api_key = _detect_provider(task)
    route = resolve_model_route(task)
    fallbacks = route.models[1:] if route.models[0] == model else ()
    return LLMClient(base_url, model, api_key, fallback_models=fallbacks)


def get_client(task: str | None = None) -> LLMClient:
    """Return the process-wide client for ``task``'s model chain.

    Tasks that resolve to the same chain share one client, so a fallback
    learned by one of them applies to all.
    """
    route = resolve_model_route(task)
    _provider, base_url, _api_key = _provider_credentials(os.environ)
    key = (base_url, route.models)
    if key not in _instances:
        log.info("LLM provider: %s  task: %s  models: %s", base_url, route.task, ", ".join(route.models))
        _instances[key] = create_client(task)
    return _instances[key]
