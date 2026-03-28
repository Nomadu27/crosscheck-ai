"""
crosscheck.observer
--------------------
Observer mode: crosscheck-ai acts as a live supervisor watching an EXTERNAL
coding session — whether that's another AI tool (Cursor, Aider, GitHub Copilot,
Claude Code) or a developer typing code.

Two sub-modes:
  1. PASTE mode  — user pastes a block of code/diff, gets instant review
  2. WATCH mode  — monitors a folder for file changes in real-time,
                   auto-reviews every save, flags bugs and plan drift

The observer uses the full supervisor + analyzer pipeline but in a leaner
"single-round verdict" mode — no coder revision, just PASS/FLAG.

Flags raised:
  - BUG:        detected logic error, exception risk, or crash scenario
  - PLAN_DRIFT: code doesn't follow the plan/spec provided
  - SECURITY:   security vulnerability introduced
  - REGRESSION: change likely breaks existing functionality
  - STYLE:      minor quality issue (low severity, non-blocking)

InsAIts monitors every supervisor↔analyzer message in observer mode too.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from crosscheck.agents.analyzer   import AnalyzerPool, AnalyzerReport
from crosscheck.agents.supervisor import SupervisorAgent, SynthesisResult
from crosscheck.client            import OpenRouterClient
from crosscheck.models            import Mode, Task, OBSERVER_DEFAULT_SUPERVISORS, MODE_PRESETS
from crosscheck.monitor           import CrosscheckMonitor, NoOpMonitor, AnomalyEvent


# ── Flag types ────────────────────────────────────────────────────────────────

FLAG_COLORS = {
    "BUG":        "red bold",
    "PLAN_DRIFT": "magenta bold",
    "SECURITY":   "red bold",
    "REGRESSION": "yellow bold",
    "STYLE":      "dim",
    "PASS":       "green",
}

FLAG_ICONS = {
    "BUG":        "🐛",
    "PLAN_DRIFT": "🚨",
    "SECURITY":   "🔐",
    "REGRESSION": "⚠️",
    "STYLE":      "💅",
    "PASS":       "✅",
}


@dataclass
class ObserverFlag:
    flag_type:   str           # BUG | PLAN_DRIFT | SECURITY | REGRESSION | STYLE | PASS
    severity:    str           # critical | major | minor
    location:    str           # file path or "line N"
    description: str
    fix:         str
    model_id:    str           # which analyzer raised it
    angle:       str


@dataclass
class ObserverResult:
    """Result of a single observer check (one paste or one file-save event)."""
    passed:       bool
    flags:        list[ObserverFlag]
    score:        float
    summary:      str
    file_path:    str          # "" for paste mode
    duration_sec: float
    anomaly_count: int
    supervisor_votes: list[str] = field(default_factory=list)
    consensus:    bool = True
    cost_usd:     float = 0.0


# ── Observer session ──────────────────────────────────────────────────────────

class ObserverSession:
    """
    Runs one review pass on a content snippet.
    Used by both paste mode (once) and watch mode (per file-save).

    Supervisors: 2 models by default (dual-supervisor voting applies).
    Analyzers:   3-4 models, same as review mode.
    No coder — observer never modifies files, only reports.
    """

    def __init__(
        self,
        api_key:      str,
        supervisors:  Optional[list[str]] = None,
        analyzers:    Optional[list[str]] = None,
        mode:         Mode                = Mode.BALANCED,
        plan:         str                 = "",
        monitor:      bool                = False,
        insaits_api_key: Optional[str]    = None,
        on_flag:      Optional[Callable[[ObserverFlag], None]] = None,
        on_anomaly:   Optional[Callable[[AnomalyEvent], None]] = None,
    ):
        self.api_key    = api_key
        self.plan       = plan       # optional spec/plan to check drift against
        self.on_flag    = on_flag
        self.on_anomaly = on_anomaly

        preset = MODE_PRESETS[mode]
        self._supervisor_models = supervisors or OBSERVER_DEFAULT_SUPERVISORS
        self._analyzer_models   = analyzers   or preset["analyzers"]

        if monitor:
            self._monitor = CrosscheckMonitor(
                api_key       = insaits_api_key,
                anchor_prompt = "Supervise external coding session for bugs and plan drift",
                enabled       = True,
                on_anomaly    = on_anomaly,
            )
        else:
            self._monitor = NoOpMonitor()

    async def check(self, content: str, file_path: str = "") -> ObserverResult:
        """
        Run a single observer pass on `content`.
        Returns ObserverResult with all flags and verdict.
        """
        t0 = time.monotonic()

        async with OpenRouterClient(self.api_key) as client:
            supervisor = SupervisorAgent(
                client     = client,
                models     = self._supervisor_models,
                task       = Task.CODE,
                max_rounds = 1,
            )
            analyzer_pool = AnalyzerPool(
                client = client,
                models = self._analyzer_models,
            )

            # Build observer-specific content: inject the plan if provided
            observed_content = content
            if self.plan:
                observed_content = (
                    f"=== ORIGINAL PLAN / SPEC ===\n{self.plan}\n\n"
                    f"=== CODE UNDER REVIEW ===\n{content}"
                )

            # Supervisor decomposes (uses observer-tuned angles)
            decompose = await supervisor.decompose(observed_content, round_num=1)

            # Monitor supervisor → analyzers
            mon1 = await self._monitor.check_supervisor_to_analyzers(
                str(decompose.__dict__), round_num=1
            )

            # Analyzers run in parallel
            analyzer_reports = await analyzer_pool.analyze(
                content   = observed_content,
                tasks     = decompose.analyzer_tasks,
                round_num = 1,
            )

            # Monitor analyzers → supervisor
            mon2 = await self._monitor.check_analyzers_to_supervisor(
                [r.raw for r in analyzer_reports], round_num=1
            )

            # Supervisor synthesizes (dual-voting if 2 supervisors)
            synthesis = await supervisor.synthesize(
                [r.raw for r in analyzer_reports], round_num=1
            )

        # Convert analyzer issues → ObserverFlags
        flags = self._extract_flags(analyzer_reports, synthesis)
        for flag in flags:
            if self.on_flag:
                self.on_flag(flag)

        anomaly_count = len(
            mon1.anomalies + mon2.anomalies + self._monitor.all_anomalies
        )

        return ObserverResult(
            passed           = synthesis.verdict == "APPROVED" and not any(
                f.flag_type in ("BUG", "SECURITY") for f in flags
            ),
            flags            = flags,
            score            = synthesis.overall_score,
            summary          = synthesis.summary,
            file_path        = file_path,
            duration_sec     = time.monotonic() - t0,
            anomaly_count    = anomaly_count,
            supervisor_votes = synthesis.supervisor_votes,
            consensus        = synthesis.consensus,
            cost_usd         = client.total_cost_usd,
        )

    @staticmethod
    def _extract_flags(
        reports:   list[AnalyzerReport],
        synthesis: SynthesisResult,
    ) -> list[ObserverFlag]:
        """Convert raw analyzer issues into typed ObserverFlags."""
        flags: list[ObserverFlag] = []

        # No issues from any analyzer → PASS
        all_issues = [i for r in reports for i in r.issues]
        if not all_issues and synthesis.verdict == "APPROVED":
            flags.append(ObserverFlag(
                flag_type="PASS", severity="none", location="",
                description="No issues detected.", fix="",
                model_id="", angle="",
            ))
            return flags

        for report in reports:
            for issue in report.issues:
                desc = issue.get("description", "").lower()
                sev  = issue.get("severity", "minor")

                # Classify flag type from issue description keywords
                if any(k in desc for k in (
                    "bug", "crash", "exception", "error", "null", "none", "undefined",
                    "divide by zero", "index out", "key error", "type error", "logic",
                )):
                    flag_type = "BUG"
                elif any(k in desc for k in (
                    "injection", "xss", "sql", "overflow", "unauthorized", "secret", "leak",
                    "hardcod", "exposur", "vuln", "unsafe", "shell", "exec",
                )):
                    flag_type = "SECURITY"
                elif any(k in desc for k in (
                    "plan", "spec", "requirement", "drift", "deviat", "doesn't follow",
                    "not implement", "missing feature", "wrong approach",
                )):
                    flag_type = "PLAN_DRIFT"
                elif any(k in desc for k in (
                    "break", "regression", "backward", "compat", "existing", "test fail",
                )):
                    flag_type = "REGRESSION"
                else:
                    flag_type = "STYLE"

                flags.append(ObserverFlag(
                    flag_type   = flag_type,
                    severity    = sev,
                    location    = issue.get("location", ""),
                    description = issue.get("description", ""),
                    fix         = issue.get("fix", ""),
                    model_id    = report.model_id,
                    angle       = report.angle,
                ))

        return flags


# ── Watch mode ────────────────────────────────────────────────────────────────

class FolderWatcher:
    """
    Watches a directory for file changes and runs an ObserverSession
    on every modified file. Debounces rapid saves (500ms window).

    Usage:
        watcher = FolderWatcher(session, path=".", extensions=[".py", ".ts"])
        watcher.start()          # blocking — Ctrl-C to stop
    """

    # Default extensions to watch
    DEFAULT_EXTENSIONS = {
        ".py", ".js", ".ts", ".tsx", ".jsx",
        ".kt", ".swift", ".go", ".rs", ".java",
        ".cpp", ".c", ".cs", ".rb", ".php",
    }

    def __init__(
        self,
        session:    ObserverSession,
        path:       str               = ".",
        extensions: Optional[set[str]] = None,
        on_result:  Optional[Callable[[ObserverResult], None]] = None,
        debounce_s: float             = 0.5,
    ):
        self.session    = session
        self.watch_path = Path(path).resolve()
        self.extensions = extensions or self.DEFAULT_EXTENSIONS
        self.on_result  = on_result
        self.debounce_s = debounce_s
        self._pending:  dict[str, float] = {}   # path → last_modified time
        self._observer  = None

    def start(self):
        """Start watching. Blocks until KeyboardInterrupt."""
        try:
            from watchdog.observers import Observer
            from watchdog.events    import FileSystemEventHandler
        except ImportError:
            raise ImportError(
                "watchdog is required for --watch mode.\n"
                "Install: pip install watchdog"
            )

        handler = self._make_handler()
        self._observer = Observer()
        self._observer.schedule(handler, str(self.watch_path), recursive=True)
        self._observer.start()

        try:
            asyncio.run(self._debounce_loop())
        except KeyboardInterrupt:
            pass
        finally:
            self._observer.stop()
            self._observer.join()

    def _make_handler(self):
        """Create a watchdog event handler that records modified files."""
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                p = Path(event.src_path)
                if p.suffix.lower() in watcher.extensions:
                    watcher._pending[str(p)] = time.monotonic()

            def on_created(self, event):
                self.on_modified(event)

        return _Handler()

    async def _debounce_loop(self):
        """Drain the pending queue after the debounce window elapses."""
        while True:
            await asyncio.sleep(self.debounce_s)
            now      = time.monotonic()
            to_check = [
                path for path, ts in list(self._pending.items())
                if now - ts >= self.debounce_s
            ]
            for path in to_check:
                del self._pending[path]
                await self._review_file(path)

    async def _review_file(self, path: str):
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        result = await self.session.check(content, file_path=path)
        if self.on_result:
            self.on_result(result)
