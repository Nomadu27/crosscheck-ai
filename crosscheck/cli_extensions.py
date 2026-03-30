"""
crosscheck.cli_extensions
--------------------------
Phase 1 & 2 CLI command extensions.

New commands added to the crosscheck CLI:

Phase 1:
  crosscheck review FILE --show-diff           show unified/side-by-side diff
  crosscheck review FILE --show-diff-mode side-by-side
  crosscheck review FILE --repo-context        inject multi-file repo context
  crosscheck pr review --repo owner/repo --pr 42
  crosscheck pr post   --repo owner/repo --pr 42 --result result.json
  crosscheck sandbox   --tools ruff,pytest     run linters and feed results in
  crosscheck review FILE --run-tools ruff pytest mypy

Phase 2:
  crosscheck dashboard  --port 8080            launch team dashboard
  crosscheck cache stats / clear               manage result cache
  crosscheck policy list / validate PATH       manage company policies
  crosscheck local list-models                 list Ollama models
  crosscheck local review FILE                 run on local models

These are registered as additional Click commands/groups and
should be added to the main cli.py cli group.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

console     = Console()
err_console = Console(stderr=True, style="red")


# ---------------------------------------------------------------------------
# --show-diff flag integration (adds to existing `review` command)
# ---------------------------------------------------------------------------

def maybe_print_diff(result, mode: str = "unified") -> None:
    """Call from review command after session completes."""
    from crosscheck.diff import print_session_diffs
    print_session_diffs(result.rounds, mode=mode)


# ---------------------------------------------------------------------------
# PR Bot commands
# ---------------------------------------------------------------------------

@click.group("pr")
def pr_group():
    """GitHub / GitLab PR bot integration."""
    pass


@pr_group.command("review")
@click.option("--repo",     required=True,  help="owner/repo or group/project")
@click.option("--pr",       required=True,  type=int, help="PR / MR number")
@click.option("--provider", default="github", type=click.Choice(["github", "gitlab"]))
@click.option("--mode",     default="balanced", type=click.Choice(["fast", "balanced", "quality"]))
@click.option("--max-rounds", default=3, type=int)
@click.option("--no-inline",  is_flag=True, help="Skip inline comments")
@click.option("--no-status",  is_flag=True, help="Skip commit status check")
@click.option("--api-key",    default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--github-token", default=None, envvar="GITHUB_TOKEN")
@click.option("--gitlab-token", default=None, envvar="GITLAB_TOKEN")
def pr_review(repo, pr, provider, mode, max_rounds, no_inline, no_status,
              api_key, github_token, gitlab_token):
    """Review a GitHub / GitLab PR with crosscheck."""
    from crosscheck.pr_bot import PRReviewBot, PRReviewConfig

    if not api_key:
        err_console.print("No CROSSCHECK_API_KEY found.")
        sys.exit(1)

    console.print(f"[cyan]crosscheck PR review[/cyan]  {repo} #{pr}")

    config = PRReviewConfig(
        repo         = repo,
        pr_number    = pr,
        provider     = provider,
        mode         = mode,
        max_rounds   = max_rounds,
        post_inline  = not no_inline,
        set_status   = not no_status,
    )

    bot = PRReviewBot(
        crosscheck_api_key = api_key,
        github_token       = github_token or "",
        gitlab_token       = gitlab_token or "",
    )

    result = asyncio.run(bot.review_pr(config))
    if result:
        verdict_color = "green" if result.verdict == "APPROVED" else "red"
        console.print(
            f"[{verdict_color}]{result.verdict}[/{verdict_color}]  "
            f"Score: {result.final_score:.1f}/10  "
            f"Cost: ${result.cost_usd:.4f}"
        )
    else:
        console.print("[yellow]No diff found — PR may be empty.[/yellow]")


@pr_group.command("webhook")
@click.option("--port",           default=8080, type=int)
@click.option("--host",           default="0.0.0.0")
@click.option("--mode",           default="balanced")
@click.option("--api-key",        default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--github-token",   default=None, envvar="GITHUB_TOKEN")
@click.option("--webhook-secret", default=None, envvar="GITHUB_WEBHOOK_SECRET")
def pr_webhook(port, host, mode, api_key, github_token, webhook_secret):
    """Start the GitHub PR webhook server."""
    from crosscheck.pr_bot import create_webhook_app
    try:
        import uvicorn
    except ImportError:
        err_console.print("uvicorn required: pip install uvicorn")
        sys.exit(1)

    app = create_webhook_app(
        crosscheck_api_key = api_key or "",
        github_token       = github_token or "",
        webhook_secret     = webhook_secret or "",
        mode               = mode,
    )
    console.print(f"[cyan]crosscheck webhook server[/cyan]  http://{host}:{port}/webhook/github")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Sandbox / tool runner commands
# ---------------------------------------------------------------------------

@click.command("sandbox")
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--tools",    default=None, help="Comma-separated tool list")
@click.option("--parallel", is_flag=True, default=True)
@click.option("--list",     "list_only", is_flag=True, help="List available tools and exit")
def sandbox_cmd(path, tools, parallel, list_only):
    """Discover and run linters/tests. Feed results to crosscheck."""
    from crosscheck.sandbox import ExecutionSandbox

    sb = ExecutionSandbox(project_root=path)

    if list_only:
        available = sb.available_tools()
        if not available:
            console.print("[yellow]No tools detected for this project.[/yellow]")
        else:
            console.print("[bold]Available tools:[/bold]")
            for t in available:
                console.print(f"  [cyan]•[/cyan] {t}")
        return

    tool_list = [t.strip() for t in tools.split(",")] if tools else None
    results   = asyncio.run(sb.run_all(tool_list, parallel=parallel))

    t = Table(title="Tool Results", box=box.ROUNDED, border_style="dim")
    t.add_column("Tool",     style="cyan")
    t.add_column("Status",   width=10)
    t.add_column("Exit",     width=6)
    t.add_column("Time",     width=7)
    t.add_column("Output preview", max_width=60, overflow="fold")

    for r in results:
        status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        preview = (r.output[:80] + "…") if len(r.output) > 80 else r.output
        t.add_row(r.tool, status, str(r.returncode), f"{r.duration_sec:.1f}s", preview)

    console.print(t)


# ---------------------------------------------------------------------------
# Dashboard command
# ---------------------------------------------------------------------------

@click.command("dashboard")
@click.option("--port", default=8080, type=int)
@click.option("--host", default="127.0.0.1")
@click.option("--db",   default=None, type=click.Path())
def dashboard_cmd(port, host, db):
    """Launch the team history/cost dashboard."""
    from crosscheck.dashboard import DEFAULT_DB_PATH, create_dashboard_app
    try:
        import uvicorn
    except ImportError:
        err_console.print("uvicorn required: pip install uvicorn")
        sys.exit(1)

    db_path = Path(db) if db else DEFAULT_DB_PATH
    app     = create_dashboard_app(db_path=db_path)
    console.print(f"[cyan]crosscheck dashboard[/cyan]  http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Cache management commands
# ---------------------------------------------------------------------------

@click.group("cache")
def cache_group():
    """Manage the result cache."""
    pass


@cache_group.command("stats")
def cache_stats():
    from crosscheck.cache import ResultCache
    c    = ResultCache()
    info = c.stats()
    for k, v in info.items():
        console.print(f"  [dim]{k}[/dim]: {v}")


@cache_group.command("clear")
@click.option("--expired-only", is_flag=True)
def cache_clear(expired_only):
    from crosscheck.cache import ResultCache
    c = ResultCache()
    n = c.clear_expired() if expired_only else c.clear()
    console.print(f"[green]Cleared {n} cache entries.[/green]")


# ---------------------------------------------------------------------------
# Policy commands
# ---------------------------------------------------------------------------

@click.group("policy")
def policy_group():
    """Manage company policy files."""
    pass


@policy_group.command("list")
@click.option("--dir", "policy_dir", default="policies", type=click.Path())
def policy_list(policy_dir):
    from crosscheck.policy import BUILTIN_POLICIES, PolicyLoader

    console.print("[bold]Built-in policies:[/bold]")
    for name in BUILTIN_POLICIES:
        console.print(f"  [cyan]•[/cyan] {name}")

    p = Path(policy_dir)
    if p.exists():
        console.print(f"\n[bold]Local policies ({policy_dir}):[/bold]")
        policies = PolicyLoader.from_directory(p)
        for pol in policies:
            console.print(f"  [cyan]•[/cyan] {pol.name}  ({len(pol.rules)} rules)  [{pol.source_file}]")
    else:
        console.print(f"\n[dim]No local policy directory found at '{policy_dir}'.[/dim]")


@policy_group.command("validate")
@click.argument("path", type=click.Path(exists=True))
def policy_validate(path):
    from crosscheck.policy import PolicyLoader
    try:
        pol = PolicyLoader.from_file(path)
        console.print(f"[green]✓[/green]  {pol.name}  —  {len(pol.rules)} rules loaded")
        for r in pol.rules[:5]:
            console.print(f"  [{r.severity}] {r.id}: {r.description[:80]}")
        if len(pol.rules) > 5:
            console.print(f"  … and {len(pol.rules) - 5} more")
    except Exception as e:
        err_console.print(f"Policy parse error: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Local model commands (Ollama)
# ---------------------------------------------------------------------------

@click.group("local")
def local_group():
    """Run crosscheck on local Ollama models."""
    pass


@local_group.command("list-models")
@click.option("--url", default="http://localhost:11434", help="Ollama base URL")
def local_list_models(url):
    from crosscheck.local import OllamaModelRegistry

    models = asyncio.run(OllamaModelRegistry.list_models(base_url=url))
    if not models:
        console.print("[yellow]No Ollama models found (is `ollama serve` running?)[/yellow]")
        return

    t = Table(title="Installed Ollama Models", box=box.ROUNDED, border_style="dim")
    t.add_column("Name",    style="cyan")
    t.add_column("Size",    width=10)
    t.add_column("Modified", width=15)
    for m in models:
        name  = m.get("name", "?")
        size  = f"{m.get('size', 0) // 1_073_741_824:.1f} GB"
        mod   = m.get("modified_at", "")[:10]
        t.add_row(name, size, mod)
    console.print(t)


@local_group.command("review")
@click.argument("file", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--stdin",        "read_stdin", is_flag=True)
@click.option("--supervisor",   default=None, help="Ollama supervisor model")
@click.option("--analyzer",     multiple=True, help="Ollama analyzer model(s)")
@click.option("--coder",        default=None, help="Ollama coder model")
@click.option("--ollama-url",   default="http://localhost:11434/v1")
@click.option("--max-rounds",   default=3, type=int)
@click.option("--output",       default="terminal", type=click.Choice(["terminal", "json", "markdown"]))
def local_review(file, read_stdin, supervisor, analyzer, coder, ollama_url, max_rounds, output):
    """Review code using local Ollama models (no API key required)."""
    from crosscheck.local import LocalMultiAgentSession
    from crosscheck.reporter import print_result, to_json, to_markdown

    if read_stdin:
        content = sys.stdin.read()
    elif file:
        content = file.read_text(encoding="utf-8", errors="replace")
    else:
        raise click.UsageError("Provide FILE or --stdin")

    session = LocalMultiAgentSession(
        supervisors = [supervisor] if supervisor else None,
        analyzers   = list(analyzer) or None,
        coder       = coder,
        ollama_url  = ollama_url,
        max_rounds  = max_rounds,
    )

    console.print("[cyan]crosscheck local review[/cyan]  (Ollama)")
    result = asyncio.run(session.run_async(content))

    if output == "json":
        console.print(to_json(result))
    elif output == "markdown":
        console.print(to_markdown(result))
    else:
        print_result(result)


# ---------------------------------------------------------------------------
# Helper: register all extensions with the main CLI group
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AI Dev Team commands (v2.0.0)
# ---------------------------------------------------------------------------

@click.command("team")
@click.option("--task", "-t", required=True, help="Task description for the team")
@click.option("--file", "-f", "code_file", default=None, type=click.Path(exists=True),
              help="Code file to work on")
@click.option("--models", default=None,
              help="Override model assignments (role:model,...) e.g. coder:openai/gpt-5")
@click.option("--rounds", default=3, type=int, help="Max iteration rounds")
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--output", default="terminal", type=click.Choice(["terminal", "json", "transcript"]))
def team_cmd(task, code_file, models, rounds, api_key, output):
    """Launch an AI Dev Team session in the terminal.

    Example: crosscheck team -t "Add authentication" -f app.py
    """
    from crosscheck.client import OpenRouterClient
    from crosscheck.team import TeamRole, TeamSession, build_team

    if not api_key:
        err_console.print("No CROSSCHECK_API_KEY found.")
        sys.exit(1)

    code = ""
    if code_file:
        code = Path(code_file).read_text(encoding="utf-8", errors="replace")

    # Parse model overrides
    team = None
    if models:
        overrides = {}
        for pair in models.split(","):
            role_str, model_id = pair.strip().split(":", 1)
            try:
                role = TeamRole(role_str.strip().lower())
                overrides[role] = model_id.strip()
            except ValueError:
                err_console.print(f"Unknown role: {role_str}")
                sys.exit(1)
        team = build_team(overrides)

    console.print("[bold cyan]crosscheck AI Dev Team[/bold cyan]")
    console.print(f"  Task: {task}")
    if code_file:
        console.print(f"  File: {code_file}")
    console.print(f"  Rounds: {rounds}")
    console.print()

    async def run():
        client = OpenRouterClient(api_key=api_key)
        session = TeamSession(client=client, team=team, max_rounds=rounds)
        return await session.run(task=task, code=code)

    history = asyncio.run(run())

    if output == "json":
        import json as _json
        data = [m.to_dict() for m in history.messages]
        console.print(_json.dumps(data, indent=2, default=str))
    elif output == "transcript":
        console.print(history.to_transcript())
    else:
        # Terminal: Rich-formatted output
        for msg in history.messages:
            from crosscheck.team.roles import DEFAULT_TEAM
            color = "white"
            for spec in DEFAULT_TEAM:
                if spec.role.value == msg.role:
                    color = spec.color
                    break
            console.print(
                f"[bold {color}]{msg.display_name}[/bold {color}] "
                f"[dim]({msg.phase})[/dim]"
            )
            console.print(f"  {msg.content[:500]}")
            if msg.code_blocks:
                for cb in msg.code_blocks:
                    console.print(f"\n  [cyan]{cb.filename}[/cyan]")
                    console.print(f"  ```{cb.language}")
                    for line in cb.content.split("\n")[:20]:
                        console.print(f"  {line}")
                    console.print("  ```")
            console.print()

    console.print(
        f"[bold green]Done.[/bold green] "
        f"{len(history.messages)} messages, session {history.session_id}"
    )


@click.command("chat")
@click.option("--port", default=8080, type=int)
@click.option("--host", default="127.0.0.1")
@click.option("--api-key", default=None, envvar="CROSSCHECK_API_KEY")
@click.option("--no-open", is_flag=True, help="Don't open browser automatically")
def chat_cmd(port, host, api_key, no_open):
    """Launch the Group Chat web UI.

    Opens a browser with the AI Dev Team dialog panel.
    Example: crosscheck chat --port 8080
    """
    from crosscheck.chat_server import create_chat_app
    try:
        import uvicorn
    except ImportError:
        err_console.print("uvicorn required: pip install 'crosscheck-ai[dashboard]'")
        sys.exit(1)

    app = create_chat_app(api_key=api_key)
    url = f"http://{host}:{port}"
    console.print(f"[bold cyan]crosscheck AI Dev Team[/bold cyan]  {url}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    if not no_open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")


# ---------------------------------------------------------------------------
# Helper: register all extensions with the main CLI group
# ---------------------------------------------------------------------------

def register_extensions(cli_group) -> None:
    """
    Call this from cli.py to attach all Phase 1/2/v2 commands:

        from crosscheck.cli_extensions import register_extensions
        register_extensions(cli)
    """
    cli_group.add_command(pr_group,       "pr")
    cli_group.add_command(sandbox_cmd,    "sandbox")
    cli_group.add_command(dashboard_cmd,  "dashboard")
    cli_group.add_command(cache_group,    "cache")
    cli_group.add_command(policy_group,   "policy")
    cli_group.add_command(local_group,    "local")
    # v2.0.0 — AI Dev Team
    cli_group.add_command(team_cmd,       "team")
    cli_group.add_command(chat_cmd,       "chat")
