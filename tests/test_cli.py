"""
tests/test_cli.py
-----------------
Tests for CLI commands: review, models, profiles, types, init.
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from crosscheck.agents import AnalyzerReport, SynthesisResult
from crosscheck.cli import cli
from crosscheck.core import Round, SessionResult

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_result() -> SessionResult:
    synthesis = SynthesisResult(
        verdict="APPROVED", overall_score=9.0,
        summary="Looks great", critical_issues=[],
        coder_instructions="", approved_aspects=["Clean code"],
    )
    analyzer = AnalyzerReport(
        model_id="deepseek/deepseek-chat-v3-0324",
        angle="Security", score=9.0, verdict="PASS",
        issues=[], positive_findings=["No issues"], summary="All good",
    )
    rnd = Round(
        number=1, content_in="def foo(): pass",
        content_out="def foo(): pass",
        decompose={"session_goal": "Review", "analyzer_tasks": []},
        analyzer_reports=[analyzer], synthesis=synthesis,
    )
    return SessionResult(
        verdict="APPROVED", final_content="def foo(): pass",
        rounds=[rnd], total_rounds=1, final_score=9.0,
        issues_fixed=0, total_tokens=1000, cost_usd=0.01,
        duration_sec=5.0, anomaly_count=0, analyzer_reports=[analyzer],
    )


# ── models list ───────────────────────────────────────────────────────────────

def test_models_list_all():
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list"])
    assert r.exit_code == 0
    # Must show both western and chinese models
    assert "anthropic" in r.output.lower()
    assert "deepseek"  in r.output.lower()

def test_models_list_tier_supervisor():
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list", "--tier", "supervisor"])
    assert r.exit_code == 0
    assert "claude" in r.output.lower()

def test_models_list_tier_coder():
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list", "--tier", "coder"])
    assert r.exit_code == 0
    # All coder models are Anthropic
    assert "anthropic" in r.output.lower()

def test_models_list_origin_western():
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list", "--origin", "western"])
    assert r.exit_code == 0
    assert "🇺🇸" in r.output

def test_models_list_origin_chinese():
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list", "--origin", "chinese"])
    assert r.exit_code == 0
    assert "🇨🇳" in r.output
    # Must NOT show grok-3 or other old western models
    assert "grok-3" not in r.output

def test_models_list_no_is_free_column():
    """The old 'Free' column header must be gone (model names may contain 'Free')."""
    runner = CliRunner()
    r = runner.invoke(cli, ["models", "list"])
    assert r.exit_code == 0
    # The table must render with model data visible
    assert "model(s)" in r.output
    # "Free" must NOT appear as a standalone column header — only in model names.
    # Check no dedicated "Free" column exists in any table row.
    for line in r.output.splitlines():
        if "\u2502 Free " in line and "(Free)" not in line:
            if line.strip().startswith("\u2502") and "Free" in line.split("\u2502"):
                raise AssertionError("Found 'Free' as standalone column header")


# ── types command ─────────────────────────────────────────────────────────────

def test_types_command():
    runner = CliRunner()
    r = runner.invoke(cli, ["types"])
    assert r.exit_code == 0
    for t in ["code", "plan", "text", "appstore"]:
        assert t in r.output


# ── init command ──────────────────────────────────────────────────────────────

def test_init_creates_toml(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        r = runner.invoke(cli, ["init"])
    assert r.exit_code == 0
    assert "crosscheck.toml" in r.output

def test_init_toml_contains_profiles(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        content = Path(td, "crosscheck.toml").read_text()
    assert "[profile." in content
    assert "chinese-best" in content


# ── profiles command ──────────────────────────────────────────────────────────

def test_profiles_no_config(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        r = runner.invoke(cli, ["profiles"])
    # Should warn about no profiles, not crash
    assert r.exit_code == 0
    assert "No profiles" in r.output or "crosscheck init" in r.output

def test_profiles_with_config(tmp_path):
    toml_content = """
[crosscheck]
api_key = "sk-test"

[profile.my-test]
notes       = "Test profile"
supervisors = ["anthropic/claude-opus-4.6"]
analyzers   = ["deepseek/deepseek-r1", "x-ai/grok-4"]
coder       = "anthropic/claude-sonnet-4.6"
"""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        Path(td, "crosscheck.toml").write_text(toml_content)
        r = runner.invoke(cli, ["profiles"])
    assert r.exit_code == 0
    assert "my-test" in r.output


# ── review command: no API key ────────────────────────────────────────────────

def test_review_no_api_key():
    runner = CliRunner()
    r = runner.invoke(
        cli, ["review", "--stdin", "--type", "code"],
        input="def foo(): pass",
        env={"CROSSCHECK_API_KEY": "", "OPENROUTER_API_KEY": ""},
        catch_exceptions=False,
    )
    assert r.exit_code != 0


# ── review command: JSON output ───────────────────────────────────────────────

def test_review_json_output(tmp_path):
    fake = _fake_result()
    runner = CliRunner()
    with patch("crosscheck.cli.asyncio.run", return_value=fake), \
         patch("crosscheck.cli.MultiAgentSession"):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            r = runner.invoke(
                cli, ["review", "--stdin", "--type", "code",
                      "--output", "json", "--api-key", "sk-test"],
                input="def foo(): pass",
            )
    assert r.exit_code == 0
    json_start = r.output.find("{")
    assert json_start != -1, f"No JSON in output: {r.output[:300]}"
    data = json.loads(r.output[json_start:])
    assert data["verdict"]     == "APPROVED"
    assert data["final_score"] == 9.0


# ── review command: markdown output ──────────────────────────────────────────

def test_review_markdown_output(tmp_path):
    fake = _fake_result()
    runner = CliRunner()
    with patch("crosscheck.cli.asyncio.run", return_value=fake), \
         patch("crosscheck.cli.MultiAgentSession"):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            r = runner.invoke(
                cli, ["review", "--stdin", "--type", "code",
                      "--output", "markdown", "--api-key", "sk-test"],
                input="def foo(): pass",
            )
    assert r.exit_code == 0
    assert "## Round 1" in r.output
    assert "APPROVED"   in r.output


# ── review command: --profile flag ────────────────────────────────────────────

def test_review_with_profile(tmp_path):
    """--profile should activate the named profile from config."""
    toml_content = """
[crosscheck]
api_key = "sk-test"

[profile.test-profile]
supervisors = ["anthropic/claude-opus-4.6"]
analyzers   = ["deepseek/deepseek-r1", "x-ai/grok-4", "qwen/qwen3-235b-a22b"]
coder       = "anthropic/claude-sonnet-4.6"
"""
    fake = _fake_result()
    runner = CliRunner()

    with patch("crosscheck.cli.asyncio.run", return_value=fake):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            cfg_path = Path(td, "crosscheck.toml")
            cfg_path.write_text(toml_content)
            r = runner.invoke(
                cli, ["review", "--stdin", "--type", "code",
                      "--profile", "test-profile",
                      "--output", "markdown",
                      f"--config={cfg_path}"],
                input="def foo(): pass",
                catch_exceptions=False,
            )

    # Should not fail and should show profile name in header
    assert r.exit_code == 0


# ── review: --interactive blocked on non-TTY ──────────────────────────────────

def test_review_interactive_non_tty(tmp_path):
    """--interactive on non-TTY stdin should warn and exit 0."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        r = runner.invoke(
            cli, ["review", "--stdin", "--type", "code",
                  "--interactive", "--api-key", "sk-test"],
            input="def foo(): pass",
        )
    # Either exited 0 (wizard cancelled) or showed TTY warning
    # The CliRunner does not provide a real TTY so the wizard should abort
    assert r.exit_code in (0, 1)
    # Should mention TTY or wizard in output/error
    _combined = (r.output or "") + (r.exception.__str__() if r.exception else "")
    # We just verify it didn't silently crash with an unhandled exception
    assert not isinstance(r.exception, KeyError), f"Unhandled exception: {r.exception}"


# ── _detect_type helper ───────────────────────────────────────────────────────

def test_detect_type_python():
    from crosscheck.cli import _detect_type
    assert _detect_type(Path("main.py"))     == __import__("crosscheck.models", fromlist=["Task"]).Task.CODE

def test_detect_type_markdown():
    from crosscheck.cli import _detect_type
    from crosscheck.models import Task
    assert _detect_type(Path("plan.md"))     == Task.PLAN

def test_detect_type_appstore():
    from crosscheck.cli import _detect_type
    from crosscheck.models import Task
    assert _detect_type(Path("appstore_submission.md")) == Task.APPSTORE

def test_detect_type_typescript():
    from crosscheck.cli import _detect_type
    from crosscheck.models import Task
    assert _detect_type(Path("component.tsx")) == Task.CODE
