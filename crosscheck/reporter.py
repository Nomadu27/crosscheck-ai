"""
crosscheck.reporter
-------------------
Output formatters for all result types:
  - print_result      : rich terminal report for SessionResult
  - print_observer    : rich terminal report for ObserverResult
  - to_json           : JSON string for SessionResult
  - to_markdown       : Markdown report for SessionResult
  - LiveReporter      : real-time progress during a session
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from crosscheck.core import Round, SessionResult
from crosscheck.monitor import AnomalyEvent

if TYPE_CHECKING:
    from crosscheck.observer import ObserverResult

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 8.0: return "green"
    if score >= 5.0: return "yellow"
    return "red"


def _verdict_color(verdict: str) -> str:
    return "green bold" if verdict == "APPROVED" else "red bold"


# ── Live reporter ─────────────────────────────────────────────────────────────

class LiveReporter:
    """Shows real-time progress during a review session via Rich progress bar."""

    def __init__(self):
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console    = console,
            transient  = True,
        )
        self._task_id = None

    def start(self, max_rounds: int) -> None:
        self._progress.start()
        self._task_id = self._progress.add_task(
            "[cyan]Initializing review pipeline...", total=max_rounds
        )

    def round_start(self, n: int) -> None:
        self._progress.update(
            self._task_id,
            description=f"[cyan]Round {n} — Decomposing task...",
        )

    def synthesizing(self, n: int) -> None:
        self._progress.update(
            self._task_id,
            description=f"[cyan]Round {n} — Supervisor(s) synthesizing...",
        )

    def anomaly(self, event: AnomalyEvent) -> None:
        self._progress.stop()
        sev_color = {
            "low":    "yellow",
            "medium": "orange3",
            "high":   "red bold",
        }.get(event.severity, "white")
        console.print(
            f"  [dim]InsAIts ⚠[/dim]  [{sev_color}]{event.severity.upper()}[/{sev_color}] "
            f"{event.anomaly_type} [{event.source}]: {event.description[:80]}"
        )
        self._progress.start()

    def round_complete(self, rnd: Round) -> None:
        self._progress.advance(self._task_id)
        sc    = rnd.synthesis.overall_score
        color = _score_color(sc)
        votes = rnd.synthesis.supervisor_votes
        vote_str = ""
        if len(votes) > 1:
            consensus = "✓" if rnd.synthesis.consensus else "split"
            vote_str = f"  votes=[{', '.join(votes)}] {consensus}"
        self._progress.update(
            self._task_id,
            description=(
                f"[dim]Round {rnd.number} done — "
                f"[{color}]{sc:.1f}/10[/{color}]  {rnd.synthesis.verdict}"
                f"{vote_str}[/dim]"
            ),
        )

    def stop(self) -> None:
        self._progress.stop()


# ── Session terminal report ───────────────────────────────────────────────────

def print_result(result: SessionResult) -> None:
    """Print a full rich terminal report for a completed review session."""
    verdict_color = _verdict_color(result.verdict)

    console.print()
    console.rule("[bold]crosscheck-ai  Session Complete[/bold]")
    console.print()

    # Verdict banner
    console.print(Panel(
        Text(result.verdict, justify="center", style=verdict_color),
        title        = "Final Verdict",
        border_style = verdict_color.replace(" bold", ""),
        padding      = (1, 4),
    ))
    console.print()

    # Session stats
    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    stats.add_column(style="dim")
    stats.add_column()
    stats.add_row("Rounds completed",    str(result.total_rounds))
    stats.add_row("Final score",         f"[{_score_color(result.final_score)}]{result.final_score:.1f}/10[/]")
    stats.add_row("Issues fixed",        str(result.issues_fixed))
    stats.add_row("Anomalies detected",  str(result.anomaly_count))
    stats.add_row("Total tokens",        f"{result.total_tokens:,}")
    stats.add_row("Est. cost",           f"${result.cost_usd:.4f}")
    stats.add_row("Duration",            f"{result.duration_sec:.1f}s")
    # Dual-supervisor vote summary
    if result.supervisor_votes:
        vote_str    = " vs ".join(result.supervisor_votes)
        consensus   = "✓ unanimous" if result.supervisor_consensus else "⚡ split (resolved)"
        stats.add_row("Supervisor votes", f"{vote_str}  {consensus}")
    console.print(stats)

    # Per-round summary table
    if result.rounds:
        console.print()
        rnd_table = Table(title="Round Summary", box=box.ROUNDED, border_style="dim")
        rnd_table.add_column("#",            style="dim", width=3)
        rnd_table.add_column("Score",        width=8)
        rnd_table.add_column("Verdict",      width=10)
        rnd_table.add_column("Issues",       width=8)
        rnd_table.add_column("Anomalies",    width=10)
        rnd_table.add_column("Votes",        width=20)
        rnd_table.add_column("Time",         width=7)

        for rnd in result.rounds:
            sc     = rnd.synthesis.overall_score
            issues = sum(len(r.issues) for r in rnd.analyzer_reports)
            votes  = "/".join(rnd.synthesis.supervisor_votes) if rnd.synthesis.supervisor_votes else "—"
            rnd_table.add_row(
                str(rnd.number),
                f"[{_score_color(sc)}]{sc:.1f}[/]",
                f"[{_verdict_color(rnd.synthesis.verdict).replace(' bold','')}]{rnd.synthesis.verdict}[/]",
                str(issues),
                str(len(rnd.anomalies)),
                votes,
                f"{rnd.duration_sec:.1f}s",
            )
        console.print(rnd_table)

    # Analyzer breakdown (last round)
    if result.analyzer_reports:
        console.print()
        a_table = Table(
            title        = "Analyzer Breakdown (Final Round)",
            box          = box.ROUNDED,
            border_style = "dim",
        )
        a_table.add_column("Model",   style="cyan", max_width=35)
        a_table.add_column("Origin",  width=4)
        a_table.add_column("Angle",   max_width=25)
        a_table.add_column("Score",   width=8)
        a_table.add_column("Verdict", width=14)
        a_table.add_column("Issues",  width=8)

        from crosscheck.models import get_by_id
        for r in result.analyzer_reports:
            sc     = r.score
            spec   = get_by_id(r.model_id)
            origin = "🇨🇳" if (spec and spec.origin == "chinese") else "🇺🇸"
            a_table.add_row(
                r.model_id.split("/")[-1],
                origin,
                r.angle[:25],
                f"[{_score_color(sc)}]{sc:.1f}[/]",
                f"[{'green' if r.verdict == 'PASS' else 'yellow'}]{r.verdict}[/]",
                str(len(r.issues)),
            )
        console.print(a_table)

    # Remaining critical issues (only if not APPROVED)
    last_synthesis = result.rounds[-1].synthesis if result.rounds else None
    if last_synthesis and last_synthesis.critical_issues and result.verdict != "APPROVED":
        console.print()
        console.print("[red bold]Remaining Critical Issues:[/red bold]")
        for issue in last_synthesis.critical_issues:
            console.print(f"  [red]•[/red] {issue}")

    console.print()


# ── Observer terminal report ──────────────────────────────────────────────────

def print_observer_result(result: ObserverResult, show_pass: bool = True) -> None:
    """
    Print a compact observer result to the terminal.
    Used by both paste and watch modes.
    show_pass: if False, only print if there are flags (quiet mode for --watch).
    """
    from crosscheck.observer import FLAG_COLORS, FLAG_ICONS

    if result.passed and not show_pass:
        return

    # Header line
    icon  = "✅" if result.passed else "🚨"
    color = "green" if result.passed else "red"
    path  = f"[dim]{result.file_path}[/dim]  " if result.file_path else ""
    console.print(
        f"\n{icon}  {path}[{color}]Score {result.score:.1f}/10[/{color}]  "
        f"[dim]{result.duration_sec:.1f}s  ${result.cost_usd:.4f}[/dim]"
    )

    # Dual-supervisor vote
    if len(result.supervisor_votes) > 1:
        vote_str  = " vs ".join(result.supervisor_votes)
        consensus = "unanimous" if result.consensus else "split → resolved"
        console.print(f"  [dim]Supervisor votes: {vote_str}  ({consensus})[/dim]")

    # Summary
    if result.summary:
        console.print(f"  [italic]{result.summary}[/italic]")

    # Flags
    critical_flags = [f for f in result.flags if f.flag_type not in ("PASS", "STYLE")]
    style_flags    = [f for f in result.flags if f.flag_type == "STYLE"]

    if not result.flags or (len(result.flags) == 1 and result.flags[0].flag_type == "PASS"):
        if show_pass:
            console.print("  [green]No issues detected.[/green]")
        return

    for flag in critical_flags:
        icon    = FLAG_ICONS.get(flag.flag_type, "⚠️")
        fcolor  = FLAG_COLORS.get(flag.flag_type, "white")
        loc_str = f"[dim][{flag.location}][/dim] " if flag.location else ""
        console.print(
            f"  {icon} [{fcolor}]{flag.flag_type}[/{fcolor}]  "
            f"{loc_str}[{flag.model_id.split('/')[-1]}] {flag.description}"
        )
        if flag.fix:
            console.print(f"     [dim]→ {flag.fix}[/dim]")

    if style_flags:
        console.print(f"  [dim]+ {len(style_flags)} style suggestion(s)[/dim]")

    if result.anomaly_count > 0:
        console.print(f"  [dim]InsAIts: {result.anomaly_count} anomaly(ies) detected[/dim]")


# ── JSON output ───────────────────────────────────────────────────────────────

def to_json(result: SessionResult, indent: int = 2) -> str:
    def serialize(obj):
        if hasattr(obj, "__dict__"): return obj.__dict__
        if hasattr(obj, "value"):    return obj.value
        return str(obj)

    data = {
        "verdict":              result.verdict,
        "final_score":          result.final_score,
        "total_rounds":         result.total_rounds,
        "issues_fixed":         result.issues_fixed,
        "anomaly_count":        result.anomaly_count,
        "total_tokens":         result.total_tokens,
        "cost_usd":             round(result.cost_usd, 6),
        "duration_sec":         round(result.duration_sec, 2),
        "supervisor_votes":     result.supervisor_votes,
        "supervisor_consensus": result.supervisor_consensus,
        "final_content":        result.final_content,
        "rounds": [
            {
                "number":       r.number,
                "duration_sec": round(r.duration_sec, 2),
                "synthesis": {
                    "verdict":          r.synthesis.verdict,
                    "overall_score":    r.synthesis.overall_score,
                    "summary":          r.synthesis.summary,
                    "critical_issues":  r.synthesis.critical_issues,
                    "approved_aspects": r.synthesis.approved_aspects,
                    "supervisor_votes": r.synthesis.supervisor_votes,
                    "consensus":        r.synthesis.consensus,
                },
                "analyzer_reports": [
                    {
                        "model":   a.model_id,
                        "angle":   a.angle,
                        "score":   a.score,
                        "verdict": a.verdict,
                        "issues":  a.issues,
                        "summary": a.summary,
                    }
                    for a in r.analyzer_reports
                ],
                "anomalies": [
                    {
                        "type":        an.anomaly_type,
                        "severity":    an.severity,
                        "source":      an.source,
                        "description": an.description,
                    }
                    for an in r.anomalies
                ],
            }
            for r in result.rounds
        ],
    }
    return json.dumps(data, indent=indent, default=serialize)


# ── Markdown report ───────────────────────────────────────────────────────────

def to_markdown(result: SessionResult, title: str = "crosscheck-ai Review Report") -> str:
    lines = [
        f"# {title}",
        "",
        f"**Verdict:** {'✅ APPROVED' if result.verdict == 'APPROVED' else '⚠️ ' + result.verdict}",
        f"**Final Score:** {result.final_score:.1f}/10",
        f"**Rounds:** {result.total_rounds}  |  **Issues Fixed:** {result.issues_fixed}  |  "
        f"**Anomalies:** {result.anomaly_count}  |  **Cost:** ${result.cost_usd:.4f}",
    ]

    if result.supervisor_votes:
        consensus = "unanimous" if result.supervisor_consensus else "split (resolved)"
        lines.append(f"**Supervisor Votes:** {' vs '.join(result.supervisor_votes)} — {consensus}")

    lines += ["", "---", ""]

    for rnd in result.rounds:
        lines += [
            f"## Round {rnd.number}",
            "",
            f"**Score:** {rnd.synthesis.overall_score:.1f}/10  |  "
            f"**Verdict:** {rnd.synthesis.verdict}",
            "",
            f"**Summary:** {rnd.synthesis.summary}",
            "",
        ]

        if rnd.synthesis.supervisor_votes:
            consensus = "unanimous" if rnd.synthesis.consensus else "split (resolved)"
            lines.append(
                f"**Supervisor votes:** {' vs '.join(rnd.synthesis.supervisor_votes)} — {consensus}"
            )
            lines.append("")

        if rnd.synthesis.critical_issues:
            lines.append("**Critical Issues:**")
            for issue in rnd.synthesis.critical_issues:
                lines.append(f"- {issue}")
            lines.append("")

        if rnd.synthesis.approved_aspects:
            lines.append("**Approved Aspects:**")
            for aspect in rnd.synthesis.approved_aspects:
                lines.append(f"- {aspect}")
            lines.append("")

        lines += [
            "**Analyzer Reports:**",
            "",
            "| Model | Origin | Angle | Score | Verdict | Issues |",
            "|---|---|---|---|---|---|",
        ]

        from crosscheck.models import get_by_id
        for r in rnd.analyzer_reports:
            spec   = get_by_id(r.model_id)
            origin = "🇨🇳" if (spec and spec.origin == "chinese") else "🇺🇸"
            model  = r.model_id.split("/")[-1]
            lines.append(
                f"| {model} | {origin} | {r.angle} | {r.score:.1f}/10 | {r.verdict} | {len(r.issues)} |"
            )
        lines.append("")

        if rnd.anomalies:
            lines.append("**InsAIts Anomalies:**")
            for an in rnd.anomalies:
                lines.append(
                    f"- [{an.severity.upper()}] `{an.source}`: "
                    f"{an.anomaly_type} — {an.description}"
                )
            lines.append("")

        lines += ["---", ""]

    lines += [
        "## Final Content",
        "",
        "```",
        result.final_content,
        "```",
    ]

    return "\n".join(lines)
