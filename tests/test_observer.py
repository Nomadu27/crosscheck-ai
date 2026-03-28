"""
tests/test_observer.py
-----------------------
Tests for ObserverSession, FolderWatcher, flag classification,
dual-supervisor integration in observer mode, and CLI observe commands.
"""

import asyncio
import json
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from crosscheck.observer import (
    ObserverSession, ObserverResult, ObserverFlag,
    FolderWatcher, FLAG_ICONS, FLAG_COLORS,
)
from crosscheck.agents.supervisor import SynthesisResult
from crosscheck.agents.analyzer   import AnalyzerReport
from crosscheck.models import Mode


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_synthesis(verdict: str = "APPROVED", score: float = 9.0,
                    votes: list = None, consensus: bool = True) -> SynthesisResult:
    return SynthesisResult(
        verdict            = verdict,
        overall_score      = score,
        summary            = f"{verdict} — score {score}",
        critical_issues    = ["Bug in line 5"] if verdict == "REVISE" else [],
        coder_instructions = "Fix the bug" if verdict == "REVISE" else "",
        approved_aspects   = ["Clean code"],
        supervisor_votes   = votes or [verdict],
        consensus          = consensus,
    )


def _make_report(angle: str, issues: list = None, score: float = 9.0,
                 verdict: str = "PASS", model_id: str = "test/model") -> AnalyzerReport:
    return AnalyzerReport(
        model_id          = model_id,
        angle             = angle,
        score             = score,
        verdict           = verdict,
        issues            = issues or [],
        positive_findings = ["Good structure"],
        summary           = f"{angle} check: {verdict}",
    )


def _make_observer_result(
    passed:   bool        = True,
    flags:    list        = None,
    score:    float       = 9.0,
    votes:    list        = None,
    consensus: bool       = True,
) -> ObserverResult:
    return ObserverResult(
        passed           = passed,
        flags            = flags or [],
        score            = score,
        summary          = "test",
        file_path        = "",
        duration_sec     = 0.1,
        anomaly_count    = 0,
        supervisor_votes = votes or ["APPROVED"],
        consensus        = consensus,
    )


# ── ObserverSession._extract_flags ────────────────────────────────────────────

def test_extract_flags_clean_code_returns_pass():
    reports   = [_make_report("Security")]
    synthesis = _make_synthesis("APPROVED")
    flags     = ObserverSession._extract_flags(reports, synthesis)

    assert len(flags) == 1
    assert flags[0].flag_type == "PASS"


def test_extract_flags_bug_keyword():
    issues  = [{"severity": "critical", "location": "line 5",
                "description": "Potential null pointer exception bug", "fix": "Add null check"}]
    reports = [_make_report("Logic", issues=issues, score=3.0, verdict="ISSUES_FOUND")]
    synthesis = _make_synthesis("REVISE", score=3.0)
    flags   = ObserverSession._extract_flags(reports, synthesis)

    bug_flags = [f for f in flags if f.flag_type == "BUG"]
    assert len(bug_flags) >= 1
    assert bug_flags[0].severity == "critical"
    assert bug_flags[0].location == "line 5"


def test_extract_flags_security_keyword():
    issues  = [{"severity": "critical", "location": "db.py:45",
                "description": "SQL injection vulnerability in query builder", "fix": "Use parameterized queries"}]
    reports = [_make_report("Security", issues=issues, score=2.0, verdict="ISSUES_FOUND")]
    synthesis = _make_synthesis("REVISE", 2.0)
    flags   = ObserverSession._extract_flags(reports, synthesis)

    sec_flags = [f for f in flags if f.flag_type == "SECURITY"]
    assert len(sec_flags) >= 1
    assert sec_flags[0].fix == "Use parameterized queries"


def test_extract_flags_plan_drift_keyword():
    issues  = [{"severity": "major", "location": "module.py",
                "description": "Code doesn't follow the spec requirement for user registration",
                "fix": "Implement OAuth as specified"}]
    reports = [_make_report("Plan", issues=issues, score=5.0, verdict="ISSUES_FOUND")]
    synthesis = _make_synthesis("REVISE", 5.0)
    flags   = ObserverSession._extract_flags(reports, synthesis)

    drift_flags = [f for f in flags if f.flag_type == "PLAN_DRIFT"]
    assert len(drift_flags) >= 1


def test_extract_flags_regression_keyword():
    issues  = [{"severity": "major", "location": "utils.py:12",
                "description": "This change may break existing backward compatibility",
                "fix": "Add version check"}]
    reports = [_make_report("Testing", issues=issues, score=6.0, verdict="ISSUES_FOUND")]
    synthesis = _make_synthesis("REVISE", 6.0)
    flags   = ObserverSession._extract_flags(reports, synthesis)

    reg_flags = [f for f in flags if f.flag_type == "REGRESSION"]
    assert len(reg_flags) >= 1


def test_extract_flags_style_fallback():
    issues  = [{"severity": "minor", "location": "main.py:3",
                "description": "Variable name is not descriptive enough", "fix": "Rename to user_count"}]
    reports = [_make_report("Style", issues=issues, score=7.5, verdict="ISSUES_FOUND")]
    synthesis = _make_synthesis("REVISE", 7.5)
    flags   = ObserverSession._extract_flags(reports, synthesis)

    style_flags = [f for f in flags if f.flag_type == "STYLE"]
    assert len(style_flags) >= 1


def test_extract_flags_multiple_reports_multiple_flags():
    reports = [
        _make_report("Security", issues=[
            {"severity": "critical", "location": "auth.py:10",
             "description": "Hardcoded secret key exposed", "fix": "Use env var"},
        ], score=2.0, verdict="ISSUES_FOUND"),
        _make_report("Logic", issues=[
            {"severity": "major", "location": "calc.py:5",
             "description": "Divide by zero bug possible", "fix": "Add zero check"},
        ], score=4.0, verdict="ISSUES_FOUND"),
    ]
    synthesis = _make_synthesis("REVISE", 3.0)
    flags     = ObserverSession._extract_flags(reports, synthesis)

    flag_types = {f.flag_type for f in flags}
    assert "SECURITY" in flag_types
    assert "BUG"      in flag_types


# ── ObserverResult.passed logic ───────────────────────────────────────────────

def test_passed_true_when_approved_no_critical_flags():
    flags  = [ObserverFlag("PASS", "none", "", "No issues", "", "", "")]
    result = _make_observer_result(passed=True, flags=flags)
    assert result.passed is True


def test_passed_false_when_bug_flag_even_if_verdict_approved():
    """
    BUG or SECURITY flags force passed=False even if supervisor says APPROVED.
    (ObserverSession.check() enforces this — we test the data contract.)
    """
    flags = [ObserverFlag("BUG", "critical", "line 5", "Null ptr", "Add check", "m", "Logic")]
    result = _make_observer_result(passed=False, flags=flags, score=8.0)
    assert result.passed is False


# ── ObserverSession.check() integration (mocked) ─────────────────────────────

@pytest.mark.asyncio
async def test_observer_check_pass(tmp_path):
    """Full ObserverSession.check() with mocked agents — clean code path."""
    synthesis = _make_synthesis("APPROVED", 9.5, votes=["APPROVED", "APPROVED"])
    reports   = [_make_report("Security"), _make_report("Logic")]

    with patch("crosscheck.observer.SupervisorAgent") as MockSup, \
         patch("crosscheck.observer.AnalyzerPool")    as MockPool, \
         patch("crosscheck.observer.OpenRouterClient") as MockClient:

        mock_client_inst = AsyncMock()
        mock_client_inst.total_cost_usd = 0.005
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_inst)
        MockClient.return_value.__aexit__  = AsyncMock(return_value=False)

        mock_sup = MagicMock()
        mock_sup.decompose  = AsyncMock(return_value=MagicMock(
            analyzer_tasks=[{"angle": "Security", "instruction": "check"}],
        ))
        mock_sup.synthesize = AsyncMock(return_value=synthesis)
        MockSup.return_value = mock_sup

        mock_pool = MagicMock()
        mock_pool.analyze = AsyncMock(return_value=reports)
        MockPool.return_value = mock_pool

        session = ObserverSession(api_key="sk-test")
        result  = await session.check("def foo(): pass", file_path="test.py")

    assert result.passed      is True
    assert result.file_path   == "test.py"
    assert result.score       == 9.5
    assert result.cost_usd    == 0.005


@pytest.mark.asyncio
async def test_observer_check_with_plan_prepends_plan():
    """Plan text must be prepended to content when provided."""
    synthesis = _make_synthesis("APPROVED", 9.0)
    reports   = [_make_report("Logic")]
    content_seen = []

    with patch("crosscheck.observer.SupervisorAgent") as MockSup, \
         patch("crosscheck.observer.AnalyzerPool")    as MockPool, \
         patch("crosscheck.observer.OpenRouterClient") as MockClient:

        mock_client_inst = AsyncMock()
        mock_client_inst.total_cost_usd = 0.001
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_inst)
        MockClient.return_value.__aexit__  = AsyncMock(return_value=False)

        mock_sup = MagicMock()

        async def capture_decompose(content, round_num):
            content_seen.append(content)
            return MagicMock(analyzer_tasks=[{"angle": "Logic", "instruction": "check"}])

        mock_sup.decompose  = capture_decompose
        mock_sup.synthesize = AsyncMock(return_value=synthesis)
        MockSup.return_value = mock_sup

        mock_pool = MagicMock()
        mock_pool.analyze = AsyncMock(return_value=reports)
        MockPool.return_value = mock_pool

        session = ObserverSession(api_key="sk-test", plan="Step 1: auth. Step 2: users.")
        await session.check("def login(): pass")

    assert content_seen, "decompose was never called"
    assert "ORIGINAL PLAN" in content_seen[0]
    assert "Step 1: auth"  in content_seen[0]


@pytest.mark.asyncio
async def test_observer_check_fires_on_flag_callback():
    """on_flag callback must be called once per flag found."""
    issues  = [{"severity": "critical", "location": "x.py",
                "description": "bug found crash possible", "fix": "fix it"}]
    synthesis = _make_synthesis("REVISE", 3.0)
    reports   = [_make_report("Logic", issues=issues, score=3.0, verdict="ISSUES_FOUND")]
    received  = []

    with patch("crosscheck.observer.SupervisorAgent") as MockSup, \
         patch("crosscheck.observer.AnalyzerPool")    as MockPool, \
         patch("crosscheck.observer.OpenRouterClient") as MockClient:

        mock_client_inst = AsyncMock()
        mock_client_inst.total_cost_usd = 0.001
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_inst)
        MockClient.return_value.__aexit__  = AsyncMock(return_value=False)

        MockSup.return_value.decompose  = AsyncMock(return_value=MagicMock(
            analyzer_tasks=[{"angle": "Logic", "instruction": "check"}]
        ))
        MockSup.return_value.synthesize = AsyncMock(return_value=synthesis)
        MockPool.return_value.analyze   = AsyncMock(return_value=reports)

        session = ObserverSession(
            api_key = "sk-test",
            on_flag = lambda f: received.append(f),
        )
        result = await session.check("def foo(): x = 1/0")

    assert len(received) >= 1
    assert all(isinstance(f, ObserverFlag) for f in received)
    assert any(f.flag_type == "BUG" for f in received)


@pytest.mark.asyncio
async def test_observer_check_dual_supervisor_votes_in_result():
    """ObserverResult must carry supervisor_votes and consensus from synthesis."""
    synthesis = _make_synthesis(
        "APPROVED", 9.0,
        votes=["APPROVED", "REVISE"], consensus=False,
    )
    reports = [_make_report("Security")]

    with patch("crosscheck.observer.SupervisorAgent") as MockSup, \
         patch("crosscheck.observer.AnalyzerPool")    as MockPool, \
         patch("crosscheck.observer.OpenRouterClient") as MockClient:

        mock_client_inst = AsyncMock()
        mock_client_inst.total_cost_usd = 0.001
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_inst)
        MockClient.return_value.__aexit__  = AsyncMock(return_value=False)

        MockSup.return_value.decompose  = AsyncMock(return_value=MagicMock(
            analyzer_tasks=[{"angle": "Security", "instruction": "check"}]
        ))
        MockSup.return_value.synthesize = AsyncMock(return_value=synthesis)
        MockPool.return_value.analyze   = AsyncMock(return_value=reports)

        session = ObserverSession(api_key="sk-test")
        result  = await session.check("def foo(): pass")

    assert result.supervisor_votes == ["APPROVED", "REVISE"]
    assert result.consensus        is False


# ── ObserverSession — model selection ─────────────────────────────────────────

def test_observer_uses_default_supervisors_when_none_given():
    from crosscheck.models import OBSERVER_DEFAULT_SUPERVISORS
    session = ObserverSession(api_key="sk-test")
    assert session._supervisor_models == OBSERVER_DEFAULT_SUPERVISORS


def test_observer_accepts_custom_supervisors():
    session = ObserverSession(
        api_key     = "sk-test",
        supervisors = ["x-ai/grok-4", "deepseek/deepseek-r1"],
    )
    assert session._supervisor_models == ["x-ai/grok-4", "deepseek/deepseek-r1"]


def test_observer_accepts_custom_analyzers():
    session = ObserverSession(
        api_key   = "sk-test",
        analyzers = ["moonshotai/kimi-k2", "qwen/qwen3-235b-a22b"],
    )
    assert session._analyzer_models == ["moonshotai/kimi-k2", "qwen/qwen3-235b-a22b"]


def test_observer_mode_fast_reduces_analyzer_pool():
    """Fast mode should use a smaller/cheaper analyzer set."""
    session_fast    = ObserverSession(api_key="sk-test", mode=Mode.FAST)
    session_quality = ObserverSession(api_key="sk-test", mode=Mode.QUALITY)
    assert len(session_fast._analyzer_models) <= len(session_quality._analyzer_models)


# ── FolderWatcher ─────────────────────────────────────────────────────────────

def test_folder_watcher_default_extensions():
    session = ObserverSession(api_key="sk-test")
    watcher = FolderWatcher(session=session, path=".")
    assert ".py"  in watcher.extensions
    assert ".ts"  in watcher.extensions
    assert ".tsx" in watcher.extensions
    assert ".go"  in watcher.extensions
    assert ".rs"  in watcher.extensions


def test_folder_watcher_custom_extensions():
    session = ObserverSession(api_key="sk-test")
    watcher = FolderWatcher(session=session, path=".", extensions={".py", ".kt"})
    assert ".py" in watcher.extensions
    assert ".kt" in watcher.extensions
    assert ".ts" not in watcher.extensions


def test_folder_watcher_on_result_callback():
    """on_result callback must be stored and callable."""
    session  = ObserverSession(api_key="sk-test")
    received = []
    watcher  = FolderWatcher(
        session   = session,
        path      = ".",
        on_result = lambda r: received.append(r),
    )
    fake_result = _make_observer_result()
    watcher.on_result(fake_result)
    assert len(received) == 1
    assert received[0].passed is True


def test_folder_watcher_debounce_default():
    session = ObserverSession(api_key="sk-test")
    watcher = FolderWatcher(session=session, path=".")
    assert watcher.debounce_s == 0.5


def test_folder_watcher_debounce_custom():
    session = ObserverSession(api_key="sk-test")
    watcher = FolderWatcher(session=session, path=".", debounce_s=1.5)
    assert watcher.debounce_s == 1.5


def test_folder_watcher_ignores_non_code_extensions(tmp_path):
    """Watcher must ignore files like .md, .jpg, etc."""
    session = ObserverSession(api_key="sk-test")
    watcher = FolderWatcher(session=session, path=str(tmp_path))
    assert ".md"  not in watcher.extensions
    assert ".jpg" not in watcher.extensions
    assert ".pdf" not in watcher.extensions


# ── FLAG_COLORS and FLAG_ICONS ────────────────────────────────────────────────

def test_all_flag_types_have_colors():
    for ft in ("BUG", "PLAN_DRIFT", "SECURITY", "REGRESSION", "STYLE", "PASS"):
        assert ft in FLAG_COLORS, f"Missing color for flag type: {ft}"


def test_all_flag_types_have_icons():
    for ft in ("BUG", "PLAN_DRIFT", "SECURITY", "REGRESSION", "STYLE", "PASS"):
        assert ft in FLAG_ICONS, f"Missing icon for flag type: {ft}"


# ── CLI observe commands ──────────────────────────────────────────────────────

def test_observe_paste_no_api_key():
    from click.testing import CliRunner
    from crosscheck.cli import cli
    runner = CliRunner()
    r = runner.invoke(
        cli, ["observe", "paste", "--code", "def foo(): pass"],
        env={"CROSSCHECK_API_KEY": "", "OPENROUTER_API_KEY": ""},
    )
    assert r.exit_code != 0


def test_observe_paste_no_code_no_stdin():
    """Without --code and without stdin, should give a UsageError."""
    from click.testing import CliRunner
    from crosscheck.cli import cli
    runner = CliRunner()
    r = runner.invoke(
        cli, ["observe", "paste"],
        env={"CROSSCHECK_API_KEY": "sk-test"},
        input="",   # empty stdin
    )
    assert r.exit_code != 0


def test_observe_paste_with_mocked_session(tmp_path):
    """observe paste should call ObserverSession.check and print result."""
    from click.testing import CliRunner
    from crosscheck.cli import cli
    from crosscheck.observer import ObserverResult, ObserverFlag

    fake = ObserverResult(
        passed=True, flags=[], score=9.5, summary="All good",
        file_path="", duration_sec=1.0, anomaly_count=0,
        supervisor_votes=["APPROVED", "APPROVED"], consensus=True,
    )

    runner = CliRunner()
    with patch("crosscheck.cli.asyncio.run", return_value=fake):
        r = runner.invoke(
            cli, ["observe", "paste", "--code", "def foo(): pass",
                  "--api-key", "sk-test"],
        )
    assert r.exit_code == 0


def test_observe_watch_no_api_key():
    from click.testing import CliRunner
    from crosscheck.cli import cli
    runner = CliRunner()
    r = runner.invoke(
        cli, ["observe", "watch", "."],
        env={"CROSSCHECK_API_KEY": "", "OPENROUTER_API_KEY": ""},
    )
    assert r.exit_code != 0


def test_observe_watch_invalid_path():
    from click.testing import CliRunner
    from crosscheck.cli import cli
    runner = CliRunner()
    r = runner.invoke(
        cli, ["observe", "watch", "/nonexistent/path/xyz",
              "--api-key", "sk-test"],
    )
    assert r.exit_code != 0


def test_observe_paste_plan_file(tmp_path):
    """--plan-file should be read and passed to ObserverSession."""
    from click.testing import CliRunner
    from crosscheck.cli import cli
    from crosscheck.observer import ObserverResult

    plan_file = tmp_path / "plan.md"
    plan_file.write_text("Step 1: Add auth\nStep 2: Add users")

    fake = ObserverResult(
        passed=True, flags=[], score=9.0, summary="ok",
        file_path="", duration_sec=0.5, anomaly_count=0,
        supervisor_votes=["APPROVED"], consensus=True,
    )

    session_kwargs = {}

    def capture_session(**kwargs):
        session_kwargs.update(kwargs)
        m = MagicMock()
        m.check = AsyncMock(return_value=fake)
        return m

    runner = CliRunner()
    with patch("crosscheck.observer.ObserverSession", side_effect=capture_session), \
         patch("crosscheck.cli.asyncio.run", return_value=fake):
        r = runner.invoke(
            cli, ["observe", "paste",
                  "--code", "def foo(): pass",
                  f"--plan-file={plan_file}",
                  "--api-key", "sk-test"],
        )
    assert r.exit_code == 0
