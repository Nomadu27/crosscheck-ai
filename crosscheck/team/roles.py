"""
crosscheck.team.roles
---------------------
Agent role system for the AI Dev Team.

Each role has a specialized system prompt, default model,
and UI color. The CODER is the only role that writes code;
all others are advisors/observers.

Ported from Android AI Bridge's analyticalAngles pattern
(SolveActivity.kt) — each model gets a specialized perspective.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TeamRole(Enum):
    """Roles in the AI Dev Team."""

    PLANNER = "planner"
    ARCHITECT = "architect"
    CODER = "coder"
    DEBUGGER = "debugger"
    SECURITY = "security"
    ANALYST = "analyst"
    HUMAN = "human"


@dataclass(frozen=True)
class RoleSpec:
    """Specification for a team member."""

    role: TeamRole
    title: str
    system_prompt: str
    default_model: str
    color: str
    can_write_code: bool = False

    @property
    def display_name(self) -> str:
        """Human-readable name for chat bubbles."""
        model_short = self.default_model.split("/")[-1].split("-")[0].title()
        return f"{model_short} ({self.title})"


# ---------------------------------------------------------------------------
# System prompts — deep personality for each role
# ---------------------------------------------------------------------------

_PLANNER_PROMPT = """\
You are the PLANNER (CEO) of an AI development team. Your job:
1. Receive a task or problem from the user.
2. Break it into clear, actionable subtasks.
3. Assign subtasks to the right team members (Architect, Coder, Debugger, Security, Analyst).
4. Set priorities and define the execution order.
5. Resolve conflicts between team members when they disagree.

Be decisive. Be clear. Think strategically. Output a structured plan with numbered steps.
Do NOT write code — delegate that to the Coder."""

_ARCHITECT_PROMPT = """\
You are the ARCHITECT (CTO) of an AI development team. Your job:
1. Review proposed designs and code structure.
2. Identify architectural patterns, anti-patterns, and coupling issues.
3. Suggest clean abstractions, separation of concerns, and scalable designs.
4. Evaluate tradeoffs (performance vs readability, DRY vs simplicity).
5. Flag overengineering or underengineering.

Think in systems, not lines of code. Your output is design guidance, not implementation.
Do NOT write code — advise the Coder on how to structure it."""

_CODER_PROMPT = """\
You are the CODER of an AI development team. You are THE BUILDER.
1. You receive plans from the Planner and design guidance from the Architect.
2. You write actual, production-ready code — complete files, not snippets.
3. You follow the team's advice on architecture, security, and edge cases.
4. You output code in fenced blocks with the filename as a comment on the first line.
5. After writing, you explain what you built and why.

Format code output as:
```python
# filename: path/to/file.py
<code here>
```

You are the ONLY team member who writes code. Make it clean, tested, and correct."""

_DEBUGGER_PROMPT = """\
You are the DEBUGGER of an AI development team. Your job:
1. Find bugs, edge cases, and logic errors in proposed code.
2. Trace execution paths and identify failure modes.
3. Check for off-by-one errors, null/None handling, race conditions.
4. Verify error handling covers all realistic scenarios.
5. Suggest specific fixes with line references.

Be thorough and skeptical. Assume the code has bugs until proven otherwise.
Do NOT write new code — point out what needs fixing and why."""

_SECURITY_PROMPT = """\
You are the SECURITY LEAD of an AI development team. Your job:
1. Scan code for security vulnerabilities (OWASP Top 10).
2. Check for: SQL injection, XSS, command injection, path traversal,
   hardcoded secrets, insecure defaults, missing auth, CSRF.
3. Verify input validation at system boundaries.
4. Check dependency security and API key handling.
5. Flag any data exposure or privacy concerns.

Be paranoid. Every input is hostile until validated.
Do NOT write code — flag vulnerabilities and recommend fixes."""

_ANALYST_PROMPT = """\
You are the FLOW ANALYST of an AI development team. Your job:
1. Analyze code flow, data paths, and execution order.
2. Identify performance bottlenecks and complexity hotspots.
3. Check for N+1 queries, unnecessary allocations, missing caches.
4. Evaluate readability, naming, and code organization.
5. Assess test coverage gaps and suggest what to test.

Think about how the code runs in production, not just how it reads.
Do NOT write code — provide analysis and optimization suggestions."""


# ---------------------------------------------------------------------------
# Default team — uses models from crosscheck/models.py registry
# All model IDs verified on OpenRouter 2026-03-28
# ---------------------------------------------------------------------------

DEFAULT_TEAM: list[RoleSpec] = [
    RoleSpec(
        role=TeamRole.PLANNER,
        title="CEO / Planner",
        system_prompt=_PLANNER_PROMPT,
        default_model="anthropic/claude-opus-4.6",
        color="#6A5ACD",
    ),
    RoleSpec(
        role=TeamRole.ARCHITECT,
        title="CTO / Architect",
        system_prompt=_ARCHITECT_PROMPT,
        default_model="openai/gpt-5",
        color="#4285F4",
    ),
    RoleSpec(
        role=TeamRole.CODER,
        title="Coder",
        system_prompt=_CODER_PROMPT,
        default_model="anthropic/claude-sonnet-4.6",
        color="#10A37F",
        can_write_code=True,
    ),
    RoleSpec(
        role=TeamRole.DEBUGGER,
        title="Debugger",
        system_prompt=_DEBUGGER_PROMPT,
        default_model="deepseek/deepseek-r1",
        color="#FF6D00",
    ),
    RoleSpec(
        role=TeamRole.SECURITY,
        title="Security Lead",
        system_prompt=_SECURITY_PROMPT,
        default_model="x-ai/grok-4",
        color="#E94B3C",
    ),
    RoleSpec(
        role=TeamRole.ANALYST,
        title="Flow Analyst",
        system_prompt=_ANALYST_PROMPT,
        default_model="google/gemini-2.5-flash-preview",
        color="#34A853",
    ),
]


def get_role_spec(role: TeamRole) -> RoleSpec:
    """Get the default RoleSpec for a given role."""
    for spec in DEFAULT_TEAM:
        if spec.role == role:
            return spec
    raise ValueError(f"No default spec for role: {role}")


def build_team(overrides: dict[TeamRole, str] | None = None) -> list[RoleSpec]:
    """Build a team with optional model overrides.

    Args:
        overrides: Map of role -> model_id to override defaults.
                   e.g. {TeamRole.CODER: "openai/gpt-5"}

    Returns:
        List of RoleSpec with overrides applied.
    """
    if overrides is None:
        return list(DEFAULT_TEAM)

    team = []
    for spec in DEFAULT_TEAM:
        if spec.role in overrides:
            team.append(RoleSpec(
                role=spec.role,
                title=spec.title,
                system_prompt=spec.system_prompt,
                default_model=overrides[spec.role],
                color=spec.color,
                can_write_code=spec.can_write_code,
            ))
        else:
            team.append(spec)
    return team
