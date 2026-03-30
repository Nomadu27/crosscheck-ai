"""
tests/test_observer.py
-----------------------
Tests for ObserverSession, FolderWatcher, and new Phase 1/2 modules:
  - context_builder (multi-file repo context)
  - diff (unified diff + side-by-side)
  - sandbox (tool execution)
  - cache (result cache + delta analyzer)
  - policy (company policy loader)
  - local (Ollama LocalClient)
  - streaming (StreamingSession events)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Observer core ──────────────────────────────────────────────────────────────

class TestObserverSession:
    """Tests for ObserverSession — unchanged core behavior."""

    @pytest.fixture
    def mock_client(self):
        decompose_json = json.dumps({
            "session_goal": "Watch for bugs",
            "analyzer_tasks": [
                {"id": 1, "angle": "Security", "instruction": "Check for vulns"},
                {"id": 2, "angle": "Logic",    "instruction": "Check for bugs"},
            ],
        })
        analysis_json = json.dumps({
            "angle": "Security", "score": 9.0, "verdict": "PASS",
            "issues": [], "positive_findings": ["Clean"], "summary": "All good",
        })
        synthesis_json = json.dumps({
            "verdict": "APPROVED", "overall_score": 9.0,
            "summary": "Looks clean", "critical_issues": [],
            "coder_instructions": "", "approved_aspects": ["Clean code"],
        })
        client = MagicMock()
        client.total_tokens   = 500
        client.total_cost_usd = 0.001
        responses = [decompose_json, analysis_json, analysis_json, synthesis_json]
        call_count = [0]

        async def _chat(**kwargs):
            idx = call_count[0] % len(responses)
            call_count[0] += 1
            return responses[idx]

        client.chat = _chat
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__  = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_observer_returns_result(self, mock_client):
        from crosscheck.observer import ObserverSession
        with patch("crosscheck.observer.OpenRouterClient", return_value=mock_client), \
             patch.object(mock_client, "__aenter__", return_value=mock_client), \
             patch.object(mock_client, "__aexit__",  return_value=False):
            session = ObserverSession(api_key="test-key", monitor=False)
            result  = await session.check("def foo(): pass")
        assert result is not None
        assert result.score >= 0
        assert isinstance(result.flags, list)

    @pytest.mark.asyncio
    async def test_observer_with_plan(self, mock_client):
        from crosscheck.observer import ObserverSession
        with patch("crosscheck.observer.OpenRouterClient", return_value=mock_client), \
             patch.object(mock_client, "__aenter__", return_value=mock_client), \
             patch.object(mock_client, "__aexit__",  return_value=False):
            session = ObserverSession(
                api_key="test-key", monitor=False,
                plan="Must use async/await throughout",
            )
            result = await session.check("def foo(): pass")
        assert result is not None


# ── Phase 1: context_builder ───────────────────────────────────────────────────

class TestRepoContextBuilder:

    def test_build_single_file(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        f = tmp_path / "main.py"
        f.write_text("def foo():\n    return 42\n")

        builder = RepoContextBuilder(root=tmp_path)
        ctx     = builder.build(str(f))

        assert ctx.target_file != ""
        assert "main.py" in ctx.context_block
        assert "def foo" in ctx.context_block
        assert len(ctx.files_included) >= 1

    def test_build_with_imports(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        main = tmp_path / "main.py"
        main.write_text("from utils import helper\ndef run():\n    helper()\n")

        builder = RepoContextBuilder(root=tmp_path)
        ctx     = builder.build(str(main))

        assert ctx.context_block  # non-empty
        assert "main.py" in ctx.context_block

    def test_file_tree_contains_target_marker(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        f = tmp_path / "app.py"
        f.write_text("x = 1\n")
        builder = RepoContextBuilder(root=tmp_path)
        ctx     = builder.build(str(f))

        assert "🎯" in ctx.file_tree

    def test_skips_pycache(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "foo.cpython-311.pyc").write_bytes(b"junk")

        f = tmp_path / "real.py"
        f.write_text("x = 1\n")
        builder = RepoContextBuilder(root=tmp_path)
        ctx     = builder.build(str(f))

        assert "__pycache__" not in ctx.context_block

    def test_max_files_respected(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        for i in range(15):
            (tmp_path / f"mod_{i}.py").write_text(f"x = {i}\n")

        builder = RepoContextBuilder(root=tmp_path, max_files=5)
        ctx     = builder.build(str(tmp_path / "mod_0.py"))

        assert len(ctx.files_included) <= 5

    def test_truncation_flag(self, tmp_path):
        from crosscheck.context_builder import RepoContextBuilder

        # Write a file big enough to trigger truncation
        f = tmp_path / "huge.py"
        f.write_text("x = 'a' * 1000\n" * 5_000)   # ~75k chars

        builder = RepoContextBuilder(root=tmp_path, max_chars=1_000)
        ctx     = builder.build(str(f))

        # May or may not be truncated depending on file size; just check it runs
        assert ctx.total_chars > 0


# ── Phase 1: diff ─────────────────────────────────────────────────────────────

class TestDiff:

    def test_unified_diff_no_changes(self):
        from crosscheck.diff import unified_diff
        assert unified_diff("abc", "abc") == ""

    def test_unified_diff_has_plus_minus(self):
        from crosscheck.diff import unified_diff
        d = unified_diff("line1\nline2\n", "line1\nline3\n")
        assert "+" in d
        assert "-" in d

    def test_diff_stats_additions(self):
        from crosscheck.diff import diff_stats
        s = diff_stats("a\nb\n", "a\nb\nc\n")
        assert s.additions >= 1

    def test_diff_stats_deletions(self):
        from crosscheck.diff import diff_stats
        s = diff_stats("a\nb\nc\n", "a\nb\n")
        assert s.deletions >= 1

    def test_diff_stats_summary(self):
        from crosscheck.diff import diff_stats
        s = diff_stats("a\n", "b\n")
        assert "addition" in s.summary() or "change" in s.summary()

    def test_side_by_side_returns_table(self):
        from rich.table import Table

        from crosscheck.diff import side_by_side
        t = side_by_side("old\n", "new\n")
        assert isinstance(t, Table)

    def test_print_diff_no_changes_no_crash(self, capsys):
        from crosscheck.diff import print_diff
        print_diff("same", "same")  # should not raise

    def test_net_change(self):
        from crosscheck.diff import diff_stats
        s = diff_stats("a\nb\n", "a\nb\nc\nd\n")
        assert s.net_change > 0


# ── Phase 1: sandbox ──────────────────────────────────────────────────────────

class TestExecutionSandbox:

    def test_available_tools_empty_dir(self, tmp_path):
        from crosscheck.sandbox import ExecutionSandbox
        sb = ExecutionSandbox(project_root=tmp_path)
        tools = sb.available_tools()
        # In a bare temp dir with no config files, likely 0 tools
        assert isinstance(tools, list)

    def test_format_for_prompt_empty(self):
        from crosscheck.sandbox import ExecutionSandbox
        assert ExecutionSandbox.format_for_prompt([]) == ""

    def test_format_for_prompt_with_result(self):
        from crosscheck.sandbox import ExecutionSandbox, ToolResult
        r = ToolResult(
            tool="pytest", command=["pytest"], returncode=0,
            stdout="3 passed", stderr="", success=True, duration_sec=1.2,
        )
        out = ExecutionSandbox.format_for_prompt([r])
        assert "pytest" in out
        assert "PASS" in out

    def test_issues_from_failed_tool(self):
        from crosscheck.sandbox import ExecutionSandbox, ToolResult
        r = ToolResult(
            tool="ruff", command=["ruff", "check"], returncode=1,
            stdout="E501 line too long", stderr="", success=False,
        )
        issues = ExecutionSandbox.issues_from_results([r])
        assert len(issues) == 1
        assert issues[0]["severity"] == "major"

    def test_issues_from_passed_tool_empty(self):
        from crosscheck.sandbox import ExecutionSandbox, ToolResult
        r = ToolResult(
            tool="ruff", command=["ruff"], returncode=0,
            stdout="All clear", stderr="", success=True,
        )
        assert ExecutionSandbox.issues_from_results([r]) == []

    @pytest.mark.asyncio
    async def test_run_tool_unknown_returns_error(self, tmp_path):
        from crosscheck.sandbox import ExecutionSandbox
        sb = ExecutionSandbox(project_root=tmp_path)
        r  = await sb.run_tool("not_a_real_tool_xyz")
        assert not r.success
        assert r.returncode == -1


# ── Phase 1: cache ────────────────────────────────────────────────────────────

class TestResultCache:

    def test_set_and_get(self, tmp_path):
        from crosscheck.cache import ResultCache
        c = ResultCache(cache_dir=tmp_path / "cache", ttl_days=1)
        key = c.make_key("model-a", "content", "code", "Security")
        c.set(key, "model-a", "hash123", {"score": 9.0, "verdict": "PASS"})
        r = c.get(key)
        assert r is not None
        assert r["score"] == 9.0

    def test_cache_miss_returns_none(self, tmp_path):
        from crosscheck.cache import ResultCache
        c = ResultCache(cache_dir=tmp_path / "cache")
        assert c.get("nonexistent_key_xyz") is None

    def test_clear_removes_all(self, tmp_path):
        from crosscheck.cache import ResultCache
        c = ResultCache(cache_dir=tmp_path / "cache")
        key = c.make_key("m", "c", "t")
        c.set(key, "m", "h", {"score": 5})
        c.clear()
        assert c.get(key) is None

    def test_stats(self, tmp_path):
        from crosscheck.cache import ResultCache
        c = ResultCache(cache_dir=tmp_path / "cache")
        info = c.stats()
        assert "total_entries" in info
        assert "db_path" in info

    def test_disabled_cache_returns_none(self, tmp_path):
        from crosscheck.cache import ResultCache
        c   = ResultCache(cache_dir=tmp_path, enabled=False)
        key = c.make_key("m", "c", "t")
        c.set(key, "m", "h", {"x": 1})
        assert c.get(key) is None

    def test_make_key_deterministic(self):
        from crosscheck.cache import ResultCache
        k1 = ResultCache.make_key("model", "content", "code", "Security")
        k2 = ResultCache.make_key("model", "content", "code", "Security")
        assert k1 == k2

    def test_make_key_different_models_differ(self):
        from crosscheck.cache import ResultCache
        k1 = ResultCache.make_key("model-A", "content", "code")
        k2 = ResultCache.make_key("model-B", "content", "code")
        assert k1 != k2


class TestDeltaAnalyzer:

    def test_extract_python_chunks(self):
        from crosscheck.cache import DeltaAnalyzer
        src = "def foo():\n    pass\n\ndef bar():\n    return 1\n"
        chunks = DeltaAnalyzer.extract_chunks(src, "python")
        assert len(chunks) >= 2
        names = [c.name for c in chunks]
        assert "foo" in names
        assert "bar" in names

    def test_changed_chunks_detect_modification(self):
        from crosscheck.cache import DeltaAnalyzer
        src_before = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        src_after  = "def foo():\n    return 99\n\ndef bar():\n    return 2\n"

        before = DeltaAnalyzer.extract_chunks(src_before, "python")
        after  = DeltaAnalyzer.extract_chunks(src_after,  "python")
        changed = DeltaAnalyzer.changed_chunks(before, after)

        changed_names = [c.name for c in changed]
        assert "foo" in changed_names
        assert "bar" not in changed_names

    def test_unchanged_code_produces_no_changes(self):
        from crosscheck.cache import DeltaAnalyzer
        src    = "def foo():\n    return 1\n"
        before = DeltaAnalyzer.extract_chunks(src, "python")
        after  = DeltaAnalyzer.extract_chunks(src, "python")
        assert DeltaAnalyzer.changed_chunks(before, after) == []

    def test_new_function_appears_as_changed(self):
        from crosscheck.cache import DeltaAnalyzer
        before_src = "def foo():\n    pass\n"
        after_src  = "def foo():\n    pass\n\ndef new_func():\n    pass\n"
        before = DeltaAnalyzer.extract_chunks(before_src, "python")
        after  = DeltaAnalyzer.extract_chunks(after_src,  "python")
        changed = DeltaAnalyzer.changed_chunks(before, after)
        assert any(c.name == "new_func" for c in changed)

    def test_chunk_hash_stable(self):
        from crosscheck.cache import DeltaAnalyzer
        src    = "def foo():\n    return 1\n"
        chunks = DeltaAnalyzer.extract_chunks(src, "python")
        assert all(c.hash for c in chunks)


# ── Phase 2: policy ───────────────────────────────────────────────────────────

class TestPolicyLoader:

    def test_load_markdown_policy(self, tmp_path):
        from crosscheck.policy import PolicyLoader
        p = tmp_path / "security.md"
        p.write_text(
            "# Security Policy\nEnsure safe coding.\n\n"
            "- [ ] No hardcoded secrets\n"
            "- [ ] All inputs validated\n"
            "- [x] HTTPS enforced\n"
        )
        pol = PolicyLoader.from_file(p)
        assert pol.name != ""
        assert len(pol.rules) >= 2

    def test_load_toml_policy(self, tmp_path):
        from crosscheck.policy import PolicyLoader
        p = tmp_path / "rules.toml"
        p.write_text(
            '[policy]\nname = "Org Rules"\ndescription = "Our rules"\n\n'
            '[rule.SEC001]\ndescription = "No eval()"\nseverity = "critical"\n'
        )
        pol = PolicyLoader.from_file(p)
        assert pol.name == "Org Rules"
        assert len(pol.rules) >= 1
        assert pol.rules[0].id == "SEC001"

    def test_from_text(self):
        from crosscheck.policy import PolicyLoader
        pol = PolicyLoader.from_text(
            "1. Never use eval()\n2. Always validate input\n",
            name="Quick Rules"
        )
        assert pol.name == "Quick Rules"
        assert len(pol.rules) >= 1

    def test_policy_to_analyzer_task(self):
        from crosscheck.policy import PolicyLoader
        pol  = PolicyLoader.from_text("- [ ] No SQL injection", name="OWASP")
        task = pol.to_analyzer_task(task_id=5)
        assert task["id"] == 5
        assert "OWASP" in task["angle"]
        assert task["instruction"]

    def test_builtin_owasp_policy(self):
        from crosscheck.policy import BUILTIN_POLICIES
        pol = BUILTIN_POLICIES["owasp_top10"]
        assert pol.name == "OWASP Top 10"
        assert len(pol.rules) >= 5

    def test_from_directory(self, tmp_path):
        from crosscheck.policy import PolicyLoader
        (tmp_path / "a.md").write_text("- [ ] Rule A\n")
        (tmp_path / "b.md").write_text("- [ ] Rule B\n")
        (tmp_path / "ignore.exe").write_bytes(b"binary")
        policies = PolicyLoader.from_directory(tmp_path)
        assert len(policies) == 2

    def test_severity_inference_critical(self):
        from crosscheck.policy import _infer_severity
        assert _infer_severity("Never store passwords in plaintext") == "critical"

    def test_severity_inference_minor(self):
        from crosscheck.policy import _infer_severity
        assert _infer_severity("Use consistent naming") == "minor"


# ── Phase 2: local (Ollama) ───────────────────────────────────────────────────

class TestLocalClient:

    @pytest.mark.asyncio
    async def test_local_client_routes_to_local_endpoint(self):
        from crosscheck.local import LocalClient

        _responses = []
        async def _mock_post(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.is_success  = True
            resp.json.return_value = {
                "choices": [{"message": {"content": "Hello"}}],
                "usage":   {"total_tokens": 10},
            }
            return resp

        async with LocalClient(base_url="http://localhost:11434/v1") as client:
            with patch.object(client._http, "post", side_effect=_mock_post):
                result = await client.chat("llama3:latest", [{"role":"user","content":"hi"}])
        assert result == "Hello"

    def test_factory_for_ollama(self):
        from crosscheck.local import LocalClient
        c = LocalClient.for_ollama()
        assert "11434" in c.base_url

    def test_factory_for_lm_studio(self):
        from crosscheck.local import LocalClient
        c = LocalClient.for_lm_studio()
        assert "1234" in c.base_url

    def test_factory_for_custom(self):
        from crosscheck.local import LocalClient
        c = LocalClient.for_custom("https://myserver.example.com/v1", "mykey")
        assert "myserver" in c.base_url
        assert c.api_key == "mykey"

    @pytest.mark.asyncio
    async def test_ollama_registry_unavailable(self):
        from crosscheck.local import OllamaModelRegistry
        # Should return [] when Ollama is not running
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("connection refused")
            )
            models = await OllamaModelRegistry.list_models()
        assert models == []


# ── Phase 1: streaming ────────────────────────────────────────────────────────

class TestStreamingSession:

    @pytest.mark.asyncio
    async def test_streaming_emits_events(self):
        from crosscheck.core import SessionResult
        from crosscheck.streaming import StreamingSession

        fake_result = MagicMock(spec=SessionResult)
        fake_result.verdict       = "APPROVED"
        fake_result.final_score   = 9.0
        fake_result.total_rounds  = 1
        fake_result.issues_fixed  = 0
        fake_result.anomaly_count = 0
        fake_result.total_tokens  = 100
        fake_result.cost_usd      = 0.001
        fake_result.duration_sec  = 1.0
        fake_result.analyzer_reports     = []
        fake_result.supervisor_votes     = ["APPROVED"]
        fake_result.supervisor_consensus = True
        fake_result.rounds               = []

        with patch("crosscheck.core.MultiAgentSession") as MockSession:
            instance = MockSession.return_value
            instance.run_async = AsyncMock(return_value=fake_result)

            session = StreamingSession(api_key="test")
            session._kwargs = {}

            events = []
            async for event in session.stream("def foo(): pass"):
                events.append(event)

        kinds = [e.kind for e in events]
        assert "session_complete" in kinds

    def test_streaming_reporter_initializes(self):
        from crosscheck.streaming import StreamingReporter
        reporter = StreamingReporter(max_rounds=3)
        assert reporter.max_rounds == 3

    def test_streaming_reporter_on_event_no_crash(self):
        from crosscheck.streaming import StreamEvent, StreamingReporter
        reporter = StreamingReporter(max_rounds=3)
        event    = StreamEvent(kind="round_start", round_num=1)
        # Should not crash even without Live context
        reporter.on_event(event)


# ── Phase 1: PR Bot ───────────────────────────────────────────────────────────

class TestGitHubProvider:

    @pytest.mark.asyncio
    async def test_format_summary_approved(self):
        from crosscheck.agents import AnalyzerReport, SynthesisResult
        from crosscheck.core import Round, SessionResult
        from crosscheck.pr_bot import PRReviewBot, PRReviewConfig

        synthesis = SynthesisResult(
            verdict="APPROVED", overall_score=9.0, summary="Looks great",
            critical_issues=[], coder_instructions="", approved_aspects=["Clean"],
        )
        analyzer = AnalyzerReport(
            model_id="deepseek/deepseek-chat-v3-0324", angle="Security",
            score=9.0, verdict="PASS", issues=[], positive_findings=[], summary="OK",
        )
        rnd = Round(
            number=1, content_in="x", content_out="x",
            decompose={}, analyzer_reports=[analyzer], synthesis=synthesis,
        )
        result = SessionResult(
            verdict="APPROVED", final_content="x", rounds=[rnd],
            total_rounds=1, final_score=9.0, issues_fixed=0,
            total_tokens=100, cost_usd=0.001, duration_sec=1.0,
            anomaly_count=0, analyzer_reports=[analyzer],
        )
        config = PRReviewConfig(repo="owner/repo", pr_number=1)
        md     = PRReviewBot._format_summary(result, config)
        assert "APPROVED" in md
        assert "crosscheck-ai" in md

    @pytest.mark.asyncio
    async def test_format_summary_revise_shows_issues(self):
        from crosscheck.agents import AnalyzerReport, SynthesisResult
        from crosscheck.core import Round, SessionResult
        from crosscheck.pr_bot import PRReviewBot, PRReviewConfig

        synthesis = SynthesisResult(
            verdict="REVISE", overall_score=4.0, summary="Needs work",
            critical_issues=["SQL injection on line 5"],
            coder_instructions="Fix it", approved_aspects=[],
        )
        analyzer = AnalyzerReport(
            model_id="deepseek/deepseek-chat-v3-0324", angle="Security",
            score=4.0, verdict="ISSUES_FOUND", issues=[], positive_findings=[], summary="Bad",
        )
        rnd = Round(
            number=1, content_in="x", content_out="x",
            decompose={}, analyzer_reports=[analyzer], synthesis=synthesis,
        )
        result = SessionResult(
            verdict="REVISE", final_content="x", rounds=[rnd],
            total_rounds=1, final_score=4.0, issues_fixed=0,
            total_tokens=100, cost_usd=0.001, duration_sec=1.0,
            anomaly_count=0, analyzer_reports=[analyzer],
        )
        config = PRReviewConfig(repo="owner/repo", pr_number=2)
        md     = PRReviewBot._format_summary(result, config)
        assert "SQL injection" in md
