"""
crosscheck.cli
--------------
Click-based CLI. Entry point: `crosscheck`

Commands:
  crosscheck review FILE     — full review pipeline
  crosscheck observe paste   — paste code and get instant observer report
  crosscheck observe watch   — watch a folder for changes, flag in real-time
  crosscheck models list     — list registered models
  crosscheck models fetch-latest — fetch live list from OpenRouter API
  crosscheck profiles        — list named profiles from crosscheck.toml
  crosscheck types           — list review types and analyzer angles
  crosscheck init            — create crosscheck.toml in current directory

Model selection priority (review command):
  1. --interactive    full wizard per tier
  2. --profile NAME   named profile from crosscheck.toml
  3. --supervisor/--analyzer/--coder  explicit flags
  4. --mode fast|balanced|quality     preset
  5. --auto-models    task-optimised preset
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich.text    import Text
from rich         import box

from crosscheck.config   import load_config, example_toml
from crosscheck.core     import MultiAgentSession
from crosscheck.models   import (
    Mode, Task, REGISTRY, Tier,
    fetch_latest, get_by_tier, validate_model_ids, OBSERVER_DEFAULT_SUPERVISORS,
)
from crosscheck.reporter import (
    LiveReporter, print_result, print_observer_result,
    to_json, to_markdown,
)

from dotenv import load_dotenv
load_dotenv(override=True)



console     = Console()
err_console = Console(stderr=True, style="red")
hdr_console = Console(stderr=True)


# ── Main group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="crosscheck-ai")
def cli():
    """crosscheck-ai — Multi-agent AI review & coding pipeline via OpenRouter."""
    pass


# ── crosscheck review ─────────────────────────────────────────────────────────

@cli.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--type", "review_type", default=None,
              type=click.Choice(["code", "plan", "text", "appstore"]),
              help="Review type. Auto-detected from extension if omitted.")
@click.option("--mode", default=None,
              type=click.Choice(["fast", "balanced", "quality"]),
              help="Model preset (default: balanced).")
@click.option("--auto-models", is_flag=True,
              help="Auto-select task-optimised models.")
@click.option("--interactive", "-i", is_flag=True,
              help="Launch wizard to choose each model tier interactively.")
@click.option("--profile", default=None,
              help="Use a named model profile from crosscheck.toml.")
@click.option("--max-rounds", default=None, type=int,
              help="Max review rounds (default: 5).")
@click.option("--supervisor", multiple=True,
              help="Supervisor model ID(s). Repeat for dual-supervisor.")
@click.option("--analyzer", multiple=True,
              help="Analyzer model IDs. Repeat 3-4 times for best results.")
@click.option("--coder", default=None,
              help="Coder model ID. Any OpenRouter model accepted.")
@click.option("--monitor/--no-monitor", default=None,
              help="Enable InsAIts anomaly monitoring.")
@click.option("--insaits-key", default=None, envvar="INSAITS_API_KEY")
@click.option("--output", default=None,
              type=click.Choice(["terminal", "json", "markdown"]),
              help="Output format (default: terminal).")
@click.option("--out-file", default=None, type=click.Path())
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--config", default=None, type=click.Path(path_type=Path))
@click.option("--stdin", "read_stdin", is_flag=True,
              help="Read content from stdin.")
def review(
    file, review_type, mode, auto_models, interactive, profile, max_rounds,
    supervisor, analyzer, coder,
    monitor, insaits_key,
    output, out_file,
    api_key, config, read_stdin,
):
    """Review a FILE (or stdin) with the multi-agent pipeline.

    \b
    Model selection — pick one approach:
      --interactive         Full wizard per tier
      --profile NAME        Named profile from crosscheck.toml
      --supervisor/--analyzer/--coder  Explicit model IDs
      --mode fast|balanced|quality     Built-in preset (default: balanced)

    \b
    Examples:
      crosscheck review mycode.py
      crosscheck review mycode.py --interactive
      crosscheck review mycode.py --profile dual-grok
      crosscheck review mycode.py \\
          --supervisor x-ai/grok-4 \\
          --supervisor anthropic/claude-opus-4.6 \\
          --analyzer deepseek/deepseek-r1 \\
          --analyzer moonshotai/kimi-k2 \\
          --analyzer qwen/qwen3-235b-a22b \\
          --coder deepseek/deepseek-chat-v3-0324
      crosscheck review plan.md --mode quality --max-rounds 5
      echo "def foo(): pass" | crosscheck review --stdin --type code
    """
    cfg = load_config(config)

    if api_key:             cfg.api_key         = api_key
    if mode:                cfg.mode            = Mode(mode)
    if max_rounds:          cfg.max_rounds      = max_rounds
    if monitor is not None: cfg.monitor         = monitor
    if insaits_key:         cfg.insaits_api_key = insaits_key
    if output:              cfg.output_format   = output
    if profile:             cfg.profile         = profile
    if supervisor:          cfg.supervisors     = list(supervisor)
    if analyzer:            cfg.analyzers       = list(analyzer)
    if coder:               cfg.coder           = coder

    # ── Resolve content ────────────────────────────────────────────────────
    if read_stdin:
        content = sys.stdin.read()
        if not content.strip():
            raise click.UsageError("stdin was empty.")
    elif file:
        content = file.read_text(encoding="utf-8", errors="replace")
    else:
        raise click.UsageError("Provide a FILE argument or use --stdin.")

    if review_type:
        cfg.review_type = Task(review_type)
    elif file:
        cfg.review_type = _detect_type(file)

    if not cfg.api_key:
        err_console.print(
            "No OpenRouter API key found.\n"
            "Set CROSSCHECK_API_KEY or pass --api-key."
        )
        sys.exit(1)

    # ── Interactive wizard ─────────────────────────────────────────────────
    if interactive:
        wiz = _run_interactive_wizard(cfg.review_type)
        if wiz is None:
            hdr_console.print("\n[yellow]Wizard cancelled.[/yellow]")
            sys.exit(0)
        chosen_sup, chosen_ana, chosen_coder = wiz
        cfg.supervisors = chosen_sup
        cfg.analyzers   = chosen_ana
        cfg.coder       = chosen_coder
        cfg.profile     = ""

    # ── Warn on unknown model IDs ──────────────────────────────────────────
    all_supplied = list(cfg.supervisors) + list(cfg.analyzers) + ([cfg.coder] if cfg.coder else [])
    if all_supplied:
        _, unknown = validate_model_ids(all_supplied)
        if unknown:
            hdr_console.print("[yellow]⚠  Model IDs not in local registry (may still work):[/yellow]")
            for uid in unknown:
                hdr_console.print(f"   [dim]{uid}[/dim]")
            hdr_console.print("[dim]   Run 'crosscheck models fetch-latest' to refresh.[/dim]\n")

    resolved_sup, resolved_ana, resolved_coder = cfg.resolve_models()

    # ── Session header ─────────────────────────────────────────────────────
    hdr_console.print()
    hdr_console.print("[bold cyan]crosscheck-ai[/bold cyan]  [dim]review[/dim]")
    hdr_console.print(f"  Type:        [cyan]{cfg.review_type.value}[/cyan]")
    hdr_console.print(
        f"  Mode:        [cyan]{cfg.mode.value}[/cyan]"
        + (f"  [dim](profile: {cfg.profile})[/dim]" if cfg.profile else "")
    )
    hdr_console.print(f"  Max rounds:  [cyan]{cfg.max_rounds}[/cyan]")
    hdr_console.print(f"  Monitoring:  [cyan]{'✓ InsAIts' if cfg.monitor else '✗ off'}[/cyan]")
    if resolved_sup:
        hdr_console.print(f"  Supervisors: [dim]{', '.join(s.split('/')[-1] for s in resolved_sup)}[/dim]")
    if resolved_ana:
        hdr_console.print(f"  Analyzers:   [dim]{', '.join(a.split('/')[-1] for a in resolved_ana)}[/dim]")
    if resolved_coder:
        hdr_console.print(f"  Coder:       [dim]{resolved_coder.split('/')[-1]}[/dim]")
    hdr_console.print()

    live = LiveReporter()
    live.start(cfg.max_rounds)

    session = MultiAgentSession(
        api_key          = cfg.api_key,
        review_type      = cfg.review_type,
        mode             = cfg.mode,
        max_rounds       = cfg.max_rounds,
        supervisors      = resolved_sup   or None,
        analyzers        = resolved_ana   or None,
        coder            = resolved_coder or None,
        auto_models      = auto_models,
        monitor          = cfg.monitor,
        insaits_api_key  = cfg.insaits_api_key,
        monitor_anchor   = cfg.monitor_anchor,
        on_round_start          = live.round_start,
        on_analyzer_complete    = lambda _: None,
        on_supervisor_verdict   = lambda s: live.synthesizing(0),
        on_coder_output         = lambda _: None,
        on_anomaly              = live.anomaly,
        on_round_complete       = live.round_complete,
    )

    try:
        result = asyncio.run(session.run_async(content))
    except KeyboardInterrupt:
        live.stop()
        hdr_console.print("\n[yellow]Session interrupted.[/yellow]")
        sys.exit(130)
    except Exception as e:
        live.stop()
        err_console.print(f"Session failed: {e}")
        raise

    live.stop()

    fmt = cfg.output_format
    if fmt == "json":
        output_text = to_json(result)
    elif fmt == "markdown":
        output_text = to_markdown(result)
    else:
        print_result(result)
        if out_file:
            Path(out_file).write_text(to_markdown(result), encoding="utf-8")
            hdr_console.print(f"[dim]Report saved to {out_file}[/dim]")
        return

    if out_file:
        Path(out_file).write_text(output_text, encoding="utf-8")
        hdr_console.print(f"[dim]Output saved to {out_file}[/dim]")
    else:
        print(output_text)


# ── crosscheck observe ────────────────────────────────────────────────────────

@cli.group()
def observe():
    """Observer mode: supervise external coding sessions in real-time.

    \b
    crosscheck acts as 2 live supervisors watching your code editor, Cursor,
    Aider, or any other tool. It flags bugs, plan drift, security issues, and
    regressions as you (or another AI) codes.

    \b
    Sub-commands:
      crosscheck observe paste   — one-shot review of pasted code
      crosscheck observe watch   — watch a folder, flag on every save
    """
    pass


@observe.command("paste")
@click.option("--code", default=None,
              help="Code snippet to review. If omitted, read from stdin.")
@click.option("--plan", default=None,
              help="Optional plan/spec text to check drift against.")
@click.option("--plan-file", default=None, type=click.Path(exists=True),
              help="Path to plan/spec file to check drift against.")
@click.option("--supervisor", "sup_models", multiple=True,
              help="Supervisor model IDs (default: claude-opus-4.6 + grok-4).")
@click.option("--analyzer", "ana_models", multiple=True,
              help="Analyzer model IDs. Repeat 3-4 times.")
@click.option("--mode", default=None,
              type=click.Choice(["fast", "balanced", "quality"]))
@click.option("--monitor/--no-monitor", default=None)
@click.option("--insaits-key", default=None, envvar="INSAITS_API_KEY")
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--config", default=None, type=click.Path(path_type=Path))
def observe_paste(code, plan, plan_file, sup_models, ana_models,
                  mode, monitor, insaits_key, api_key, config):
    """Review a pasted code snippet instantly and flag any issues.

    \b
    Examples:
      crosscheck observe paste --code "def foo(): return 1/0"
      cat myfile.py | crosscheck observe paste
      crosscheck observe paste --plan-file PLAN.md < changed_code.py
    """
    from crosscheck.observer import ObserverSession

    cfg = load_config(config)
    if api_key:  cfg.api_key = api_key
    if mode:     cfg.mode    = Mode(mode)
    if monitor is not None: cfg.monitor = monitor
    if insaits_key: cfg.insaits_api_key = insaits_key

    if not cfg.api_key:
        err_console.print("No OpenRouter API key. Set CROSSCHECK_API_KEY.")
        sys.exit(1)

    # Resolve code
    if code:
        snippet = code
    else:
        snippet = sys.stdin.read()
    if not snippet.strip():
        raise click.UsageError("No code provided. Use --code or pipe to stdin.")

    # Resolve plan
    plan_text = plan or ""
    if plan_file:
        plan_text = Path(plan_file).read_text(encoding="utf-8")
    if not plan_text and cfg.observer_plan:
        p = Path(cfg.observer_plan)
        plan_text = p.read_text(encoding="utf-8") if p.exists() else cfg.observer_plan

    obs_sup, obs_ana = cfg.resolve_observer_models()
    supervisors = list(sup_models) or obs_sup
    analyzers   = list(ana_models) or obs_ana or None

    hdr_console.print()
    hdr_console.print("[bold cyan]crosscheck-ai[/bold cyan]  [dim]observe paste[/dim]")
    hdr_console.print(f"  Supervisors: [dim]{', '.join(s.split('/')[-1] for s in supervisors)}[/dim]")
    if plan_text:
        hdr_console.print(f"  Plan drift:  [cyan]✓ checking[/cyan]")
    hdr_console.print()

    session = ObserverSession(
        api_key     = cfg.api_key,
        supervisors = supervisors,
        analyzers   = analyzers,
        mode        = cfg.mode,
        plan        = plan_text,
        monitor     = cfg.monitor,
        insaits_api_key = cfg.insaits_api_key,
    )

    try:
        result = asyncio.run(session.check(snippet))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        err_console.print(f"Observer failed: {e}")
        raise

    print_observer_result(result, show_pass=True)


@observe.command("watch")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--plan", default=None, help="Plan/spec text for drift detection.")
@click.option("--plan-file", default=None, type=click.Path(exists=True))
@click.option("--ext", "extensions", multiple=True,
              help="File extensions to watch (e.g. --ext .py --ext .ts). "
                   "Default: .py .js .ts .tsx .jsx .kt .go .rs .java")
@click.option("--supervisor", "sup_models", multiple=True,
              help="Supervisor model IDs.")
@click.option("--analyzer", "ana_models", multiple=True,
              help="Analyzer model IDs.")
@click.option("--mode", default=None,
              type=click.Choice(["fast", "balanced", "quality"]))
@click.option("--quiet", is_flag=True,
              help="Only print when issues are found.")
@click.option("--monitor/--no-monitor", default=None)
@click.option("--insaits-key", default=None, envvar="INSAITS_API_KEY")
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--config", default=None, type=click.Path(path_type=Path))
def observe_watch(
    path, plan, plan_file, extensions, sup_models, ana_models,
    mode, quiet, monitor, insaits_key, api_key, config,
):
    """Watch a folder and flag issues on every file save.

    \b
    crosscheck-ai acts as 2 AI supervisors watching your coding session.
    Every time you save a watched file, it runs the full analyzer pipeline
    and prints any flags instantly to your terminal.

    \b
    Examples:
      crosscheck observe watch .
      crosscheck observe watch ./src --ext .py --ext .ts --quiet
      crosscheck observe watch . --plan-file PLAN.md --supervisor x-ai/grok-4
      crosscheck observe watch . --supervisor x-ai/grok-4 \\
          --supervisor anthropic/claude-opus-4.6 --quiet
    """
    from crosscheck.observer import ObserverSession, FolderWatcher

    cfg = load_config(config)
    if api_key:  cfg.api_key = api_key
    if mode:     cfg.mode    = Mode(mode)
    if monitor is not None: cfg.monitor = monitor
    if insaits_key: cfg.insaits_api_key = insaits_key

    if not cfg.api_key:
        err_console.print("No OpenRouter API key. Set CROSSCHECK_API_KEY.")
        sys.exit(1)

    # Resolve plan
    plan_text = plan or ""
    if plan_file:
        plan_text = Path(plan_file).read_text(encoding="utf-8")
    if not plan_text and cfg.observer_plan:
        p = Path(cfg.observer_plan)
        plan_text = p.read_text(encoding="utf-8") if p.exists() else cfg.observer_plan

    # Resolve models
    obs_sup, obs_ana = cfg.resolve_observer_models()
    supervisors = list(sup_models) or obs_sup
    analyzers   = list(ana_models) or obs_ana or None

    # Resolve extensions
    ext_set = (
        set(extensions)
        if extensions
        else (set(cfg.observer_extensions) if cfg.observer_extensions else None)
    )

    watch_path = Path(path).resolve()
    hdr_console.print()
    hdr_console.print(Panel(
        Text("crosscheck-ai  Observer  [watch mode]", justify="center", style="bold cyan"),
        border_style = "cyan",
    ))
    hdr_console.print(f"  Watching:    [cyan]{watch_path}[/cyan]")
    hdr_console.print(f"  Supervisors: [dim]{', '.join(s.split('/')[-1] for s in supervisors)}[/dim]")
    hdr_console.print(f"  Quiet:       [dim]{'yes (flags only)' if quiet else 'no (all results)'}[/dim]")
    if plan_text:
        hdr_console.print(f"  Plan drift:  [cyan]✓ active[/cyan]")
    hdr_console.print(f"\n  [dim]Ctrl+C to stop[/dim]\n")

    session = ObserverSession(
        api_key     = cfg.api_key,
        supervisors = supervisors,
        analyzers   = analyzers,
        mode        = cfg.mode,
        plan        = plan_text,
        monitor     = cfg.monitor,
        insaits_api_key = cfg.insaits_api_key,
    )

    def on_result(result):
        print_observer_result(result, show_pass=not quiet)

    watcher = FolderWatcher(
        session    = session,
        path       = str(watch_path),
        extensions = ext_set,
        on_result  = on_result,
        debounce_s = cfg.observer_debounce,
    )

    try:
        watcher.start()
    except KeyboardInterrupt:
        hdr_console.print("\n[dim]Observer stopped.[/dim]")
    except ImportError as e:
        err_console.print(f"{e}\nInstall: pip install watchdog")
        sys.exit(1)


# ── Interactive wizard ─────────────────────────────────────────────────────────

def _run_interactive_wizard(
    review_type: Task,
) -> Optional[tuple[list[str], list[str], str]]:
    """
    Full interactive model-selection wizard using questionary.
    Returns (supervisors, analyzers, coder) or None if aborted/TTY unavailable.
    """
    if not sys.stdin.isatty():
        err_console.print(
            "[yellow]⚠  --interactive requires a TTY. "
            "Use --supervisor/--analyzer/--coder or --profile instead.[/yellow]"
        )
        return None

    try:
        import questionary
        from questionary import Style as QStyle
    except ImportError:
        err_console.print(
            "[red]questionary not installed.[/red] Run: pip install questionary"
        )
        return None

    _style = QStyle([
        ("qmark",       "fg:#00d7ff bold"),
        ("question",    "bold"),
        ("answer",      "fg:#00d7ff bold"),
        ("pointer",     "fg:#00d7ff bold"),
        ("highlighted", "fg:#00d7ff bold"),
        ("selected",    "fg:#00ff87"),
        ("separator",   "fg:#6c6c6c"),
        ("instruction", "fg:#6c6c6c"),
    ])

    from crosscheck.models import get_by_id, TASK_BEST

    def _label(m) -> str:
        ctx = m.context_k
        ctx_s = f"{ctx}K" if ctx < 1000 else f"{ctx // 1000}M"
        flag  = "🇨🇳" if m.origin == "chinese" else "🇺🇸"
        return f"{flag}  {m.display_name:<28} [{m.provider:<12}] {ctx_s:>6}  {m.notes[:40]}"

    console.print()
    console.print(Panel(
        Text("crosscheck-ai  Model Selection Wizard", justify="center", style="bold cyan"),
        border_style = "cyan",
    ))
    console.print(f"  Review type: [cyan]{review_type.value}[/cyan]\n")
    console.print("  [dim]Any model can serve any role — choose freely.[/dim]\n")

    # ── Step 1: Supervisors ────────────────────────────────────────────────
    sup_models = get_by_tier(Tier.SUPERVISOR)
    sup_choices = [
        questionary.Choice(title=_label(m), value=m.model_id)
        for m in sup_models
    ]
    console.print("[bold]Step 1/3:[/bold] Choose SUPERVISOR model(s)  [dim](1-2 recommended)[/dim]")
    console.print("[dim]  Tip: 2 supervisors = dual voting — more reliable verdicts[/dim]")

    while True:
        chosen_sup = questionary.checkbox(
            "Supervisor(s):", choices=sup_choices, style=_style
        ).ask()
        if chosen_sup is None:
            return None
        if 1 <= len(chosen_sup) <= 2:
            break
        console.print("[yellow]  Select 1 or 2 supervisor models.[/yellow]")

    # ── Step 2: Analyzers ──────────────────────────────────────────────────
    ana_models    = get_by_tier(Tier.ANALYZER)
    task_defaults = set(TASK_BEST[review_type]["analyzers"])

    ana_choices = [
        questionary.Choice(
            title   = _label(m) + (" ★" if m.model_id in task_defaults else ""),
            value   = m.model_id,
            checked = m.model_id in task_defaults,
        )
        for m in ana_models
    ]

    console.print()
    console.print("[bold]Step 2/3:[/bold] Choose ANALYZER models  "
                  "[dim](3-4 recommended, ★ = best for this task)[/dim]")

    while True:
        chosen_ana = questionary.checkbox(
            "Analyzers:", choices=ana_choices, style=_style
        ).ask()
        if chosen_ana is None:
            return None
        if 2 <= len(chosen_ana) <= 6:
            break
        console.print(
            "[yellow]  Please select 2-6 analyzers.[/yellow]"
        )

    # ── Step 3: Coder ──────────────────────────────────────────────────────
    coder_models = get_by_tier(Tier.CODER)
    coder_choices = [
        questionary.Choice(title=_label(m), value=m.model_id)
        for m in coder_models
    ]

    console.print()
    console.print("[bold]Step 3/3:[/bold] Choose CODER model  "
                  "[dim](any model — Anthropic recommended by default)[/dim]")

    chosen_coder = questionary.select(
        "Coder:", choices=coder_choices, style=_style,
        default="anthropic/claude-opus-4.6",
    ).ask()
    if chosen_coder is None:
        return None

    # ── Confirm ────────────────────────────────────────────────────────────
    console.print()
    console.print("[bold]Your selection:[/bold]")
    console.print(f"  Supervisors: [cyan]{', '.join(s.split('/')[-1] for s in chosen_sup)}[/cyan]")
    console.print(f"  Analyzers:   [cyan]{', '.join(a.split('/')[-1] for a in chosen_ana)}[/cyan]")
    console.print(f"  Coder:       [cyan]{chosen_coder.split('/')[-1]}[/cyan]")
    console.print()

    confirmed = questionary.confirm("Proceed?", default=True, style=_style).ask()
    if not confirmed:
        return None

    return chosen_sup, chosen_ana, chosen_coder


# ── crosscheck models ─────────────────────────────────────────────────────────

@cli.group()
def models():
    """Manage and list available models."""
    pass


@models.command("list")
@click.option("--tier", default=None,
              type=click.Choice(["supervisor", "analyzer", "coder"]))
@click.option("--origin", default=None,
              type=click.Choice(["western", "chinese"]))
def models_list(tier, origin):
    """List all registered models."""
    items = list(REGISTRY)
    if tier:
        t     = Tier(tier)
        items = [m for m in items if t in m.tiers]
    if origin:
        items = [m for m in items if m.origin == origin]

    tbl = Table(title="Available Models", box=box.ROUNDED, border_style="cyan")
    tbl.add_column("Origin",   width=3)
    tbl.add_column("Model ID", style="cyan", max_width=42)
    tbl.add_column("Name",     max_width=24)
    tbl.add_column("Tiers",    max_width=30)
    tbl.add_column("Ctx",      width=7)
    tbl.add_column("Provider", width=12)
    tbl.add_column("Notes",    max_width=40)

    for m in items:
        tiers_str  = ", ".join(x.value for x in m.tiers)
        origin_ico = "🇨🇳" if m.origin == "chinese" else "🇺🇸"
        ctx_str    = f"{m.context_k}K" if m.context_k < 1000 else f"{m.context_k // 1000}M"
        tbl.add_row(origin_ico, m.model_id, m.display_name,
                    tiers_str, ctx_str, m.provider, m.notes)

    console.print(tbl)
    console.print(f"  [dim]{len(items)} model(s)[/dim]")


@models.command("fetch-latest")
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--filter", "name_filter", default=None,
              help="Filter by substring in model ID or name.")
def models_fetch_latest(api_key, name_filter):
    """Fetch the live model list from OpenRouter API."""
    if not api_key:
        err_console.print("Provide --api-key or set CROSSCHECK_API_KEY.")
        sys.exit(1)

    console.print("[dim]Fetching from https://openrouter.ai/api/v1/models ...[/dim]")
    data = asyncio.run(fetch_latest(api_key))

    if not data:
        console.print("[yellow]No data returned. Check your API key.[/yellow]")
        return

    if name_filter:
        f    = name_filter.lower()
        data = [m for m in data
                if f in m.get("id", "").lower() or f in m.get("name", "").lower()]

    from crosscheck.models import get_by_id
    tbl = Table(
        title        = f"OpenRouter Live Models  ({len(data)} shown)",
        box          = box.ROUNDED,
        border_style = "cyan",
    )
    tbl.add_column("Model ID",     style="cyan", max_width=50)
    tbl.add_column("Name",         max_width=32)
    tbl.add_column("Context",      width=10)
    tbl.add_column("In registry?", width=12)

    for m in data[:60]:
        mid   = m.get("id", "")
        ctx   = m.get("context_length", "?")
        known = "✓" if get_by_id(mid) else ""
        tbl.add_row(mid, m.get("name", ""), str(ctx), known)

    console.print(tbl)
    if len(data) > 60:
        console.print(f"[dim]  ... and {len(data) - 60} more. Use --filter to narrow.[/dim]")


# ── crosscheck profiles ───────────────────────────────────────────────────────

@cli.command()
@click.option("--config", default=None, type=click.Path(path_type=Path))
def profiles(config):
    """List named model profiles defined in crosscheck.toml."""
    cfg = load_config(config)

    if not cfg.profiles:
        console.print(
            "[yellow]No profiles found in crosscheck.toml.[/yellow]\n"
            "Run [cyan]crosscheck init[/cyan] to create a config with example profiles."
        )
        return

    for name, p in cfg.profiles.items():
        console.print(Panel(
            f"[bold]{name}[/bold]"
            + (f"  [dim]{p.notes}[/dim]" if p.notes else ""),
            border_style = "cyan",
        ))
        console.print(f"  Supervisors: {', '.join(p.supervisors) or '[dim]<default>[/dim]'}")
        console.print(f"  Analyzers:   {', '.join(p.analyzers)   or '[dim]<default>[/dim]'}")
        console.print(f"  Coder:       {p.coder                  or '[dim]<default>[/dim]'}")
        console.print()


# ── crosscheck types ──────────────────────────────────────────────────────────

@cli.command()
def types():
    """List available review types and their analyzer angles."""
    from crosscheck.prompts import TASK_ANGLES, TASK_LABELS

    tbl = Table(title="Review Types", box=box.ROUNDED, border_style="cyan")
    tbl.add_column("Type",  style="cyan", width=10)
    tbl.add_column("Label", width=22)
    tbl.add_column("Analyzer Angles")

    for task in Task:
        angles = " | ".join(TASK_ANGLES[task])
        tbl.add_row(task.value, TASK_LABELS[task], angles)
    console.print(tbl)


# ── crosscheck init ───────────────────────────────────────────────────────────

@cli.command()
def init():
    """Create a crosscheck.toml config file in the current directory."""
    target = Path("crosscheck.toml")
    if target.exists():
        click.confirm("crosscheck.toml already exists. Overwrite?", abort=True)
    target.write_text(example_toml(), encoding="utf-8")
    console.print(f"[green]✓[/green] Created {target}")
    console.print(
        "  Set your API key, then try:\n"
        "  [cyan]crosscheck review mycode.py[/cyan]\n"
        "  [cyan]crosscheck observe watch .[/cyan]"
    )


# ── Helper ────────────────────────────────────────────────────────────────────

def _detect_type(path: Path) -> Task:
    ext  = path.suffix.lower()
    name = path.name.lower()
    if ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".kt", ".swift",
               ".go", ".rs", ".java", ".cpp", ".c", ".cs", ".rb", ".php"):
        return Task.CODE
    if "appstore" in name or "submission" in name or "metadata" in name:
        return Task.APPSTORE
    if ext in (".md", ".rst", ".txt", ".html", ".yaml", ".yml", ".json"):
        return Task.PLAN
    return Task.TEXT


# ── Register Phase 1/2 extensions ────────────────────────────────────────────
from crosscheck.cli_extensions import register_extensions
register_extensions(cli)


if __name__ == "__main__":
    cli()
