"""
crosscheck.agents.supervisor
-----------------------------
Supervisor agent — orchestrates the session, decomposes tasks, synthesizes
analyzer reports, and makes APPROVED / REVISE decisions.

DUAL SUPERVISOR VOTING (when 2 models configured):
  Both run independently on every synthesis call. Rules:
  - Both APPROVED → APPROVED (unanimous)
  - Both REVISE   → REVISE   (unanimous)
  - Disagreement + both scores ≥ 8.5 → APPROVED (near-perfect exception)
  - Disagreement + any score < 8.5   → REVISE   (conservative, safe default)
  Critical issues and coder instructions are merged from both.
  consensus=False is recorded in the result for visibility.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from crosscheck.client import OpenRouterClient
from crosscheck.models import Task
from crosscheck.prompts import (
    SUPERVISOR_SYSTEM,
    supervisor_decompose,
    supervisor_synthesize,
)


@dataclass
class DecomposeResult:
    session_goal:   str
    analyzer_tasks: list[dict]   # [{id, angle, instruction}]


@dataclass
class SynthesisResult:
    verdict:            str            # "APPROVED" | "REVISE"
    overall_score:      float
    summary:            str
    critical_issues:    list[str]
    coder_instructions: str
    approved_aspects:   list[str]
    raw:                dict = field(default_factory=dict)
    supervisor_votes:   list[str] = field(default_factory=list)
    consensus:          bool = True


class SupervisorAgent:
    """
    Wraps 1 or 2 supervisor models.
    With 2 models, both synthesize in parallel and votes are reconciled.
    Any OpenRouter model ID is accepted — no restrictions.
    """

    def __init__(
        self,
        client:     OpenRouterClient,
        models:     list[str],
        task:       Task,
        max_rounds: int,
    ):
        if not models:
            raise ValueError("SupervisorAgent requires at least one model ID.")
        self.client     = client
        self.models     = models[:2]   # hard cap at 2
        self.task       = task
        self.max_rounds = max_rounds

    # ── Public API ───────────────────────────────────────────────────────

    async def decompose(self, content: str, round_num: int) -> DecomposeResult:
        """Primary supervisor decomposes the content into per-analyzer tasks."""
        prompt = supervisor_decompose(self.task, content, round_num, self.max_rounds)
        raw    = await self._call(self.models[0], prompt)
        parsed = self._parse_json(raw, "decompose")
        return DecomposeResult(
            session_goal   = parsed.get("session_goal", ""),
            analyzer_tasks = parsed.get("analyzer_tasks", []),
        )

    async def synthesize(
        self,
        analyzer_reports: list[dict],
        round_num:        int,
    ) -> SynthesisResult:
        """
        Synthesize analyzer reports into a final verdict.
        Single supervisor: straightforward.
        Dual supervisor: both run in parallel, votes reconciled.
        """
        reports_text = json.dumps(analyzer_reports, indent=2)
        prompt       = supervisor_synthesize(reports_text, round_num)

        if len(self.models) == 1:
            return await self._single_synthesis(self.models[0], prompt)

        # Dual: parallel execution
        r1, r2 = await asyncio.gather(
            self._single_synthesis(self.models[0], prompt),
            self._single_synthesis(self.models[1], prompt),
        )
        return self._reconcile(r1, r2)

    # ── Private ───────────────────────────────────────────────────────────

    async def _single_synthesis(self, model: str, prompt: str) -> SynthesisResult:
        raw    = await self._call(model, prompt)
        parsed = self._parse_json(raw, f"synthesize[{model.split('/')[-1]}]")
        return SynthesisResult(
            verdict            = parsed.get("verdict", "REVISE"),
            overall_score      = float(parsed.get("overall_score", 0)),
            summary            = parsed.get("summary", ""),
            critical_issues    = parsed.get("critical_issues", []),
            coder_instructions = parsed.get("coder_instructions", ""),
            approved_aspects   = parsed.get("approved_aspects", []),
            raw                = parsed,
            supervisor_votes   = [parsed.get("verdict", "REVISE")],
            consensus          = True,
        )

    @staticmethod
    def _reconcile(r1: SynthesisResult, r2: SynthesisResult) -> SynthesisResult:
        """Reconcile two supervisor verdicts. Conservative: REVISE wins on split."""
        votes = [r1.verdict, r2.verdict]

        if r1.verdict == r2.verdict:
            # Unanimous — easy
            final_verdict = r1.verdict
            consensus     = True
        else:
            # Split vote
            both_high = r1.overall_score >= 8.5 and r2.overall_score >= 8.5
            if both_high:
                # Both nearly perfect — allow APPROVED despite split
                final_verdict = "APPROVED"
            else:
                # Safe default: if in doubt, revise
                final_verdict = "REVISE"
            consensus = False

        # Use the higher-scoring result as primary for narrative fields
        primary   = r1 if r1.overall_score >= r2.overall_score else r2
        secondary = r2 if primary is r1 else r1

        # Deduplicated merge of critical issues
        merged_issues = list(dict.fromkeys(
            primary.critical_issues + secondary.critical_issues
        ))

        # Merge coder instructions (append secondary only when relevant)
        merged_instructions = primary.coder_instructions
        if final_verdict == "REVISE" and secondary.coder_instructions:
            merged_instructions = (
                f"{primary.coder_instructions}\n\n"
                f"[Supervisor 2 additions]\n{secondary.coder_instructions}"
            ).strip()

        return SynthesisResult(
            verdict            = final_verdict,
            overall_score      = round((r1.overall_score + r2.overall_score) / 2, 1),
            summary            = primary.summary,
            critical_issues    = merged_issues,
            coder_instructions = merged_instructions,
            approved_aspects   = list(dict.fromkeys(
                primary.approved_aspects + secondary.approved_aspects
            )),
            raw              = {"supervisor_1": r1.raw, "supervisor_2": r2.raw},
            supervisor_votes = votes,
            consensus        = consensus,
        )

    async def _call(self, model: str, user_prompt: str) -> str:
        return await self.client.chat(
            model       = model,
            messages    = [
                {"role": "system", "content": SUPERVISOR_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            temperature = 0.2,
            json_mode   = True,
        )

    @staticmethod
    def _parse_json(raw: str, context: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cleaned = (
                raw.strip()
                   .removeprefix("```json")
                   .removeprefix("```")
                   .removesuffix("```")
                   .strip()
            )
            try:
                return json.loads(cleaned)
            except Exception as e:
                raise ValueError(
                    f"Supervisor {context}: JSON parse failed — {e}\n"
                    f"Raw (first 400 chars): {raw[:400]}"
                ) from e
