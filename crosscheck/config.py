"""
crosscheck.config
-----------------
Config loader. Priority (highest to lowest):
  1. CLI flags (applied in cli.py after load_config)
  2. Environment variables (CROSSCHECK_*)
  3. crosscheck.toml in cwd or ~/.config/crosscheck/crosscheck.toml
  4. Built-in defaults

Profile support:
  Define named model sets in crosscheck.toml as [profile.NAME] sections.
  Activate with: crosscheck review file.py --profile NAME
  or:            export CROSSCHECK_PROFILE=NAME

Observer config:
  observer_plan — optional spec/plan file path to detect drift against
  observer_extensions — comma-separated file extensions to watch
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from crosscheck.models import OBSERVER_DEFAULT_SUPERVISORS, Mode, Task

_DEFAULT_CONFIG_PATHS = [
    Path("crosscheck.toml"),
    Path.home() / ".config" / "crosscheck" / "crosscheck.toml",
]


@dataclass
class ProfileConfig:
    """A named model configuration profile."""
    name:        str
    supervisors: list[str] = field(default_factory=list)
    analyzers:   list[str] = field(default_factory=list)
    coder:       str       = ""
    notes:       str       = ""


@dataclass
class CrosscheckConfig:
    # Required
    api_key:          str  = ""

    # Session defaults
    mode:             Mode = Mode.BALANCED
    review_type:      Task = Task.CODE
    max_rounds:       int  = 5

    # Model overrides (empty = use mode/profile/task preset)
    supervisors:      list[str] = field(default_factory=list)
    analyzers:        list[str] = field(default_factory=list)
    coder:            str       = ""

    # Active profile name (empty = no profile active)
    profile:          str  = ""

    # Named profiles loaded from [profile.*] TOML sections
    profiles:         dict[str, ProfileConfig] = field(default_factory=dict)

    # InsAIts monitoring
    monitor:          bool = False
    insaits_api_key:  str  = ""
    monitor_anchor:   str  = ""

    # Output
    output_format:    str  = "terminal"   # terminal | json | markdown
    output_file:      str  = ""

    # Observer mode settings
    observer_supervisors:  list[str] = field(default_factory=list)
    observer_analyzers:    list[str] = field(default_factory=list)
    observer_plan:         str       = ""    # path to plan file or inline plan text
    observer_extensions:   list[str] = field(default_factory=list)
    observer_debounce:     float     = 0.5  # seconds

    # Misc
    site_url:         str  = "https://github.com/Nomadu27/crosscheck-ai"

    def resolve_models(self) -> tuple[list[str], list[str], str]:
        """
        Return (supervisors, analyzers, coder) after applying active profile.
        Priority: explicit CLI overrides > active profile > mode preset.
        CLI applies its own overrides on top after calling this.
        """
        if self.profile and self.profile in self.profiles:
            p = self.profiles[self.profile]
            sup = self.supervisors or p.supervisors
            ana = self.analyzers   or p.analyzers
            cod = self.coder       or p.coder
        else:
            sup = self.supervisors
            ana = self.analyzers
            cod = self.coder
        return sup, ana, cod

    def resolve_observer_models(self) -> tuple[list[str], list[str]]:
        """Return (supervisors, analyzers) for observer mode."""
        sup = self.observer_supervisors or OBSERVER_DEFAULT_SUPERVISORS
        ana = self.observer_analyzers   or []
        return sup, ana


def load_config(config_path: Path | None = None) -> CrosscheckConfig:
    """Load config from TOML file then overlay environment variables."""
    cfg = CrosscheckConfig()

    paths = [config_path] if config_path else _DEFAULT_CONFIG_PATHS
    for p in paths:
        if p and p.exists():
            with open(p, "rb") as f:
                data = tomllib.load(f)
            _apply_toml(cfg, data)
            break

    _apply_env(cfg)
    return cfg


def _apply_toml(cfg: CrosscheckConfig, data: dict) -> None:
    top = data.get("crosscheck", data)

    if v := top.get("api_key"):           cfg.api_key         = v
    if v := top.get("mode"):              cfg.mode            = Mode(v)
    if v := top.get("review_type"):       cfg.review_type     = Task(v)
    if v := top.get("max_rounds"):        cfg.max_rounds      = int(v)
    if v := top.get("supervisors"):       cfg.supervisors     = list(v)
    if v := top.get("analyzers"):         cfg.analyzers       = list(v)
    if v := top.get("coder"):             cfg.coder           = str(v)
    if v := top.get("profile"):           cfg.profile         = str(v)
    if v := top.get("monitor"):           cfg.monitor         = bool(v)
    if v := top.get("insaits_api_key"):   cfg.insaits_api_key = str(v)
    if v := top.get("monitor_anchor"):    cfg.monitor_anchor  = str(v)
    if v := top.get("output_format"):     cfg.output_format   = str(v)
    if v := top.get("site_url"):          cfg.site_url        = str(v)

    # Observer settings
    obs = top.get("observer", {})
    if v := obs.get("supervisors"):  cfg.observer_supervisors = list(v)
    if v := obs.get("analyzers"):    cfg.observer_analyzers   = list(v)
    if v := obs.get("plan"):         cfg.observer_plan        = str(v)
    if v := obs.get("extensions"):   cfg.observer_extensions  = list(v)
    if v := obs.get("debounce"):     cfg.observer_debounce    = float(v)

    # Named profiles: [profile.NAME]
    for name, vals in data.get("profile", {}).items():
        if not isinstance(vals, dict):
            continue
        cfg.profiles[name] = ProfileConfig(
            name        = name,
            supervisors = list(vals.get("supervisors", [])),
            analyzers   = list(vals.get("analyzers",   [])),
            coder       = str( vals.get("coder",       "")),
            notes       = str( vals.get("notes",       "")),
        )


def _apply_env(cfg: CrosscheckConfig) -> None:
    env = os.environ

    if v := env.get("CROSSCHECK_API_KEY") or env.get("OPENROUTER_API_KEY"):
        cfg.api_key = v
    if v := env.get("CROSSCHECK_MODE"):
        cfg.mode = Mode(v)
    if v := env.get("CROSSCHECK_REVIEW_TYPE"):
        cfg.review_type = Task(v)
    if v := env.get("CROSSCHECK_MAX_ROUNDS"):
        cfg.max_rounds = int(v)
    if (v := env.get("CROSSCHECK_MONITOR", "")) != "":
        cfg.monitor = v.lower() in ("1", "true", "yes")
    if v := env.get("INSAITS_API_KEY"):
        cfg.insaits_api_key = v
    if v := env.get("CROSSCHECK_MONITOR_ANCHOR"):
        cfg.monitor_anchor = v
    if v := env.get("CROSSCHECK_OUTPUT"):
        cfg.output_format = v
    if v := env.get("CROSSCHECK_PROFILE"):
        cfg.profile = v
    if v := env.get("CROSSCHECK_OBSERVER_PLAN"):
        cfg.observer_plan = v


def example_toml() -> str:
    return """\
# crosscheck.toml — configuration for crosscheck-ai
# Place in project root or ~/.config/crosscheck/crosscheck.toml

[crosscheck]
api_key       = "sk-or-..."    # or: export CROSSCHECK_API_KEY=sk-or-...
mode          = "balanced"      # fast | balanced | quality
review_type   = "code"          # code | plan | text | appstore
max_rounds    = 5

# Activate a named profile (optional):
# profile = "chinese-best"

# InsAIts anomaly monitoring (requires: pip install crosscheck-ai[monitor])
monitor         = false
insaits_api_key = ""
monitor_anchor  = "Review content for quality and correctness"

# ── Observer mode settings ────────────────────────────────────────────────────
# Observer watches external coding sessions and flags bugs/drift in real-time.
[crosscheck.observer]
# supervisors = ["anthropic/claude-opus-4.6", "x-ai/grok-4"]  # defaults
# analyzers   = []   # empty = use mode preset
# plan        = ""   # path to plan file OR inline plan text (for drift detection)
# extensions  = [".py", ".ts", ".kt"]  # file extensions to watch
# debounce    = 0.5  # seconds to wait after save before reviewing

# ── Named model profiles ──────────────────────────────────────────────────────
# Use with: crosscheck review myfile.py --profile NAME
# Each profile fully specifies supervisors, analyzers, and coder.
# Any OpenRouter model ID is accepted for any role.

[profile.chinese-best]
notes       = "Maximum performance using Chinese open-source models"
supervisors = ["anthropic/claude-opus-4.6"]
analyzers   = [
  "deepseek/deepseek-v3.2",
  "qwen/qwen3.5-397b-a17b",
  "moonshotai/kimi-k2.5",
  "qwen/qwen3-235b-a22b",
]
coder       = "deepseek/deepseek-v3.2"

[profile.western-only]
notes       = "All Western models — Grok 4 + Gemini 3.1 + GPT-5"
supervisors = ["anthropic/claude-opus-4.6", "x-ai/grok-4"]
analyzers   = [
  "x-ai/grok-4",
  "google/gemini-3.1-pro-preview",
  "openai/gpt-5-mini",
  "mistralai/mistral-large-2411",
]
coder       = "anthropic/claude-opus-4.6"

[profile.code-specialist]
notes       = "Best models for code review — mixed East/West"
supervisors = ["anthropic/claude-opus-4.6"]
analyzers   = [
  "qwen/qwen3-coder-next",
  "deepseek/deepseek-r1",
  "moonshotai/kimi-k2.5",
  "mistralai/devstral-small",
]
coder       = "anthropic/claude-opus-4.6"

[profile.fast-cheap]
notes       = "Fastest turnaround, lowest cost"
supervisors = ["anthropic/claude-sonnet-4.6"]
analyzers   = [
  "google/gemini-2.5-flash",
  "deepseek/deepseek-chat-v3-0324",
  "x-ai/grok-4-fast",
]
coder       = "anthropic/claude-haiku-4.5"

[profile.dual-grok]
notes       = "Dual Grok supervisors — maximum reasoning power"
supervisors = ["x-ai/grok-4", "anthropic/claude-opus-4.6"]
analyzers   = [
  "deepseek/deepseek-v3.2",
  "moonshotai/kimi-k2.5",
  "qwen/qwen3-235b-a22b",
  "google/gemini-3.1-pro-preview",
]
coder       = "anthropic/claude-sonnet-4.6"
"""
