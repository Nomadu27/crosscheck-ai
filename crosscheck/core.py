"""
crosscheck.core
---------------
MultiAgentSession: the main orchestrator.

Flow per round:
  1. Supervisor(s) decompose task → analyzer instructions
     [With dual supervisors: primary decomposes, both vote on synthesis]
  2. InsAIts monitors supervisor→analyzers
  3. Analyzer pool runs in parallel (3-4 models)
  4. InsAIts monitors analyzers→supervisor
  5. Supervisor(s) synthesize → APPROVED or REVISE
     [Dual-supervisor voting: unanimous or near-perfect exception rule]
  6. If REVISE: InsAIts monitors supervisor→coder instructions
  7. Coder revises content (any model — user picks)
  8. InsAIts monitors coder output
  9. Repeat until APPROVED or max_rounds reached

The session exposes both async (run_async) and sync (run) interfaces.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from crosscheck.agents.analyzer   import AnalyzerPool, AnalyzerReport
from crosscheck.agents.coder      import CoderAgent
from crosscheck.agents.supervisor import SupervisorAgent, SynthesisResult
from crosscheck.client            import OpenRouterClient
from crosscheck.models            import Mode, Task, MODE_PRESETS, TASK_BEST
from crosscheck.monitor           import CrosscheckMonitor, NoOpMonitor, AnomalyEvent


@dataclass
class Round:
    number:           int
    content_in:       str
    content_out:      str
    decompose:        dict
    analyzer_reports: list[AnalyzerReport]
    synthesis:        SynthesisResult
    anomalies:        list[AnomalyEvent] = field(default_factory=list)
    duration_sec:     float = 0.0


@dataclass
class SessionResult:
    verdict:            str               # "APPROVED" | "MAX_ROUNDS_REACHED"
    final_content:      str
    rounds:             list[Round]
    total_rounds:       int
    final_score:        float
    issues_fixed:       int
    total_tokens:       int
    cost_usd:           float
    duration_sec:       float
    anomaly_count:      int
    analyzer_reports:   list[AnalyzerReport]   # last round's reports
    supervisor_votes:   list[str] = field(default_factory=list)  # e.g. ["APPROVED","REVISE"]
    supervisor_consensus: bool    = True


class MultiAgentSession:
    """
    Main public interface for the crosscheck-ai library.

    Any OpenRouter model can be assigned to any role:
        session = MultiAgentSession(
            api_key="sk-or-...",
            supervisors=["x-ai/grok-4", "anthropic/claude-opus-4.6"],  # dual supervisor
            analyzers=["deepseek/deepseek-r1", "moonshotai/kimi-k2", "qwen/qwen3-235b-a22b"],
            coder="deepseek/deepseek-chat-v3-0324",  # any model, not just Anthropic
        )
    """

    def __init__(
        self,
        api_key:         str,
        review_type:     Task                 = Task.CODE,
        mode:            Mode                 = Mode.BALANCED,
        max_rounds:      int                  = 5,

        # Model selection — override mode presets freely
        supervisors:     Optional[list[str]]  = None,
        analyzers:       Optional[list[str]]  = None,
        coder:           Optional[str]        = None,

        # Auto-select task-optimised models instead of mode preset
        auto_models:     bool                 = False,

        # InsAIts integration
        monitor:         bool                 = False,
        insaits_api_key: Optional[str]        = None,
        monitor_anchor:  str                  = "",

        # Callbacks (all optional)
        on_round_start:          Optional[Callable[[int], None]]              = None,
        on_supervisor_decompose: Optional[Callable[[dict], None]]             = None,
        on_analyzer_complete:    Optional[Callable[[AnalyzerReport], None]]   = None,
        on_supervisor_verdict:   Optional[Callable[[SynthesisResult], None]]  = None,
        on_coder_output:         Optional[Callable[[str], None]]              = None,
        on_anomaly:              Optional[Callable[[AnomalyEvent], None]]     = None,
        on_round_complete:       Optional[Callable[[Round], None]]            = None,
    ):
        self.api_key     = api_key
        self.review_type = review_type
        self.mode        = mode
        self.max_rounds  = max_rounds

        # Resolve model config: explicit > auto_models > mode preset
        preset = TASK_BEST[review_type] if auto_models else MODE_PRESETS[mode]
        self._supervisor_models = supervisors or preset["supervisors"]
        self._analyzer_models   = analyzers   or preset["analyzers"]
        self._coder_model       = coder       or preset["coder"]

        # Monitor
        if monitor:
            self._monitor = CrosscheckMonitor(
                api_key       = insaits_api_key,
                anchor_prompt = monitor_anchor or (
                    f"Review {review_type.value} content for quality and correctness"
                ),
                enabled    = True,
                on_anomaly = on_anomaly,
            )
        else:
            self._monitor = NoOpMonitor()

        # Callbacks
        self.on_round_start          = on_round_start
        self.on_supervisor_decompose = on_supervisor_decompose
        self.on_analyzer_complete    = on_analyzer_complete
        self.on_supervisor_verdict   = on_supervisor_verdict
        self.on_coder_output         = on_coder_output
        self.on_round_complete       = on_round_complete

    # ── Public API ────────────────────────────────────────────────────────

    async def run_async(self, content: str) -> SessionResult:
        """Run the full multi-agent review pipeline asynchronously."""
        session_start = time.monotonic()
        rounds: list[Round] = []
        current_content = content
        prior_reports: Optional[list[AnalyzerReport]] = None
        issues_fixed = 0
        final_score  = 0.0
        verdict      = "MAX_ROUNDS_REACHED"
        last_votes:   list[str] = []
        last_consensus: bool    = True

        async with OpenRouterClient(self.api_key) as client:
            supervisor = SupervisorAgent(
                client     = client,
                models     = self._supervisor_models,
                task       = self.task,
                max_rounds = self.max_rounds,
            )
            analyzer_pool = AnalyzerPool(
                client = client,
                models = self._analyzer_models,
            )
            coder = CoderAgent(
                client = client,
                model  = self._coder_model,
                task   = self.task,
            )

            for round_num in range(1, self.max_rounds + 1):
                round_start     = time.monotonic()
                round_anomalies: list[AnomalyEvent] = []

                if self.on_round_start:
                    self.on_round_start(round_num)

                # ── Step 1: Decompose ───────────────────────────────────
                decompose = await supervisor.decompose(current_content, round_num)
                if self.on_supervisor_decompose:
                    self.on_supervisor_decompose(decompose.__dict__)

                mon1 = await self._monitor.check_supervisor_to_analyzers(
                    str(decompose.__dict__), round_num
                )
                round_anomalies.extend(mon1.anomalies)

                # ── Step 2: Analyze ─────────────────────────────────────
                analyzer_reports = await analyzer_pool.analyze(
                    content       = current_content,
                    tasks         = decompose.analyzer_tasks,
                    round_num     = round_num,
                    prior_reports = prior_reports,
                )
                for r in analyzer_reports:
                    if self.on_analyzer_complete:
                        self.on_analyzer_complete(r)

                mon2 = await self._monitor.check_analyzers_to_supervisor(
                    [r.raw for r in analyzer_reports], round_num
                )
                round_anomalies.extend(mon2.anomalies)

                # ── Step 3: Synthesize (with dual-supervisor voting) ────
                synthesis = await supervisor.synthesize(
                    [r.raw for r in analyzer_reports], round_num
                )
                final_score    = synthesis.overall_score
                last_votes     = synthesis.supervisor_votes
                last_consensus = synthesis.consensus

                if self.on_supervisor_verdict:
                    self.on_supervisor_verdict(synthesis)

                # ── Approval check ──────────────────────────────────────
                if synthesis.verdict == "APPROVED" or round_num == self.max_rounds:
                    verdict = (
                        "APPROVED"
                        if synthesis.verdict == "APPROVED"
                        else "MAX_ROUNDS_REACHED"
                    )
                    rounds.append(Round(
                        number           = round_num,
                        content_in       = current_content,
                        content_out      = current_content,
                        decompose        = decompose.__dict__,
                        analyzer_reports = analyzer_reports,
                        synthesis        = synthesis,
                        anomalies        = round_anomalies,
                        duration_sec     = time.monotonic() - round_start,
                    ))
                    break

                # ── Step 4: Monitor supervisor → coder ─────────────────
                mon3 = await self._monitor.check_supervisor_to_coder(
                    synthesis.coder_instructions, round_num
                )
                round_anomalies.extend(mon3.anomalies)

                # High-severity InsAIts anomaly: re-query supervisor before coder
                if mon3.should_block:
                    synthesis = await supervisor.synthesize(
                        [r.raw for r in analyzer_reports], round_num
                    )

                # ── Step 5: Coder revises ───────────────────────────────
                critical_issues = [
                    issue
                    for r in analyzer_reports
                    for issue in r.issues
                    if issue.get("severity") in ("critical", "major")
                ]
                issues_fixed += len(critical_issues)

                revised = await coder.revise(
                    content      = current_content,
                    instructions = synthesis.coder_instructions,
                    issues       = critical_issues,
                    round_num    = round_num,
                )
                if self.on_coder_output:
                    self.on_coder_output(revised)

                mon4 = await self._monitor.check_coder_output(revised, round_num)
                round_anomalies.extend(mon4.anomalies)

                # ── Record round ────────────────────────────────────────
                round_obj = Round(
                    number           = round_num,
                    content_in       = current_content,
                    content_out      = revised,
                    decompose        = decompose.__dict__,
                    analyzer_reports = analyzer_reports,
                    synthesis        = synthesis,
                    anomalies        = round_anomalies,
                    duration_sec     = time.monotonic() - round_start,
                )
                if self.on_round_complete:
                    self.on_round_complete(round_obj)
                rounds.append(round_obj)

                current_content = revised
                prior_reports   = analyzer_reports

        last_rpt = rounds[-1].analyzer_reports if rounds else []

        return SessionResult(
            verdict              = verdict,
            final_content        = current_content,
            rounds               = rounds,
            total_rounds         = len(rounds),
            final_score          = final_score,
            issues_fixed         = issues_fixed,
            total_tokens         = client.total_tokens,
            cost_usd             = client.total_cost_usd,
            duration_sec         = time.monotonic() - session_start,
            anomaly_count        = len(self._monitor.all_anomalies),
            analyzer_reports     = last_rpt,
            supervisor_votes     = last_votes,
            supervisor_consensus = last_consensus,
        )

    def run(self, content: str) -> SessionResult:
        """Synchronous convenience wrapper around run_async."""
        return asyncio.run(self.run_async(content))

    @property
    def task(self) -> Task:
        return self.review_type

    @property
    def model_config(self) -> dict:
        return {
            "supervisors": self._supervisor_models,
            "analyzers":   self._analyzer_models,
            "coder":       self._coder_model,
        }
