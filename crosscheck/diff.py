"""
crosscheck.diff
---------------
Phase 1 Feature 2: Unified diff output + side-by-side view.

Produces:
  - unified_diff(before, after)   → standard unified diff string
  - side_by_side(before, after)   → rich-renderable side-by-side table
  - print_diff(before, after)     → prints to terminal via Rich
  - diff_stats(before, after)     → addition/deletion counts

CLI flag --show-diff triggers print_diff after a session completes.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class DiffStats:
    additions:    int
    deletions:    int
    changes:      int
    files_changed: int = 1

    @property
    def net_change(self) -> int:
        return self.additions - self.deletions

    def summary(self) -> str:
        return (
            f"+{self.additions} additions  "
            f"-{self.deletions} deletions  "
            f"~{self.changes} changes"
        )


# ---------------------------------------------------------------------------
# Core diff functions
# ---------------------------------------------------------------------------

def unified_diff(
    before:    str,
    after:     str,
    fromfile:  str = "original",
    tofile:    str = "revised",
    n:         int = 3,
) -> str:
    """Return a unified diff string (identical to git diff output style)."""
    before_lines = before.splitlines(keepends=True)
    after_lines  = after.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile = f"a/{fromfile}",
        tofile   = f"b/{tofile}",
        n        = n,
    ))

    if not diff_lines:
        return ""   # no changes

    return "".join(diff_lines)


def diff_stats(before: str, after: str) -> DiffStats:
    """Count additions, deletions, and change lines."""
    b = before.splitlines()
    a = after.splitlines()

    matcher = difflib.SequenceMatcher(None, b, a)
    adds    = 0
    dels    = 0
    chgs    = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            chgs += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            dels += i2 - i1
        elif tag == "insert":
            adds += j2 - j1

    return DiffStats(additions=adds, deletions=dels, changes=chgs)


def side_by_side(
    before:   str,
    after:    str,
    width:    int = 60,
    title_a:  str = "Original",
    title_b:  str = "Revised",
) -> Table:
    """
    Build a Rich Table with two columns: before (red highlights) and after (green highlights).
    Changed lines are colour-coded; identical lines are dim.
    """
    b_lines = before.splitlines()
    a_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(None, b_lines, a_lines)

    table = Table(
        title        = "Side-by-side Diff",
        box          = box.ROUNDED,
        border_style = "dim",
        show_lines   = True,
        padding      = (0, 1),
    )
    table.add_column(f"{title_a}", style="", max_width=width, overflow="fold")
    table.add_column(f"{title_b}", style="", max_width=width, overflow="fold")

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Show equal lines dim
            for bl, al in zip(b_lines[i1:i2], a_lines[j1:j2], strict=False):
                table.add_row(
                    Text(bl, style="dim"),
                    Text(al, style="dim"),
                )
        elif tag == "replace":
            # Pair up changed lines; pad the shorter side
            b_chunk = b_lines[i1:i2]
            a_chunk = a_lines[j1:j2]
            max_len = max(len(b_chunk), len(a_chunk))
            b_chunk += [""] * (max_len - len(b_chunk))
            a_chunk += [""] * (max_len - len(a_chunk))
            for bl, al in zip(b_chunk, a_chunk, strict=False):
                table.add_row(
                    Text(bl, style="red"),
                    Text(al, style="green"),
                )
        elif tag == "delete":
            for bl in b_lines[i1:i2]:
                table.add_row(Text(bl, style="red"), Text("", style=""))
        elif tag == "insert":
            for al in a_lines[j1:j2]:
                table.add_row(Text("", style=""), Text(al, style="green"))

    return table


# ---------------------------------------------------------------------------
# Rich terminal printer
# ---------------------------------------------------------------------------

def print_diff(
    before:    str,
    after:     str,
    fromfile:  str = "original",
    tofile:    str = "revised",
    mode:      str = "unified",   # "unified" | "side-by-side"
) -> None:
    """
    Print a diff to the terminal.
    mode="unified"    → coloured unified diff
    mode="side-by-side" → two-column table
    """
    if before == after:
        console.print("[dim]No changes between rounds.[/dim]")
        return

    stats = diff_stats(before, after)
    console.print()
    console.rule("[bold]Diff[/bold]")
    console.print(f"  [green]+{stats.additions}[/green]  [red]-{stats.deletions}[/red]  ~{stats.changes} changes")
    console.print()

    if mode == "side-by-side":
        table = side_by_side(before, after, title_a=fromfile, title_b=tofile)
        console.print(table)
    else:
        _print_unified(before, after, fromfile, tofile)

    console.print()


def _print_unified(before: str, after: str, fromfile: str, tofile: str) -> None:
    """Print a coloured unified diff."""
    diff = unified_diff(before, after, fromfile=fromfile, tofile=tofile)
    if not diff:
        console.print("[dim]No changes.[/dim]")
        return

    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(f"[dim]{line}[/dim]")


# ---------------------------------------------------------------------------
# Multi-round diff summary (for SessionResult)
# ---------------------------------------------------------------------------

def print_session_diffs(rounds: list, mode: str = "unified") -> None:
    """
    Print diffs for every revision round in a session.
    `rounds` is a list of crosscheck.core.Round objects.
    """
    revisions = [r for r in rounds if r.content_in != r.content_out]
    if not revisions:
        console.print("[dim]No revisions were made.[/dim]")
        return

    for rnd in revisions:
        console.print(f"\n[bold cyan]Round {rnd.number} revision:[/bold cyan]")
        print_diff(
            before   = rnd.content_in,
            after    = rnd.content_out,
            fromfile = f"round_{rnd.number}_before",
            tofile   = f"round_{rnd.number}_after",
            mode     = mode,
        )
