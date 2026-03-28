"""
crosscheck.agents.analyzer
---------------------------
Analyzer agent pool — runs 3-4 models in parallel, each probing a different
angle of the content. Results are structured AnalyzerReport objects.

Each analyzer gets a specific task from the Supervisor's decomposition.
If a model errors, it returns an ERROR report rather than crashing the session.
Prior round feedback is passed in so analyzers can see if their issues were fixed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

from crosscheck.client  import OpenRouterClient
from crosscheck.prompts import ANALYZER_SYSTEM, analyzer_prompt


@dataclass
class AnalyzerReport:
    model_id:          str
    angle:             str
    score:             float
    verdict:           str          # "PASS" | "ISSUES_FOUND" | "ERROR"
    issues:            list[dict]   # [{severity, location, description, fix}]
    positive_findings: list[str]
    summary:           str
    raw:               dict = field(default_factory=dict)
    error:             Optional[str] = None


class AnalyzerPool:
    """Runs N analyzer models in parallel, one task per model."""

    def __init__(
        self,
        client:  OpenRouterClient,
        models:  list[str],
    ):
        if not models:
            raise ValueError("AnalyzerPool requires at least one model ID.")
        self.client = client
        self.models = models

    async def analyze(
        self,
        content:       str,
        tasks:         list[dict],        # from Supervisor.decompose()
        round_num:     int,
        prior_reports: Optional[list[AnalyzerReport]] = None,
    ) -> list[AnalyzerReport]:
        """
        Run all analyzers in parallel.
        Models are paired to tasks by index (cycling if more models than tasks).
        Returns one AnalyzerReport per model — errors are caught individually.
        """
        pairs: list[tuple[str, dict, str]] = []
        for i, model in enumerate(self.models):
            task  = tasks[i % len(tasks)] if tasks else {"angle": "General", "instruction": "Analyze thoroughly."}
            prior = ""
            if prior_reports:
                prev = next((r for r in prior_reports if r.model_id == model), None)
                if prev and prev.issues:
                    prior = (
                        f"Prior round score: {prev.score}/10. "
                        f"Prior issues: {json.dumps(prev.issues[:5])}"
                    )
            pairs.append((model, task, prior))

        results = await asyncio.gather(
            *[self._single(model, task, content, round_num, prior)
              for model, task, prior in pairs],
            return_exceptions=True,
        )

        reports: list[AnalyzerReport] = []
        for i, result in enumerate(results):
            model_id = pairs[i][0]
            angle    = pairs[i][1].get("angle", f"Angle {i + 1}")
            if isinstance(result, Exception):
                reports.append(AnalyzerReport(
                    model_id=model_id, angle=angle,
                    score=0.0, verdict="ERROR",
                    issues=[], positive_findings=[],
                    summary="Analyzer failed to respond.",
                    error=str(result),
                ))
            else:
                reports.append(result)

        return reports

    async def _single(
        self,
        model:     str,
        task:      dict,
        content:   str,
        round_num: int,
        prior:     str,
    ) -> AnalyzerReport:
        angle       = task.get("angle",       "General")
        instruction = task.get("instruction", "Analyze the content thoroughly.")

        prompt = analyzer_prompt(
            angle          = angle,
            instruction    = instruction,
            content        = content,
            round_num      = round_num,
            prior_feedback = prior,
        )

        raw = await self.client.chat(
            model       = model,
            messages    = [
                {"role": "system", "content": ANALYZER_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature = 0.2,
            json_mode   = True,
        )

        parsed = self._parse(raw, model)
        return AnalyzerReport(
            model_id          = model,
            angle             = parsed.get("angle", angle),
            score             = float(parsed.get("score", 0)),
            verdict           = parsed.get("verdict", "ISSUES_FOUND"),
            issues            = parsed.get("issues", []),
            positive_findings = parsed.get("positive_findings", []),
            summary           = parsed.get("summary", ""),
            raw               = parsed,
        )

    @staticmethod
    def _parse(raw: str, model: str) -> dict:
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
                    f"Analyzer {model}: JSON parse failed — {e}\n"
                    f"Raw (first 300 chars): {raw[:300]}"
                )
