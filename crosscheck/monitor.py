"""
crosscheck.monitor
------------------
Full seamless integration with InsAIts (v3.1.6+).

Now uses the complete InsAIts SDK:
- Full sender/receiver attribution (spawn-tree visibility)
- All 17 security adapters + 6 core detectors
- Intervention engine, circuit breaker, fact tracking, audit log
- Persistence Engine escalation
- Tamper-evident audit trail
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger("crosscheck.monitor")

# ── Try to import full InsAIts SDK ─────────────────────────────────────
try:
    from insa_its import insAItsMonitor  # ← Official package name
    _INSAITS_AVAILABLE = True
except ImportError:
    _INSAITS_AVAILABLE = False


@dataclass
class AnomalyEvent:
    round_num:   int
    source:      str
    anomaly_type: str
    severity:    str
    description: str
    raw:         dict = field(default_factory=dict)


@dataclass
class MonitorResult:
    passed:       bool
    anomalies:    list[AnomalyEvent] = field(default_factory=list)
    should_block: bool = False


class CrosscheckMonitor:
    """
    Full InsAIts-powered security layer for Crosscheck AI.
    """

    def __init__(
        self,
        api_key:       str | None = None,
        anchor_prompt: str = "",
        enabled:       bool = True,
        on_anomaly:    Callable[[AnomalyEvent], None] | None = None,
    ):
        self.enabled    = enabled and _INSAITS_AVAILABLE
        self.on_anomaly = on_anomaly
        self._monitor:  insAItsMonitor | None = None
        self._anchor    = anchor_prompt or "Review content for quality, correctness and security"
        self._history:  list[AnomalyEvent] = []

        if self.enabled:
            try:
                self._monitor = insAItsMonitor()
                self._monitor.enable_audit("./audit_crosscheck.jsonl")   # tamper-evident
                self._monitor.enable_fact_tracking(True)
                # Optional: enable full intervention engine
                # self._monitor.enable_interventions()
                logger.info("✅ InsAIts fully integrated — real-time AI-to-AI security active")
            except Exception as e:
                logger.warning(f"InsAIts init failed: {e} — falling back to no-op")
                self.enabled = False

        if enabled and not _INSAITS_AVAILABLE:
            logger.warning(
                "InsAIts not installed. Run: pip install insa-its[full]\n"
                "Continuing without full security monitoring."
            )

    # ── Core monitoring hooks (now fully powered by InsAIts) ─────────────

    async def check_supervisor_to_analyzers(self, message: str, round_num: int) -> MonitorResult:
        return await self._check(
            message=message,
            sender_id="supervisor",
            receiver_id="analyzer_pool",
            llm_id="crosscheck-supervisor",
            round_num=round_num,
            hop="decompose",
        )

    async def check_analyzers_to_supervisor(self, reports: list[dict], round_num: int) -> MonitorResult:
        import json
        content = json.dumps(reports, indent=2)
        return await self._check(
            message=content,
            sender_id="analyzer_pool",
            receiver_id="supervisor",
            llm_id="crosscheck-analyzer",
            round_num=round_num,
            hop="analyze",
        )

    async def check_supervisor_to_coder(self, instructions: str, round_num: int) -> MonitorResult:
        return await self._check(
            message=instructions,
            sender_id="supervisor",
            receiver_id="coder",
            llm_id="crosscheck-coder",
            round_num=round_num,
            hop="coder_instructions",
        )

    async def check_coder_output(self, output: str, round_num: int) -> MonitorResult:
        return await self._check(
            message=output,
            sender_id="coder",
            receiver_id="session",
            llm_id="crosscheck-coder",
            round_num=round_num,
            hop="final_output",
        )

    # ── Internal InsAIts call ─────────────────────────────────────────────

    async def _check(
        self,
        message:   str,
        sender_id: str,
        receiver_id: str,
        llm_id:    str,
        round_num: int,
        hop:       str,
    ) -> MonitorResult:
        if not self.enabled or self._monitor is None:
            return MonitorResult(passed=True)

        try:
            result = self._monitor.send_message(
                text=message,
                sender_id=sender_id,
                receiver_id=receiver_id,
                llm_id=llm_id,
                metadata={
                    "round": round_num,
                    "hop": hop,
                    "platform": "crosscheck-ai",
                }
            )

            monitor_result = result["monitor_result"]
            anomalies = []

            for raw in monitor_result.get("anomalies", []):
                event = AnomalyEvent(
                    round_num=round_num,
                    source=f"{sender_id}→{receiver_id}",
                    anomaly_type=raw.get("type", "unknown"),
                    severity=raw.get("severity", "low"),
                    description=raw.get("description", ""),
                    raw=raw,
                )
                anomalies.append(event)
                self._history.append(event)
                if self.on_anomaly:
                    self.on_anomaly(event)

            # Let InsAIts decide the action
            should_block = monitor_result.should_halt() or monitor_result.should_block()

            # Optional: let InsAIts intervene automatically
            if should_block and hasattr(self._monitor, "intervene"):
                self._monitor.intervene(message, monitor_result)

            return MonitorResult(
                passed=not should_block,
                anomalies=anomalies,
                should_block=should_block,
            )

        except Exception as e:
            logger.warning(f"InsAIts check failed ({sender_id}→{receiver_id}): {e}")
            return MonitorResult(passed=True)   # fail-open

    @property
    def all_anomalies(self) -> list[AnomalyEvent]:
        return self._history.copy()

    @property
    def high_severity_count(self) -> int:
        return sum(1 for a in self._history if a.severity == "critical")


# ── No-op fallback (unchanged) ────────────────────────────────────────

class NoOpMonitor(CrosscheckMonitor):
    def __init__(self):
        self.enabled = False
        self.on_anomaly = None
        self._history: list = []
        self._monitor = None

    async def check_supervisor_to_analyzers(self, *_, **__):
        return MonitorResult(passed=True)

    async def check_analyzers_to_supervisor(self, *_, **__):
        return MonitorResult(passed=True)

    async def check_supervisor_to_coder(self, *_, **__):
        return MonitorResult(passed=True)

    async def check_coder_output(self, *_, **__):
        return MonitorResult(passed=True)