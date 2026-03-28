"""
crosscheck.team
---------------
AI Dev Team: multi-agent coding pipeline where Claude codes
and other models advise, debug, review, and plan.

v2.0.0 — The real deal.
"""

from crosscheck.team.roles import TeamRole, RoleSpec, DEFAULT_TEAM
from crosscheck.team.chat import (
    TeamMessage, CodeBlock, ChatHistory, SessionPhase,
)
from crosscheck.team.session import TeamSession
from crosscheck.team.command_parser import CommandParser, ParseResult
from crosscheck.team.language import LanguageDetector

__all__ = [
    "TeamRole",
    "RoleSpec",
    "DEFAULT_TEAM",
    "TeamMessage",
    "CodeBlock",
    "ChatHistory",
    "SessionPhase",
    "TeamSession",
    "CommandParser",
    "ParseResult",
    "LanguageDetector",
]
