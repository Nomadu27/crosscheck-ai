"""
crosscheck.local
----------------
Phase 2: Self-hosted / on-prem mode with full Ollama + local model support.

Provides a drop-in replacement for OpenRouterClient that routes to a
local Ollama instance (or any OpenAI-compatible endpoint).

Supports:
  - Ollama   (http://localhost:11434 — standard)
  - LM Studio (http://localhost:1234/v1)
  - vLLM     (any endpoint)
  - llama.cpp server
  - Custom OpenAI-compatible endpoints

Usage:
    from crosscheck.local import LocalClient, OllamaModelRegistry

    # Auto-discover running Ollama models
    models = await OllamaModelRegistry.list_models()

    session = MultiAgentSession(
        api_key     = "local",   # unused but required by interface
        supervisors = ["llama3.3:70b"],
        analyzers   = ["qwen2.5-coder:32b", "deepseek-r1:32b", "mistral:latest"],
        coder       = "qwen2.5-coder:32b",
    )
    # Replace the default OpenRouter client factory:
    session._client_factory = LocalClient.for_ollama()
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# Local model specs (popular Ollama models)
# ---------------------------------------------------------------------------

OLLAMA_RECOMMENDED = {
    "supervisor": [
        "llama3.3:70b",
        "qwen2.5:72b",
        "mistral-large:latest",
    ],
    "analyzer": [
        "qwen2.5-coder:32b",
        "deepseek-r1:32b",
        "codellama:34b",
        "mistral:latest",
    ],
    "coder": [
        "qwen2.5-coder:32b",
        "deepseek-coder-v2:16b",
        "codellama:34b",
    ],
}


# ---------------------------------------------------------------------------
# Ollama model registry
# ---------------------------------------------------------------------------

class OllamaModelRegistry:
    """Discover and manage locally installed Ollama models."""

    @staticmethod
    async def list_models(base_url: str = "http://localhost:11434") -> list[dict]:
        """List all installed models from Ollama API."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base_url}/api/tags")
                r.raise_for_status()
                return r.json().get("models", [])
        except Exception:
            return []

    @staticmethod
    async def is_available(base_url: str = "http://localhost:11434") -> bool:
        """Check if Ollama is running."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}/api/tags")
                return r.is_success
        except Exception:
            return False

    @staticmethod
    async def pull_model(model: str, base_url: str = "http://localhost:11434") -> bool:
        """Pull a model from Ollama Hub."""
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/api/pull",
                    json={"name": model},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            data = json.loads(line)
                            if data.get("status") == "success":
                                return True
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Local client (OpenAI-compatible)
# ---------------------------------------------------------------------------

class LocalClient:
    """
    Drop-in replacement for OpenRouterClient that routes to a
    local Ollama / LM Studio / vLLM / llama.cpp endpoint.
    
    The OpenAI-compatible /v1/chat/completions endpoint is used.
    """

    def __init__(
        self,
        base_url:   str   = "http://localhost:11434/v1",
        api_key:    str   = "local",   # ignored by most local servers
        timeout:    int   = 300,
        max_retries: int  = 2,
    ):
        self.base_url     = base_url.rstrip("/")
        self.api_key      = api_key
        self.timeout      = timeout
        self.max_retries  = max_retries
        self.total_tokens: int   = 0
        self.total_cost_usd: float = 0.0
        self._http: Optional[httpx.AsyncClient] = None

    # ── Factory methods ──────────────────────────────────────────────────

    @classmethod
    def for_ollama(cls, host: str = "localhost", port: int = 11434) -> "LocalClient":
        return cls(base_url=f"http://{host}:{port}/v1")

    @classmethod
    def for_lm_studio(cls, host: str = "localhost", port: int = 1234) -> "LocalClient":
        return cls(base_url=f"http://{host}:{port}/v1")

    @classmethod
    def for_vllm(cls, host: str = "localhost", port: int = 8000) -> "LocalClient":
        return cls(base_url=f"http://{host}:{port}/v1")

    @classmethod
    def for_custom(cls, base_url: str, api_key: str = "") -> "LocalClient":
        return cls(base_url=base_url, api_key=api_key)

    # ── Context manager ──────────────────────────────────────────────────

    async def __aenter__(self) -> "LocalClient":
        self._http = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()

    # ── Chat completion ──────────────────────────────────────────────────

    async def chat(
        self,
        model:       str,
        messages:    list[dict],
        max_tokens:  int   = 4096,
        temperature: float = 0.3,
        json_mode:   bool  = False,
    ) -> str:
        assert self._http is not None, "Use LocalClient inside 'async with' block."

        body: dict = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "stream":      False,
        }
        if json_mode:
            # Ollama supports response_format for JSON
            body["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._http.post(
                    f"{self.base_url}/chat/completions",
                    headers = headers,
                    content = json.dumps(body),
                )

                if resp.status_code in {429, 500, 502, 503} and attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue

                resp.raise_for_status()
                data    = resp.json()
                content = data["choices"][0]["message"]["content"]

                usage = data.get("usage", {})
                self.total_tokens += usage.get("total_tokens", 0)
                # Local models are free — cost is $0
                return content

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"LocalClient: connection to {self.base_url} failed — {e}\n"
                        "Is Ollama running? Try: ollama serve"
                    ) from e
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError("LocalClient: max retries exceeded")


# ---------------------------------------------------------------------------
# LocalMultiAgentSession — wires LocalClient into the core pipeline
# ---------------------------------------------------------------------------

class LocalMultiAgentSession:
    """
    Full crosscheck pipeline running on local models via Ollama.
    
    Usage:
        session = LocalMultiAgentSession(
            supervisors = ["llama3.3:70b"],
            analyzers   = ["qwen2.5-coder:32b", "deepseek-r1:32b", "mistral:latest"],
            coder       = "qwen2.5-coder:32b",
        )
        result = await session.run_async("def foo(): return 1/0")
    """

    def __init__(
        self,
        supervisors:   Optional[list[str]] = None,
        analyzers:     Optional[list[str]] = None,
        coder:         Optional[str]       = None,
        ollama_url:    str                 = "http://localhost:11434/v1",
        max_rounds:    int                 = 5,
        **kwargs,
    ):
        from crosscheck.models import Task, Mode, MODE_PRESETS

        preset = MODE_PRESETS[Mode.BALANCED]
        self._supervisor_models = supervisors or OLLAMA_RECOMMENDED["supervisor"][:1]
        self._analyzer_models   = analyzers   or OLLAMA_RECOMMENDED["analyzer"][:3]
        self._coder_model       = coder       or OLLAMA_RECOMMENDED["coder"][0]
        self._ollama_url        = ollama_url
        self._max_rounds        = max_rounds
        self._kwargs            = kwargs

    async def run_async(self, content: str):
        from crosscheck.agents.analyzer   import AnalyzerPool
        from crosscheck.agents.coder      import CoderAgent
        from crosscheck.agents.supervisor import SupervisorAgent
        from crosscheck.core              import MultiAgentSession, SessionResult
        from crosscheck.models            import Task, Mode
        from crosscheck.monitor           import NoOpMonitor
        import time

        async with LocalClient(base_url=self._ollama_url) as client:
            # Patch: wire LocalClient into agents directly
            supervisor = SupervisorAgent(
                client     = client,
                models     = self._supervisor_models,
                task       = Task.CODE,
                max_rounds = self._max_rounds,
            )
            analyzer_pool = AnalyzerPool(client=client, models=self._analyzer_models)
            coder         = CoderAgent(client=client, model=self._coder_model, task=Task.CODE)

            # Mini orchestration loop (mirrors core.py)
            from crosscheck.core import Round, SessionResult
            rounds: list[Round] = []
            current = content
            prior   = None
            verdict = "MAX_ROUNDS_REACHED"
            t0      = time.monotonic()

            for round_num in range(1, self._max_rounds + 1):
                rt = time.monotonic()
                decompose = await supervisor.decompose(current, round_num)
                reports   = await analyzer_pool.analyze(
                    current, decompose.analyzer_tasks, round_num, prior
                )
                synthesis = await supervisor.synthesize(
                    [r.raw for r in reports], round_num
                )

                if synthesis.verdict == "APPROVED" or round_num == self._max_rounds:
                    verdict = "APPROVED" if synthesis.verdict == "APPROVED" else "MAX_ROUNDS_REACHED"
                    rounds.append(Round(
                        number=round_num, content_in=current, content_out=current,
                        decompose=decompose.__dict__, analyzer_reports=reports,
                        synthesis=synthesis, duration_sec=time.monotonic() - rt,
                    ))
                    break

                issues = [
                    i for r in reports for i in r.issues
                    if i.get("severity") in ("critical", "major")
                ]
                revised = await coder.revise(current, synthesis.coder_instructions, issues, round_num)
                rounds.append(Round(
                    number=round_num, content_in=current, content_out=revised,
                    decompose=decompose.__dict__, analyzer_reports=reports,
                    synthesis=synthesis, duration_sec=time.monotonic() - rt,
                ))
                current = revised
                prior   = reports

        return SessionResult(
            verdict=verdict, final_content=current, rounds=rounds,
            total_rounds=len(rounds), final_score=rounds[-1].synthesis.overall_score,
            issues_fixed=0, total_tokens=client.total_tokens,
            cost_usd=0.0, duration_sec=time.monotonic() - t0,
            anomaly_count=0, analyzer_reports=rounds[-1].analyzer_reports,
        )

    def run(self, content: str):
        return asyncio.run(self.run_async(content))
