"""
crosscheck.streaming
--------------------
Phase 1 Feature 5: Streaming output + enhanced progress UX.

Provides:
  - StreamingSession     Wraps MultiAgentSession with real-time streaming events
  - StreamEvent          Typed event union for consumers
  - StreamingReporter    Rich Live layout with live spinner, scores, and feed

Architecture:
  The pipeline emits events via an asyncio.Queue.
  Consumers (CLI, VS Code extension, web dashboard) subscribe to the queue.
  
  Event types:
    "round_start"        round number starting
    "decompose_done"     supervisor decomposed task
    "analyzer_start"     single analyzer beginning
    "analyzer_done"      single analyzer completed with score
    "synthesizing"       supervisor is synthesizing
    "synthesis_done"     verdict returned
    "coder_start"        coder beginning revision
    "coder_done"         coder finished
    "session_complete"   final result
    "anomaly"            InsAIts anomaly detected
    "tool_result"        sandbox tool result
    "error"              pipeline error
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    kind:      str          # one of the event types above
    round_num: int   = 0
    data:      Any   = None
    ts:        float = field(default_factory=time.monotonic)

    # Convenience accessors
    @property
    def analyzer_report(self):
        return self.data if self.kind == "analyzer_done" else None

    @property
    def synthesis(self):
        return self.data if self.kind == "synthesis_done" else None


# ---------------------------------------------------------------------------
# Streaming session
# ---------------------------------------------------------------------------

class StreamingSession:
    """
    Wraps MultiAgentSession and emits structured StreamEvents via an
    asyncio.Queue so consumers can react in real-time.
    
    Usage:
        session = StreamingSession(api_key="sk-or-...", ...)
        async for event in session.stream("def foo(): pass"):
            print(event.kind, event.data)
    """

    def __init__(self, api_key: str, **kwargs):
        self._api_key = api_key
        self._kwargs  = kwargs
        self._queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

    async def stream(self, content: str) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvents as the session progresses."""
        from crosscheck.core import MultiAgentSession

        queue = self._queue

        def on_round_start(n):
            queue.put_nowait(StreamEvent(kind="round_start", round_num=n))

        def on_decompose(decompose_dict):
            queue.put_nowait(StreamEvent(kind="decompose_done", data=decompose_dict))

        def on_analyzer(report):
            queue.put_nowait(StreamEvent(
                kind      = "analyzer_done",
                round_num = 0,
                data      = report,
            ))

        def on_verdict(synthesis):
            queue.put_nowait(StreamEvent(kind="synthesis_done", data=synthesis))

        def on_coder(revised):
            queue.put_nowait(StreamEvent(kind="coder_done", data=revised))

        def on_anomaly(event):
            queue.put_nowait(StreamEvent(kind="anomaly", data=event))

        def on_round_complete(rnd):
            queue.put_nowait(StreamEvent(
                kind      = "round_complete",
                round_num = rnd.number,
                data      = rnd,
            ))

        session = MultiAgentSession(
            api_key                 = self._api_key,
            on_round_start          = on_round_start,
            on_supervisor_decompose = on_decompose,
            on_analyzer_complete    = on_analyzer,
            on_supervisor_verdict   = on_verdict,
            on_coder_output         = on_coder,
            on_anomaly              = on_anomaly,
            on_round_complete       = on_round_complete,
            **self._kwargs,
        )

        # Run session in a background task
        task = asyncio.create_task(session.run_async(content))

        # Yield events as they arrive
        while True:
            try:
                # Poll queue; also check if task is done
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                if event is None:
                    break
                yield event
            except asyncio.TimeoutError:
                if task.done():
                    # Drain remaining events
                    while not queue.empty():
                        event = queue.get_nowait()
                        if event is not None:
                            yield event
                    break

        # Emit final result
        result = await task
        yield StreamEvent(kind="session_complete", data=result)


# ---------------------------------------------------------------------------
# Streaming Rich reporter (Live layout)
# ---------------------------------------------------------------------------

class StreamingReporter:
    """
    Real-time Rich Live display with:
      - Spinning progress bar per round
      - Live analyzer score feed
      - Verdict panel that updates in place
    """

    def __init__(self, max_rounds: int, console: Console | None = None):
        self.max_rounds  = max_rounds
        self.console     = console or Console()
        self._round      = 0
        self._scores: list[tuple[str, float, str]] = []  # (model_short, score, verdict)
        self._verdict    = ""
        self._score      = 0.0
        self._live:      Live | None = None
        self._progress:  Progress | None = None
        self._task_id    = None
        self._start_time = time.monotonic()

    def start(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            TextColumn("[dim]{task.elapsed:.0f}s"),
            console   = self.console,
            transient = False,
        )
        self._task_id = self._progress.add_task(
            "Initializing…", total=self.max_rounds
        )
        self._live = Live(
            self._make_layout(),
            console     = self.console,
            refresh_rate= 4,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def on_event(self, event: StreamEvent) -> None:
        if event.kind == "round_start":
            self._round = event.round_num
            self._scores = []
            if self._progress and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=f"Round {self._round} — decomposing…",
                )

        elif event.kind == "decompose_done":
            if self._progress and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=f"Round {self._round} — analyzers running…",
                )

        elif event.kind == "analyzer_done" and event.data:
            r = event.data
            model_short = r.model_id.split("/")[-1][:20]
            self._scores.append((model_short, r.score, r.verdict))

        elif event.kind == "synthesis_done" and event.data:
            s              = event.data
            self._verdict  = s.verdict
            self._score    = s.overall_score
            if self._progress and self._task_id is not None:
                color = "green" if s.verdict == "APPROVED" else "yellow"
                self._progress.update(
                    self._task_id,
                    description=(
                        f"Round {self._round} — [{color}]{s.verdict}[/{color}] "
                        f"{s.overall_score:.1f}/10"
                    ),
                    advance=1,
                )

        if self._live:
            self._live.update(self._make_layout())

    def _make_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._make_progress_panel(), name="progress", size=3),
            Layout(self._make_feed_panel(), name="feed"),
        )
        return layout

    def _make_progress_panel(self) -> Panel:
        return Panel(
            self._progress or Text(""),
            title        = f"[bold cyan]crosscheck-ai[/bold cyan]  Round {self._round}/{self.max_rounds}",
            border_style = "cyan",
            padding      = (0, 1),
        )

    def _make_feed_panel(self) -> Panel:
        if not self._scores:
            content = Text("Waiting for analyzers…", style="dim")
        else:
            table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
            table.add_column("Analyzer",  style="cyan", max_width=22)
            table.add_column("Score",     width=8)
            table.add_column("Verdict",   width=14)
            for model, score, verdict in self._scores:
                color = "green" if score >= 8 else ("yellow" if score >= 5 else "red")
                v_col = "green" if verdict == "PASS" else "yellow"
                table.add_row(
                    model,
                    f"[{color}]{score:.1f}[/{color}]",
                    f"[{v_col}]{verdict}[/{v_col}]",
                )
            content = table

        border_color = (
            "green" if self._verdict == "APPROVED"
            else ("red" if self._verdict == "REVISE" else "dim")
        )
        title = "Analyzer Feed"
        if self._verdict:
            v_col  = "green bold" if self._verdict == "APPROVED" else "red bold"
            title += f"  [{v_col}]{self._verdict}[/{v_col}] {self._score:.1f}/10"

        return Panel(content, title=title, border_style=border_color, padding=(0, 1))


# ---------------------------------------------------------------------------
# Convenience: stream_review()
# ---------------------------------------------------------------------------

async def stream_review(
    content:    str,
    api_key:    str,
    on_event:   Callable[[StreamEvent], None] | None = None,
    **kwargs,
) -> Any:
    """
    Stream a review and optionally call on_event for each StreamEvent.
    Returns the final SessionResult.
    """
    session = StreamingSession(api_key=api_key, **kwargs)
    result  = None
    async for event in session.stream(content):
        if on_event:
            on_event(event)
        if event.kind == "session_complete":
            result = event.data
    return result
