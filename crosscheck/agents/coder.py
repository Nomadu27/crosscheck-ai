"""
crosscheck.agents.coder
------------------------
Coder agent — the final implementer in the pipeline.

DESIGN: Any OpenRouter model can be Coder. The user chooses via
--interactive, --coder flag, or named profile. Anthropic is the
default in presets but is NOT enforced. DeepSeek, Grok, Kimi, etc.
are all valid choices.

The coder receives the original content, supervisor instructions,
and all critical/major issues, then returns the complete revised content.
"""

from __future__ import annotations

from crosscheck.client  import OpenRouterClient
from crosscheck.models  import Task
from crosscheck.prompts import CODER_SYSTEM, coder_prompt


class CoderAgent:
    """Wraps any single model as the Coder. No model-ID restrictions."""

    def __init__(
        self,
        client: OpenRouterClient,
        model:  str,
        task:   Task,
    ):
        if not model:
            raise ValueError("CoderAgent requires a non-empty model ID.")
        self.client = client
        self.model  = model
        self.task   = task

    async def revise(
        self,
        content:      str,
        instructions: str,
        issues:       list[dict],
        round_num:    int,
        max_tokens:   int = 8192,
    ) -> str:
        """
        Produce a complete revised version of `content` resolving all `issues`.
        Returns raw revised content — no JSON, no preamble.
        """
        prompt = coder_prompt(
            task         = self.task,
            content      = content,
            instructions = instructions,
            issues       = self._format_issues(issues),
            round_num    = round_num,
        )

        revised = await self.client.chat(
            model       = self.model,
            messages    = [
                {"role": "system", "content": CODER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens  = max_tokens,
            temperature = 0.2,
            json_mode   = False,   # raw content output, never JSON
        )

        return revised.strip()

    @staticmethod
    def _format_issues(issues: list[dict]) -> str:
        if not issues:
            return "No specific issues flagged — improve overall quality and clarity."
        lines = []
        for i, issue in enumerate(issues, 1):
            sev  = issue.get("severity", "major").upper()
            loc  = issue.get("location", "")
            desc = issue.get("description", "")
            fix  = issue.get("fix", "")
            loc_str = f" [{loc}]" if loc else ""
            lines.append(f"{i}. [{sev}]{loc_str} {desc}")
            if fix:
                lines.append(f"   → Fix: {fix}")
        return "\n".join(lines)
