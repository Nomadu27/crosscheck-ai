"""
crosscheck.policy
-----------------
Phase 2: Custom policy / company-rule agents.

Allows teams to upload internal security checklists, architecture guides,
or coding standards as TOML/YAML/Markdown files. These are injected as
additional analyzer tasks with custom system prompts.

Usage:
    # In crosscheck.toml:
    [policy]
    files = ["policies/security.md", "policies/arch_guidelines.toml"]

    # Or programmatically:
    policy = PolicyLoader.from_file("policies/security.md")
    session = PolicyAwareSession(api_key=..., policies=[policy])
    result  = await session.run_async(content)

Policy file formats supported:
  - Markdown (.md)    — free-form checklist, rules, or guidelines
  - TOML (.toml)      — structured rule definitions
  - YAML (.yaml/.yml) — structured rule definitions
  - Plain text (.txt) — treated as prose checklist
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Policy data model
# ---------------------------------------------------------------------------

@dataclass
class PolicyRule:
    id:          str
    description: str
    severity:    str   = "major"     # critical | major | minor
    category:    str   = "custom"
    check_for:   list[str] = field(default_factory=list)  # keywords/patterns to flag
    example:     str   = ""


@dataclass
class Policy:
    name:        str
    description: str
    source_file: str
    rules:       list[PolicyRule]
    raw_text:    str = ""            # full policy prose for injection

    def to_analyzer_task(self, task_id: int = 99) -> dict:
        """Convert policy into an analyzer task dict for the decompose pipeline."""
        rules_summary = "\n".join(
            f"  [{r.severity.upper()}] {r.id}: {r.description}"
            for r in self.rules[:20]   # cap at 20 rules per policy
        )
        instruction = (
            f"Apply '{self.name}' company policy:\n{rules_summary}\n\n"
            f"Full policy context:\n{self.raw_text[:3000]}"
        )
        return {
            "id":          task_id,
            "angle":       f"Policy: {self.name}",
            "instruction": instruction,
        }

    def to_system_prompt_addition(self) -> str:
        """Extra system prompt text injected for policy-aware analyzers."""
        return (
            f"\n\n=== COMPANY POLICY: {self.name} ===\n"
            f"{self.description}\n\n"
            f"{self.raw_text[:5000]}\n"
            f"=== END POLICY ===\n"
            "Always check the content against ALL rules above. Flag any violations."
        )


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------

class PolicyLoader:
    """Load and parse policy files into Policy objects."""

    @classmethod
    def from_file(cls, path: str | Path) -> Policy:
        p    = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        ext  = p.suffix.lower()

        if ext in (".yaml", ".yml"):
            return cls._from_yaml(text, p.name, str(p))
        elif ext == ".toml":
            return cls._from_toml(text, p.name, str(p))
        else:
            # Markdown or plain text
            return cls._from_markdown(text, p.stem.replace("_", " ").title(), str(p))

    @classmethod
    def from_text(cls, text: str, name: str = "Custom Policy") -> Policy:
        return cls._from_markdown(text, name, "<inline>")

    @classmethod
    def from_directory(cls, directory: str | Path) -> list[Policy]:
        """Load all policy files from a directory."""
        d       = Path(directory)
        exts    = {".md", ".txt", ".toml", ".yaml", ".yml"}
        policies = []
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in exts:
                try:
                    policies.append(cls.from_file(f))
                except Exception:
                    pass
        return policies

    # ── Parsers ──────────────────────────────────────────────────────────

    @staticmethod
    def _from_markdown(text: str, name: str, source: str) -> Policy:
        """
        Parse markdown policy. Rules are extracted from:
          - Checklist items:  - [ ] description
          - Numbered lists:   1. description
          - H3/H4 headings:   ### rule name
        """
        rules: list[PolicyRule] = []

        # Extract checklist items
        for i, m in enumerate(re.finditer(
            r"^[\-\*]\s+\[[ xX]\]\s+(.+)$", text, re.MULTILINE
        )):
            rules.append(PolicyRule(
                id          = f"MD-{i+1:03d}",
                description = m.group(1).strip(),
                severity    = _infer_severity(m.group(1)),
                category    = "checklist",
            ))

        # Extract numbered items if no checklist found
        if not rules:
            for i, m in enumerate(re.finditer(
                r"^\d+\.\s+(.+)$", text, re.MULTILINE
            )):
                rules.append(PolicyRule(
                    id          = f"MD-{i+1:03d}",
                    description = m.group(1).strip(),
                    severity    = _infer_severity(m.group(1)),
                    category    = "rule",
                ))

        # Extract description from first paragraph
        first_para = re.search(r"^[^#\-\*\d].{20,}", text, re.MULTILINE)
        description = first_para.group(0)[:200] if first_para else name

        return Policy(
            name        = name,
            description = description,
            source_file = source,
            rules       = rules,
            raw_text    = text,
        )

    @staticmethod
    def _from_toml(text: str, name: str, source: str) -> Policy:
        import tomllib
        data  = tomllib.loads(text)
        rules = []

        for rule_id, rule_data in data.get("rule", {}).items():
            rules.append(PolicyRule(
                id          = rule_id,
                description = rule_data.get("description", ""),
                severity    = rule_data.get("severity", "major"),
                category    = rule_data.get("category", "custom"),
                check_for   = rule_data.get("check_for", []),
                example     = rule_data.get("example", ""),
            ))

        meta = data.get("policy", {})
        return Policy(
            name        = meta.get("name", name),
            description = meta.get("description", ""),
            source_file = source,
            rules       = rules,
            raw_text    = text,
        )

    @staticmethod
    def _from_yaml(text: str, name: str, source: str) -> Policy:
        try:
            import yaml
        except ImportError:
            # Fall back to markdown parsing if PyYAML not installed
            return PolicyLoader._from_markdown(text, name, source)

        data  = yaml.safe_load(text) or {}
        rules = []

        for rule_data in data.get("rules", []):
            rules.append(PolicyRule(
                id          = str(rule_data.get("id", f"YAML-{len(rules)+1:03d}")),
                description = rule_data.get("description", ""),
                severity    = rule_data.get("severity", "major"),
                category    = rule_data.get("category", "custom"),
                check_for   = rule_data.get("check_for", []),
            ))

        return Policy(
            name        = data.get("name", name),
            description = data.get("description", ""),
            source_file = source,
            rules       = rules,
            raw_text    = text,
        )


def _infer_severity(text: str) -> str:
    text_lower = text.lower()
    if any(k in text_lower for k in ("critical", "never", "must not", "prohibited", "forbidden")):
        return "critical"
    if any(k in text_lower for k in ("should not", "avoid", "warning", "important")):
        return "major"
    return "minor"


# ---------------------------------------------------------------------------
# Policy-aware session wrapper
# ---------------------------------------------------------------------------

class PolicyAwareSession:
    """
    Wraps MultiAgentSession and injects company policies as extra analyzer tasks.
    """

    def __init__(
        self,
        api_key:   str,
        policies:  list[Policy],
        **session_kwargs,
    ):
        from crosscheck.core import MultiAgentSession
        self._policies = policies
        self._session  = MultiAgentSession(api_key=api_key, **session_kwargs)

    async def run_async(self, content: str):
        """
        Run session with policy injection.
        Policies are prepended to the content as context.
        """
        # Build policy context block
        policy_block = "\n\n".join(
            p.to_system_prompt_addition() for p in self._policies
        )

        # Inject policies into content as a prefix block
        enriched_content = (
            f"{policy_block}\n\n"
            f"=== CONTENT TO REVIEW ===\n"
            f"{content}"
        ) if policy_block else content

        return await self._session.run_async(enriched_content)

    def run(self, content: str):
        import asyncio
        return asyncio.run(self.run_async(content))


# ---------------------------------------------------------------------------
# Built-in policy templates
# ---------------------------------------------------------------------------

BUILTIN_POLICIES = {
    "owasp_top10": Policy(
        name        = "OWASP Top 10",
        description = "Check for the OWASP Top 10 web application security risks",
        source_file = "<builtin>",
        raw_text    = """
OWASP Top 10 Security Checklist:
- [ ] A01: No broken access control (check authorization on all endpoints)
- [ ] A02: No cryptographic failures (no hardcoded secrets, proper encryption)
- [ ] A03: No injection vulnerabilities (SQL, NoSQL, OS, LDAP injection)
- [ ] A04: No insecure design (threat modeling applied)
- [ ] A05: No security misconfiguration (default credentials, open ports)
- [ ] A06: No vulnerable/outdated components
- [ ] A07: No authentication/session failures
- [ ] A08: No data integrity failures (unsafe deserialization)
- [ ] A09: Logging and monitoring in place
- [ ] A10: No SSRF vulnerabilities
""",
        rules = [
            PolicyRule("A01", "No broken access control",       "critical", "security"),
            PolicyRule("A02", "No cryptographic failures",      "critical", "security"),
            PolicyRule("A03", "No injection vulnerabilities",   "critical", "security"),
            PolicyRule("A05", "No security misconfiguration",   "major",    "security"),
            PolicyRule("A07", "No auth/session failures",       "critical", "security"),
            PolicyRule("A10", "No SSRF vulnerabilities",        "major",    "security"),
        ],
    ),
    "pep8_strict": Policy(
        name        = "PEP 8 Strict",
        description = "Enforce PEP 8 Python style guide strictly",
        source_file = "<builtin>",
        raw_text    = """
PEP 8 Python Style Rules (strict):
- [ ] Max line length 79 characters
- [ ] 4 spaces indentation, no tabs
- [ ] Two blank lines between top-level definitions
- [ ] One blank line between methods
- [ ] snake_case for functions and variables
- [ ] PascalCase for classes
- [ ] UPPER_CASE for constants
- [ ] No trailing whitespace
- [ ] Imports on separate lines, stdlib before third-party
- [ ] Type hints on all public functions
""",
        rules = [
            PolicyRule("PEP8-E501", "Max line length 79 chars",   "minor", "style"),
            PolicyRule("PEP8-E302", "Two blank lines between defs","minor", "style"),
            PolicyRule("PEP8-N801", "Classes use PascalCase",     "minor", "style"),
            PolicyRule("PEP8-ANN",  "Type hints required",        "major", "typing"),
        ],
    ),
}
