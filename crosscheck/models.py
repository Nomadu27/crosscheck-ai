"""
crosscheck.models
-----------------
Model registry for all supported OpenRouter models.

Design decisions (v0.2.0):
- ANY capable model can be Coder. The user picks freely.
  Anthropic is the recommended default in presets, not a hard requirement.
- Tier.CODER is assigned to every model with strong code-generation ability.
- origin: "western" | "chinese" — display/filter only, never restricts.
- No is_free field — a single OpenRouter API key accesses everything.

Verify IDs against the live API any time:
    crosscheck models fetch-latest
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    SUPERVISOR = "supervisor"
    ANALYZER   = "analyzer"
    CODER      = "coder"


class Task(str, Enum):
    CODE     = "code"
    PLAN     = "plan"
    TEXT     = "text"
    APPSTORE = "appstore"


class Mode(str, Enum):
    FAST     = "fast"
    BALANCED = "balanced"
    QUALITY  = "quality"


@dataclass
class ModelSpec:
    model_id:     str
    display_name: str
    tiers:        list[Tier]
    tasks:        list[Task]
    context_k:    int = 128
    provider:     str = ""
    origin:       str = "western"   # "western" | "chinese"
    notes:        str = ""


# ---------------------------------------------------------------------------
# Master registry — Mar 2026
# Verified against OpenRouter /api/v1/models on 2026-03-28 (347 models).
# ANY capable model tagged Tier.CODER. Anthropic is default, not mandatory.
# ---------------------------------------------------------------------------
REGISTRY: list[ModelSpec] = [

    # =====================================================================
    # WESTERN MODELS
    # =====================================================================

    # -- Anthropic -- recommended coder + supervisor ---------------------
    ModelSpec(
        model_id     = "anthropic/claude-opus-4.6",
        display_name = "Claude Opus 4.6",
        tiers        = [Tier.SUPERVISOR, Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 200,
        provider     = "Anthropic",
        origin       = "western",
        notes        = "Latest flagship — strongest coding & long-running tasks",
    ),
    ModelSpec(
        model_id     = "anthropic/claude-sonnet-4.6",
        display_name = "Claude Sonnet 4.6",
        tiers        = [Tier.SUPERVISOR, Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 200,
        provider     = "Anthropic",
        origin       = "western",
        notes        = "Best speed/quality ratio — balanced supervisor & coder",
    ),
    ModelSpec(
        model_id     = "anthropic/claude-opus-4.5",
        display_name = "Claude Opus 4.5",
        tiers        = [Tier.SUPERVISOR, Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 200,
        provider     = "Anthropic",
        origin       = "western",
        notes        = "Previous-gen flagship — still excellent",
    ),
    ModelSpec(
        model_id     = "anthropic/claude-haiku-4.5",
        display_name = "Claude Haiku 4.5",
        tiers        = [Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 200,
        provider     = "Anthropic",
        origin       = "western",
        notes        = "Fastest Anthropic — budget coder & analyzer",
    ),

    # -- OpenAI ----------------------------------------------------------
    ModelSpec(
        model_id     = "openai/gpt-5",
        display_name = "GPT-5",
        tiers        = [Tier.SUPERVISOR, Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Latest OpenAI flagship",
    ),
    ModelSpec(
        model_id     = "openai/gpt-5-mini",
        display_name = "GPT-5 Mini",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Lightweight fast analyzer",
    ),
    ModelSpec(
        model_id     = "openai/gpt-5-codex",
        display_name = "GPT-5 Codex",
        tiers        = [Tier.CODER, Tier.ANALYZER],
        tasks        = [Task.CODE],
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Code-specialized — strong agentic coding",
    ),
    ModelSpec(
        model_id     = "openai/o4-mini",
        display_name = "o4-mini",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.CODE, Task.PLAN],
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Latest reasoning model — strong for analysis",
    ),
    ModelSpec(
        model_id     = "openai/gpt-4.1",
        display_name = "GPT-4.1",
        tiers        = [Tier.SUPERVISOR, Tier.CODER, Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Proven workhorse — still solid",
    ),
    ModelSpec(
        model_id     = "openai/gpt-4.1-mini",
        display_name = "GPT-4.1 Mini",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 128,
        provider     = "OpenAI",
        origin       = "western",
        notes        = "Budget-friendly fast analyzer",
    ),

    # -- Google ----------------------------------------------------------
    ModelSpec(
        model_id     = "google/gemini-3.1-pro-preview",
        display_name = "Gemini 3.1 Pro",
        tiers        = [Tier.ANALYZER, Tier.SUPERVISOR, Tier.CODER],
        tasks        = list(Task),
        context_k    = 1000,
        provider     = "Google",
        origin       = "western",
        notes        = "Latest Gemini — 1M context frontier reasoning",
    ),
    ModelSpec(
        model_id     = "google/gemini-2.5-pro-preview",
        display_name = "Gemini 2.5 Pro",
        tiers        = [Tier.ANALYZER, Tier.SUPERVISOR, Tier.CODER],
        tasks        = list(Task),
        context_k    = 1000,
        provider     = "Google",
        origin       = "western",
        notes        = "1M context — excellent for large codebases",
    ),
    ModelSpec(
        model_id     = "google/gemini-2.5-flash",
        display_name = "Gemini 2.5 Flash",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 1000,
        provider     = "Google",
        origin       = "western",
        notes        = "Fast & cheap with 1M context",
    ),

    # -- xAI / Grok ------------------------------------------------------
    ModelSpec(
        model_id     = "x-ai/grok-4.20-beta",
        display_name = "Grok 4.20",
        tiers        = [Tier.SUPERVISOR, Tier.ANALYZER, Tier.CODER],
        tasks        = list(Task),
        context_k    = 256,
        provider     = "xAI",
        origin       = "western",
        notes        = "Latest Grok — multi-agent support",
    ),
    ModelSpec(
        model_id     = "x-ai/grok-4",
        display_name = "Grok 4",
        tiers        = [Tier.SUPERVISOR, Tier.ANALYZER, Tier.CODER],
        tasks        = list(Task),
        context_k    = 256,
        provider     = "xAI",
        origin       = "western",
        notes        = "Top reasoning — strong supervisor & coder",
    ),
    ModelSpec(
        model_id     = "x-ai/grok-4-fast",
        display_name = "Grok 4 Fast",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 256,
        provider     = "xAI",
        origin       = "western",
        notes        = "256K ctx — excellent cost/performance ratio",
    ),
    ModelSpec(
        model_id     = "x-ai/grok-4.1-fast",
        display_name = "Grok 4.1 Fast",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 2000,
        provider     = "xAI",
        origin       = "western",
        notes        = "2M ctx — best agentic tool-calling",
    ),

    # -- Meta ------------------------------------------------------------
    ModelSpec(
        model_id     = "meta-llama/llama-4-maverick",
        display_name = "Llama 4 Maverick",
        tiers        = [Tier.ANALYZER],
        tasks        = list(Task),
        context_k    = 1000,
        provider     = "Meta",
        origin       = "western",
        notes        = "Best open-weight Western model, 1M ctx",
    ),

    # -- Mistral ---------------------------------------------------------
    ModelSpec(
        model_id     = "mistralai/mistral-large-2411",
        display_name = "Mistral Large 2411",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.CODE, Task.TEXT, Task.PLAN],
        context_k    = 128,
        provider     = "Mistral",
        origin       = "western",
        notes        = "Solid European flagship",
    ),
    ModelSpec(
        model_id     = "mistralai/devstral-small",
        display_name = "Devstral Small",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE],
        context_k    = 128,
        provider     = "Mistral",
        origin       = "western",
        notes        = "Code-specialized — strong SWE-bench, lean coder option",
    ),
    ModelSpec(
        model_id     = "mistralai/devstral-medium",
        display_name = "Devstral Medium",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE],
        context_k    = 128,
        provider     = "Mistral",
        origin       = "western",
        notes        = "Mid-tier code model — good balance",
    ),

    # -- Perplexity ------------------------------------------------------
    ModelSpec(
        model_id     = "perplexity/sonar-pro",
        display_name = "Sonar Pro",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.APPSTORE, Task.TEXT, Task.PLAN],
        context_k    = 127,
        provider     = "Perplexity",
        origin       = "western",
        notes        = "Web-aware — best for App Store & live docs",
    ),

    # =====================================================================
    # CHINESE MODELS — Best-in-class only
    # =====================================================================

    # -- DeepSeek --------------------------------------------------------
    ModelSpec(
        model_id     = "deepseek/deepseek-r1",
        display_name = "DeepSeek R1",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.CODE, Task.PLAN],
        context_k    = 64,
        provider     = "DeepSeek",
        origin       = "chinese",
        notes        = "Best open reasoning/CoT model globally",
    ),
    ModelSpec(
        model_id     = "deepseek/deepseek-v3.2",
        display_name = "DeepSeek V3.2",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE, Task.PLAN, Task.TEXT],
        context_k    = 64,
        provider     = "DeepSeek",
        origin       = "chinese",
        notes        = "Latest DeepSeek — major upgrade over V3.x",
    ),
    ModelSpec(
        model_id     = "deepseek/deepseek-chat-v3-0324",
        display_name = "DeepSeek V3.x",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE, Task.PLAN, Task.TEXT],
        context_k    = 64,
        provider     = "DeepSeek",
        origin       = "chinese",
        notes        = "Proven strong coder — still solid",
    ),

    # -- Qwen ------------------------------------------------------------
    ModelSpec(
        model_id     = "qwen/qwen3.5-397b-a17b",
        display_name = "Qwen 3.5 397B",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE, Task.PLAN, Task.TEXT],
        context_k    = 128,
        provider     = "Qwen",
        origin       = "chinese",
        notes        = "Latest Qwen flagship — 397B MoE",
    ),
    ModelSpec(
        model_id     = "qwen/qwen3-235b-a22b",
        display_name = "Qwen3 235B",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.CODE, Task.PLAN, Task.TEXT],
        context_k    = 128,
        provider     = "Qwen",
        origin       = "chinese",
        notes        = "Strong MoE — proven quality",
    ),
    ModelSpec(
        model_id     = "qwen/qwen3-coder-next",
        display_name = "Qwen3 Coder Next",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE],
        context_k    = 128,
        provider     = "Qwen",
        origin       = "chinese",
        notes        = "Latest dedicated code model — replaces qwen2.5-coder",
    ),

    # -- Moonshot / Kimi -------------------------------------------------
    ModelSpec(
        model_id     = "moonshotai/kimi-k2.5",
        display_name = "Kimi K2.5",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE, Task.PLAN],
        context_k    = 128,
        provider     = "Moonshot",
        origin       = "chinese",
        notes        = "Latest Kimi — upgrade over K2",
    ),
    ModelSpec(
        model_id     = "moonshotai/kimi-k2",
        display_name = "Kimi K2",
        tiers        = [Tier.ANALYZER, Tier.CODER],
        tasks        = [Task.CODE, Task.PLAN],
        context_k    = 128,
        provider     = "Moonshot",
        origin       = "chinese",
        notes        = "1T params, 32B active — strong agentic & coding",
    ),
    ModelSpec(
        model_id     = "moonshotai/kimi-k2-thinking",
        display_name = "Kimi K2 Thinking",
        tiers        = [Tier.ANALYZER],
        tasks        = [Task.CODE, Task.PLAN],
        context_k    = 128,
        provider     = "Moonshot",
        origin       = "chinese",
        notes        = "Deep reasoning variant of Kimi K2",
    ),
]

# ---------------------------------------------------------------------------
# Mode presets
# Coder defaults to Anthropic but users override freely via --interactive,
# --coder flag, or named profiles.
# ---------------------------------------------------------------------------
MODE_PRESETS: dict[Mode, dict] = {
    Mode.FAST: {
        "supervisors": ["anthropic/claude-sonnet-4.6"],
        "analyzers": [
            "google/gemini-2.5-flash",
            "deepseek/deepseek-chat-v3-0324",
            "x-ai/grok-4-fast",
        ],
        "coder": "anthropic/claude-haiku-4.5",
    },
    Mode.BALANCED: {
        "supervisors": ["anthropic/claude-opus-4.6"],
        "analyzers": [
            "deepseek/deepseek-v3.2",
            "moonshotai/kimi-k2.5",
            "x-ai/grok-4-fast",
            "qwen/qwen3-235b-a22b",
        ],
        "coder": "anthropic/claude-sonnet-4.6",
    },
    Mode.QUALITY: {
        "supervisors": [
            "anthropic/claude-opus-4.6",
            "openai/gpt-5",
        ],
        "analyzers": [
            "deepseek/deepseek-v3.2",
            "moonshotai/kimi-k2.5",
            "x-ai/grok-4",
            "google/gemini-3.1-pro-preview",
        ],
        "coder": "anthropic/claude-opus-4.6",
    },
}

# Task-best presets
TASK_BEST: dict[Task, dict] = {
    Task.CODE: {
        "supervisors": ["anthropic/claude-opus-4.6"],
        "analyzers": [
            "qwen/qwen3-coder-next",
            "deepseek/deepseek-r1",
            "moonshotai/kimi-k2.5",
            "mistralai/devstral-small",
        ],
        "coder": "anthropic/claude-opus-4.6",
    },
    Task.PLAN: {
        "supervisors": ["anthropic/claude-opus-4.6"],
        "analyzers": [
            "x-ai/grok-4",
            "moonshotai/kimi-k2.5",
            "deepseek/deepseek-r1",
            "google/gemini-3.1-pro-preview",
        ],
        "coder": "anthropic/claude-sonnet-4.6",
    },
    Task.TEXT: {
        "supervisors": ["anthropic/claude-opus-4.6"],
        "analyzers": [
            "x-ai/grok-4.1-fast",
            "google/gemini-3.1-pro-preview",
            "qwen/qwen3.5-397b-a17b",
            "deepseek/deepseek-v3.2",
        ],
        "coder": "anthropic/claude-sonnet-4.6",
    },
    Task.APPSTORE: {
        "supervisors": ["anthropic/claude-opus-4.6"],
        "analyzers": [
            "x-ai/grok-4.1-fast",
            "google/gemini-3.1-pro-preview",
            "perplexity/sonar-pro",
            "openai/gpt-5",
        ],
        "coder": "anthropic/claude-sonnet-4.6",
    },
}

# Observer mode: 2 supervisors watching an external coding tool/session
# These watch silently and flag bugs, plan drift, and anomalies in real-time.
OBSERVER_DEFAULT_SUPERVISORS = [
    "anthropic/claude-opus-4.6",
    "x-ai/grok-4",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_by_id(model_id: str) -> Optional[ModelSpec]:
    """Return ModelSpec by exact OpenRouter ID, or None."""
    return next((m for m in REGISTRY if m.model_id == model_id), None)


def get_by_tier(tier: Tier) -> list[ModelSpec]:
    """Return all models that can serve a given tier."""
    return [m for m in REGISTRY if tier in m.tiers]


def get_by_origin(origin: str) -> list[ModelSpec]:
    """Return models by origin. 'all' returns everything."""
    if origin == "all":
        return list(REGISTRY)
    return [m for m in REGISTRY if m.origin == origin]


def validate_model_ids(ids: list[str]) -> tuple[list[str], list[str]]:
    """
    Split ids into (known_in_registry, unknown).
    Unknown IDs are still allowed — user may pass a live OpenRouter model
    not yet in the local registry. A warning is shown, session continues.
    """
    known   = [mid for mid in ids if get_by_id(mid) is not None]
    unknown = [mid for mid in ids if get_by_id(mid) is None]
    return known, unknown


async def fetch_latest(api_key: str) -> list[dict]:
    """Fetch live model list from OpenRouter. Returns [] on any error."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer":  "https://github.com/Nomadu27/crosscheck-ai",
                    "X-Title":       "crosscheck-ai",
                },
            )
            r.raise_for_status()
            return r.json().get("data", [])
    except Exception:
        return []
