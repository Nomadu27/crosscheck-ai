"""
crosscheck.team
---------------
AI Dev Team: multi-agent coding pipeline where Claude codes
and other models advise, debug, review, and plan.

v2.0.0 — The real deal.
"""

from crosscheck.team.chat import (
    ChatHistory,
    CodeBlock,
    SessionPhase,
    TeamMessage,
)
from crosscheck.team.command_parser import CommandParser, ParseResult
from crosscheck.team.language import LanguageDetector
from crosscheck.team.roles import DEFAULT_TEAM, RoleSpec, TeamRole, build_team
from crosscheck.team.session import TeamSession

__all__ = [
    "TeamRole",
    "RoleSpec",
    "DEFAULT_TEAM",
    "build_team",
    "TeamMessage",
    "CodeBlock",
    "ChatHistory",
    "SessionPhase",
    "TeamSession",
    "CommandParser",
    "ParseResult",
    "LanguageDetector",
]
