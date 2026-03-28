"""
crosscheck.team.command_parser
------------------------------
Parse @role and @model directives from user input.

Near 1:1 port from Android AI Bridge's ModelCommandParser.kt.

Examples:
    "@coder fix the null check in line 45"
    "@claude design the architecture"
    "@debugger @security both check this function"
    "everyone, what do you think about this approach?"  (broadcast)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from crosscheck.team.roles import TeamRole


@dataclass
class ParseResult:
    """Result of parsing user input for @mentions."""

    has_mentions: bool
    targets: list[TeamRole]       # Specific roles mentioned
    global_message: str           # The message text (with @mentions stripped)
    role_messages: dict[TeamRole, str]  # Per-role messages if different tasks


# ---------------------------------------------------------------------------
# Alias tables — ported from Android ModelCommandParser.kt
# ---------------------------------------------------------------------------

ROLE_ALIASES: dict[TeamRole, list[str]] = {
    TeamRole.PLANNER: ["planner", "ceo", "boss", "lead", "plan"],
    TeamRole.ARCHITECT: ["architect", "cto", "designer", "design"],
    TeamRole.CODER: ["coder", "dev", "programmer", "builder", "code", "write"],
    TeamRole.DEBUGGER: ["debugger", "debug", "tester", "qa", "bug"],
    TeamRole.SECURITY: ["security", "sec", "guard", "vuln"],
    TeamRole.ANALYST: ["analyst", "analyzer", "flow", "perf", "performance"],
}

MODEL_ALIASES: dict[str, list[str]] = {
    "claude": ["claude", "anthropic", "sonnet", "opus"],
    "gpt": ["gpt", "openai", "chatgpt", "gpt-5"],
    "gemini": ["gemini", "google"],
    "grok": ["grok", "xai"],
    "deepseek": ["deepseek", "deep-seek"],
    "qwen": ["qwen"],
    "llama": ["llama", "meta"],
    "mistral": ["mistral", "mixtral"],
}

# Build reverse lookup: alias -> role
_ALIAS_TO_ROLE: dict[str, TeamRole] = {}
for _role, _aliases in ROLE_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_ROLE[_alias] = _role

# Default model -> role mapping (for @claude -> CODER, @grok -> SECURITY, etc.)
_MODEL_TO_ROLE: dict[str, TeamRole] = {
    "claude": TeamRole.CODER,
    "gpt": TeamRole.ARCHITECT,
    "gemini": TeamRole.ANALYST,
    "grok": TeamRole.SECURITY,
    "deepseek": TeamRole.DEBUGGER,
    "qwen": TeamRole.ANALYST,
    "llama": TeamRole.DEBUGGER,
    "mistral": TeamRole.ARCHITECT,
}


# Pattern: @word at the start of text or after whitespace
_MENTION_RE = re.compile(r"@(\w+)", re.IGNORECASE)


class CommandParser:
    """Parse @mentions from user input and route to roles."""

    @staticmethod
    def parse(text: str) -> ParseResult:
        """Parse user input for @role or @model mentions.

        Args:
            text: Raw user input, e.g. "@coder fix line 5" or
                  "@debugger @security check this function"

        Returns:
            ParseResult with target roles and cleaned message.
        """
        mentions = _MENTION_RE.findall(text)

        if not mentions:
            return ParseResult(
                has_mentions=False,
                targets=[],
                global_message=text.strip(),
                role_messages={},
            )

        targets: list[TeamRole] = []
        for mention in mentions:
            lower = mention.lower()

            # Check role aliases first
            if lower in _ALIAS_TO_ROLE:
                role = _ALIAS_TO_ROLE[lower]
                if role not in targets:
                    targets.append(role)
                continue

            # Check model aliases
            for model_name, aliases in MODEL_ALIASES.items():
                if lower in aliases:
                    role = _MODEL_TO_ROLE.get(model_name)
                    if role and role not in targets:
                        targets.append(role)
                    break

        # Strip @mentions from the message text
        cleaned = _MENTION_RE.sub("", text).strip()
        # Clean up extra whitespace
        cleaned = re.sub(r"\s+", " ", cleaned)

        if not targets:
            return ParseResult(
                has_mentions=False,
                targets=[],
                global_message=text.strip(),
                role_messages={},
            )

        return ParseResult(
            has_mentions=True,
            targets=targets,
            global_message=cleaned,
            role_messages={role: cleaned for role in targets},
        )

    @staticmethod
    def wants_full_history(text: str) -> bool:
        """Check if user is requesting full conversation history.

        Ported from Android's keyword detection in buildAppendContext.
        """
        lower = text.lower()
        triggers = [
            "review entire discussion",
            "review everything",
            "full history",
            "check all previous",
            "what did we discuss",
            "summarize our conversation",
            "what have we covered",
            "summarize everything",
        ]
        return any(trigger in lower for trigger in triggers)
