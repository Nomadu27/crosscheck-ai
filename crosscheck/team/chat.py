"""
crosscheck.team.chat
--------------------
Message protocol and chat history for the AI Dev Team.

Ported from Android AI Bridge's DebateMessage pattern — each message
has a role, model, content, timestamp, and phase context.

Privacy-first context building: by default each agent only sees the
last N messages. Full history is sent only on explicit request
(same pattern as Android's buildAppendContext).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SessionPhase(Enum):
    """Phases of a TeamSession."""

    PLANNING = "planning"
    DISCUSSION = "discussion"
    CODING = "coding"
    REVIEW = "review"
    HUMAN_INPUT = "human_input"
    DONE = "done"


@dataclass
class CodeBlock:
    """A code block extracted from a CODER's response."""

    filename: str
    language: str
    content: str
    action: str = "create"  # "create", "edit", "delete"


@dataclass
class TeamMessage:
    """A single message in the team chat."""

    role: str               # TeamRole.value or "human"
    model_id: str           # OpenRouter model ID
    display_name: str       # "Claude (Coder)" or "Cristi (Human)"
    content: str
    phase: SessionPhase
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    round_num: int = 0
    code_blocks: list[CodeBlock] = field(default_factory=list)
    is_code_output: bool = False
    target: str | None = None  # "@debugger" if directed

    def to_dict(self) -> dict:
        """Serialize for WebSocket/JSON transport."""
        return {
            "role": self.role,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "content": self.content,
            "phase": self.phase.value,
            "timestamp": self.timestamp.isoformat(),
            "round_num": self.round_num,
            "code_blocks": [
                {
                    "filename": cb.filename,
                    "language": cb.language,
                    "content": cb.content,
                    "action": cb.action,
                }
                for cb in self.code_blocks
            ],
            "is_code_output": self.is_code_output,
            "target": self.target,
        }


@dataclass
class ChatHistory:
    """Full conversation history for a team session."""

    messages: list[TeamMessage] = field(default_factory=list)
    topic: str = ""
    session_id: str = ""

    def add(self, message: TeamMessage) -> None:
        """Append a message to history (immutable pattern: creates new list)."""
        self.messages = [*self.messages, message]

    def for_model_context(
        self, role: str, last_n: int = 6
    ) -> list[dict[str, str]]:
        """Build OpenRouter messages array for a specific agent.

        Privacy-first: only last N messages by default.
        Each agent sees the conversation focused on recent context.
        Ported from Android's buildAppendContext pattern.

        Args:
            role: The role value of the agent requesting context.
            last_n: Number of recent messages to include.

        Returns:
            List of dicts with "role" and "content" keys.
        """
        context: list[dict[str, str]] = []

        recent = self.messages[-last_n:] if len(self.messages) > last_n else self.messages
        for msg in recent:
            if msg.role == "human":
                context.append({"role": "user", "content": msg.content})
            else:
                context.append({
                    "role": "assistant",
                    "content": f"[{msg.display_name}]: {msg.content}",
                })

        return context

    def full_context(self) -> list[dict[str, str]]:
        """Full history — only when user explicitly requests it.

        Trigger phrases (from Android pattern):
            "review everything", "full history", "summarize our conversation"
        """
        context: list[dict[str, str]] = []
        for msg in self.messages:
            if msg.role == "human":
                context.append({"role": "user", "content": msg.content})
            else:
                context.append({
                    "role": "assistant",
                    "content": f"[{msg.display_name}]: {msg.content}",
                })
        return context

    def to_transcript(self) -> str:
        """Export conversation as markdown transcript.

        Ported from Android DebateViewModel.exportAsText().
        """
        lines = [
            "=" * 60,
            "CROSSCHECK AI DEV TEAM — TRANSCRIPT",
            "=" * 60,
            f"Topic: {self.topic}",
            f"Session: {self.session_id}",
            "",
        ]
        for msg in self.messages:
            phase_tag = f" [{msg.phase.value.upper()}]" if msg.phase != SessionPhase.DONE else ""
            lines.append(f"**{msg.display_name}**{phase_tag}:")
            lines.append(msg.content)
            if msg.code_blocks:
                for cb in msg.code_blocks:
                    lines.append(f"\n```{cb.language}")
                    lines.append(f"# filename: {cb.filename}")
                    lines.append(cb.content)
                    lines.append("```\n")
            lines.append("")
            lines.append("-" * 60)
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(
    r"```(\w+)?\s*\n"
    r"(?:#\s*filename:\s*(.+?)\n)?"
    r"(.*?)"
    r"\n```",
    re.DOTALL,
)


def extract_code_blocks(content: str) -> list[CodeBlock]:
    """Extract fenced code blocks from a model's response.

    Expected format from CODER:
        ```python
        # filename: path/to/file.py
        <code>
        ```

    Returns list of CodeBlock. If no filename comment, uses 'untitled'.
    """
    blocks: list[CodeBlock] = []
    for match in _CODE_BLOCK_RE.finditer(content):
        language = match.group(1) or "text"
        filename = (match.group(2) or "untitled").strip()
        code = match.group(3).strip()
        if code:
            blocks.append(CodeBlock(
                filename=filename,
                language=language,
                content=code,
            ))
    return blocks
