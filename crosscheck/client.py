"""
crosscheck.client
-----------------
Async OpenRouter HTTP client.
Handles retries, error mapping, and token/cost tracking.
All agents share one client instance per session via context manager.
"""

from __future__ import annotations

import asyncio
import json

import httpx

BASE_URL = "https://openrouter.ai/api/v1"

# Cost estimates per 1K tokens (blended input+output), USD
# Updated Feb 2026 — used only for session cost display, not billing.
_COST_PER_1K: dict[str, float] = {
    # Anthropic
    "anthropic/claude-opus-4.6":           0.030,
    "anthropic/claude-sonnet-4.6":         0.010,
    "anthropic/claude-opus-4.5":           0.030,
    "anthropic/claude-haiku-4.5":          0.002,
    # OpenAI
    "openai/gpt-5":                        0.015,
    "openai/gpt-5-mini":                   0.003,
    "openai/gpt-5-codex":                  0.012,
    "openai/o4-mini":                      0.005,
    "openai/gpt-4.1":                      0.010,
    "openai/gpt-4.1-mini":                 0.002,
    # Google
    "google/gemini-3.1-pro-preview":       0.008,
    "google/gemini-2.5-pro-preview":       0.007,
    "google/gemini-2.5-flash":             0.001,
    # xAI / Grok
    "x-ai/grok-4.20-beta":                0.018,
    "x-ai/grok-4":                         0.015,
    "x-ai/grok-4-fast":                    0.005,
    "x-ai/grok-4.1-fast":                  0.004,
    # Meta
    "meta-llama/llama-4-maverick":         0.002,
    # Mistral
    "mistralai/mistral-large-2411":        0.003,
    "mistralai/devstral-small":            0.001,
    "mistralai/devstral-medium":           0.002,
    # Perplexity
    "perplexity/sonar-pro":                0.003,
    # DeepSeek
    "deepseek/deepseek-r1":                0.002,
    "deepseek/deepseek-v3.2":              0.001,
    "deepseek/deepseek-chat-v3-0324":      0.001,
    # Qwen
    "qwen/qwen3.5-397b-a17b":             0.004,
    "qwen/qwen3-235b-a22b":                0.003,
    "qwen/qwen3-coder-next":              0.003,
    # Moonshot / Kimi
    "moonshotai/kimi-k2.5":               0.005,
    "moonshotai/kimi-k2":                  0.004,
    "moonshotai/kimi-k2-thinking":         0.005,
}

_RETRY_CODES   = {429, 500, 502, 503}
_MAX_RETRIES   = 3
_RETRY_DELAYS  = [5, 15, 30]   # seconds between retry attempts


class OpenRouterError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class OpenRouterClient:
    """
    Shared async HTTP client for all agents in a session.
    Must be used as an async context manager:
        async with OpenRouterClient(api_key) as client:
            text = await client.chat(model, messages)
    """

    def __init__(
        self,
        api_key:  str,
        site_url: str = "https://github.com/Nomadu27/crosscheck-ai",
    ):
        self.api_key          = api_key
        self.site_url         = site_url
        self.total_tokens:    int   = 0
        self.total_cost_usd:  float = 0.0
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> OpenRouterClient:
        self._http = httpx.AsyncClient(timeout=120)
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  self.site_url,
            "X-Title":       "crosscheck-ai",
        }

    def _track_usage(self, model: str, usage: dict) -> None:
        tokens = usage.get("total_tokens", 0)
        self.total_tokens += tokens
        cost_per_k = _COST_PER_1K.get(model, 0.005)
        self.total_cost_usd += (tokens / 1000) * cost_per_k

    async def chat(
        self,
        model:       str,
        messages:    list[dict],
        max_tokens:  int   = 4096,
        temperature: float = 0.3,
        json_mode:   bool  = False,
    ) -> str:
        """
        POST a chat completion and return the assistant message content.
        Retries on transient errors (429, 5xx, network failures).
        Raises OpenRouterError on permanent failures.
        """
        assert self._http is not None, \
            "OpenRouterClient must be used inside 'async with' block."

        body: dict = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        last_error: Exception = OpenRouterError("No attempts made")

        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._http.post(
                    f"{BASE_URL}/chat/completions",
                    headers = self._headers(),
                    content = json.dumps(body),
                )

                if resp.status_code in _RETRY_CODES and attempt < _MAX_RETRIES:
                    last_error = OpenRouterError(resp.text, resp.status_code)
                    continue

                if not resp.is_success:
                    self._raise_error(resp)

                data    = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage   = data.get("usage", {})
                self._track_usage(model, usage)
                return content

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = e
                if attempt >= _MAX_RETRIES:
                    break

        raise last_error

    def _raise_error(self, resp: httpx.Response) -> None:
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err_msg = resp.text

        messages = {
            400: f"Bad request: {err_msg}",
            401: "Invalid OpenRouter API key. Get one at https://openrouter.ai/keys",
            402: "Insufficient credits. Add credits at https://openrouter.ai/credits",
            404: f"Model not found on OpenRouter: {err_msg}",
            429: "Rate limit exceeded. Retrying automatically.",
            500: f"OpenRouter server error: {err_msg}",
        }
        msg = messages.get(resp.status_code, f"HTTP {resp.status_code}: {err_msg}")
        raise OpenRouterError(msg, resp.status_code)
