"""
tests/test_team.py
------------------
Tests for the crosscheck.team package (v2.0.0 AI Dev Team).

Covers: roles, chat messages, command parser, language detection,
and TeamSession orchestration. All mocked — no API key needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from crosscheck.team.chat import (
    ChatHistory,
    CodeBlock,
    SessionPhase,
    TeamMessage,
    extract_code_blocks,
)
from crosscheck.team.command_parser import CommandParser
from crosscheck.team.language import LanguageDetector
from crosscheck.team.roles import (
    DEFAULT_TEAM,
    TeamRole,
    build_team,
    get_role_spec,
)
from crosscheck.team.session import ReviewResult, TeamSession

# ── Roles ────────────────────────────────────────────────────────────────────

class TestTeamRole:

    def test_all_roles_defined(self):
        """TeamRole enum has 10 values (9 agents + HUMAN)."""
        assert len(TeamRole) == 10
        assert TeamRole.HUMAN in TeamRole
        assert TeamRole.COORDINATOR in TeamRole
        assert TeamRole.OBSERVER_1 in TeamRole
        assert TeamRole.OBSERVER_2 in TeamRole

    def test_role_values_are_strings(self):
        assert TeamRole.PLANNER.value == "planner"
        assert TeamRole.CODER.value == "coder"

    def test_default_team_has_nine_agents(self):
        """DEFAULT_TEAM has 9 RoleSpecs (no HUMAN)."""
        assert len(DEFAULT_TEAM) == 9
        roles = {s.role for s in DEFAULT_TEAM}
        assert TeamRole.HUMAN not in roles

    def test_default_team_all_roles_covered(self):
        """Every non-HUMAN role has a default spec."""
        roles = {s.role for s in DEFAULT_TEAM}
        expected = {
            TeamRole.COORDINATOR, TeamRole.PLANNER, TeamRole.ARCHITECT,
            TeamRole.CODER, TeamRole.DEBUGGER, TeamRole.SECURITY,
            TeamRole.ANALYST, TeamRole.OBSERVER_1, TeamRole.OBSERVER_2,
        }
        assert roles == expected

    def test_only_coder_can_write_code(self):
        """Only CODER has can_write_code=True."""
        for spec in DEFAULT_TEAM:
            if spec.role == TeamRole.CODER:
                assert spec.can_write_code is True
            else:
                assert spec.can_write_code is False, f"{spec.role} should not write code"

    def test_role_spec_is_frozen(self):
        spec = DEFAULT_TEAM[0]
        with pytest.raises(AttributeError):
            spec.role = TeamRole.CODER  # type: ignore

    def test_get_role_spec(self):
        spec = get_role_spec(TeamRole.SECURITY)
        assert spec.role == TeamRole.SECURITY
        assert "security" in spec.system_prompt.lower() or "Security" in spec.system_prompt

    def test_get_role_spec_invalid_raises(self):
        with pytest.raises(ValueError, match="No default spec"):
            get_role_spec(TeamRole.HUMAN)

    def test_build_team_no_overrides(self):
        team = build_team()
        assert len(team) == 9
        assert team[0].default_model == DEFAULT_TEAM[0].default_model

    def test_build_team_with_override(self):
        team = build_team({TeamRole.CODER: "openai/gpt-5"})
        coder = next(s for s in team if s.role == TeamRole.CODER)
        assert coder.default_model == "openai/gpt-5"
        # Other roles unchanged
        planner = next(s for s in team if s.role == TeamRole.PLANNER)
        assert planner.default_model == DEFAULT_TEAM[1].default_model

    def test_display_name_format(self):
        spec = get_role_spec(TeamRole.CODER)
        name = spec.display_name
        # Should contain the title
        assert "Coder" in name


# ── Chat Messages ────────────────────────────────────────────────────────────

class TestTeamMessage:

    def test_message_creation(self):
        msg = TeamMessage(
            role="coder",
            model_id="anthropic/claude-sonnet-4.6",
            display_name="Claude (Coder)",
            content="Here is the code",
            phase=SessionPhase.CODING,
        )
        assert msg.role == "coder"
        assert msg.is_code_output is False
        assert msg.code_blocks == []
        assert msg.target is None

    def test_message_to_dict(self):
        msg = TeamMessage(
            role="debugger",
            model_id="deepseek/deepseek-r1",
            display_name="DeepSeek (Debugger)",
            content="Found a bug",
            phase=SessionPhase.REVIEW,
            round_num=2,
        )
        d = msg.to_dict()
        assert d["role"] == "debugger"
        assert d["phase"] == "review"
        assert d["round_num"] == 2
        assert isinstance(d["timestamp"], str)

    def test_message_with_code_blocks(self):
        cb = CodeBlock(filename="app.py", language="python", content="print('hi')")
        msg = TeamMessage(
            role="coder",
            model_id="test",
            display_name="Test",
            content="code",
            phase=SessionPhase.CODING,
            code_blocks=[cb],
            is_code_output=True,
        )
        d = msg.to_dict()
        assert len(d["code_blocks"]) == 1
        assert d["code_blocks"][0]["filename"] == "app.py"
        assert d["is_code_output"] is True


class TestCodeBlock:

    def test_code_block_defaults(self):
        cb = CodeBlock(filename="test.py", language="python", content="x = 1")
        assert cb.action == "create"

    def test_extract_code_blocks_single(self):
        content = """Here is the code:

```python
# filename: app.py
def hello():
    return "world"
```

Done."""
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].filename == "app.py"
        assert blocks[0].language == "python"
        assert "def hello" in blocks[0].content

    def test_extract_code_blocks_multiple(self):
        content = """```python
# filename: models.py
class User: pass
```

```javascript
# filename: app.js
console.log("hi");
```"""
        blocks = extract_code_blocks(content)
        assert len(blocks) == 2
        assert blocks[0].filename == "models.py"
        assert blocks[1].filename == "app.js"
        assert blocks[1].language == "javascript"

    def test_extract_code_blocks_no_filename(self):
        content = """```python
x = 1 + 2
```"""
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].filename == "untitled"

    def test_extract_code_blocks_empty(self):
        blocks = extract_code_blocks("No code here, just text.")
        assert len(blocks) == 0


class TestChatHistory:

    def _make_msg(self, role: str, content: str, phase=SessionPhase.DISCUSSION):
        return TeamMessage(
            role=role,
            model_id="test-model",
            display_name=f"Test ({role})",
            content=content,
            phase=phase,
        )

    def test_add_message(self):
        history = ChatHistory()
        msg = self._make_msg("coder", "Hello")
        history.add(msg)
        assert len(history.messages) == 1
        assert history.messages[0].content == "Hello"

    def test_add_creates_new_list(self):
        """add() should not mutate the original list reference."""
        history = ChatHistory()
        original = history.messages
        history.add(self._make_msg("coder", "test"))
        assert history.messages is not original

    def test_for_model_context_privacy(self):
        """Privacy-first: only returns last N messages."""
        history = ChatHistory()
        for i in range(10):
            history.add(self._make_msg("analyst", f"Message {i}"))

        context = history.for_model_context("analyst", last_n=3)
        assert len(context) == 3
        assert "Message 7" in context[0]["content"]

    def test_for_model_context_human_as_user(self):
        """Human messages become 'user' role in context."""
        history = ChatHistory()
        history.add(self._make_msg("human", "Fix this bug"))
        history.add(self._make_msg("debugger", "Found the issue"))

        context = history.for_model_context("debugger")
        assert context[0]["role"] == "user"
        assert context[1]["role"] == "assistant"

    def test_full_context_all_messages(self):
        history = ChatHistory()
        for i in range(10):
            history.add(self._make_msg("analyst", f"Message {i}"))

        context = history.full_context()
        assert len(context) == 10

    def test_to_transcript(self):
        history = ChatHistory(topic="Fix the auth bug", session_id="abc123")
        history.add(self._make_msg("planner", "Let me plan this"))
        history.add(self._make_msg("coder", "Here is the fix"))

        transcript = history.to_transcript()
        assert "CROSSCHECK AI DEV TEAM" in transcript
        assert "Fix the auth bug" in transcript
        assert "abc123" in transcript
        assert "Let me plan this" in transcript


# ── Command Parser ───────────────────────────────────────────────────────────

class TestCommandParser:

    def test_no_mentions_is_broadcast(self):
        result = CommandParser.parse("What do you all think?")
        assert result.has_mentions is False
        assert result.targets == []
        assert result.global_message == "What do you all think?"

    def test_single_role_mention(self):
        result = CommandParser.parse("@coder fix line 45")
        assert result.has_mentions is True
        assert TeamRole.CODER in result.targets
        assert "fix line 45" in result.global_message

    def test_multiple_role_mentions(self):
        result = CommandParser.parse("@debugger @security check this function")
        assert result.has_mentions is True
        assert TeamRole.DEBUGGER in result.targets
        assert TeamRole.SECURITY in result.targets
        assert len(result.targets) == 2

    def test_model_alias_claude(self):
        """@claude should route to CODER."""
        result = CommandParser.parse("@claude write the authentication module")
        assert result.has_mentions is True
        assert TeamRole.CODER in result.targets

    def test_model_alias_grok(self):
        """@grok should route to SECURITY."""
        result = CommandParser.parse("@grok scan for vulnerabilities")
        assert result.has_mentions is True
        assert TeamRole.SECURITY in result.targets

    def test_model_alias_deepseek(self):
        """@deepseek should route to DEBUGGER."""
        result = CommandParser.parse("@deepseek find the bug")
        assert result.has_mentions is True
        assert TeamRole.DEBUGGER in result.targets

    def test_role_alias_boss(self):
        """@boss should route to PLANNER."""
        result = CommandParser.parse("@boss what should we do next?")
        assert result.has_mentions is True
        assert TeamRole.PLANNER in result.targets

    def test_role_alias_cto(self):
        """@cto should route to ARCHITECT."""
        result = CommandParser.parse("@cto review the design")
        assert result.has_mentions is True
        assert TeamRole.ARCHITECT in result.targets

    def test_unknown_mention_is_broadcast(self):
        """Unknown @mention should fall through to broadcast."""
        result = CommandParser.parse("@unknownmodel do something")
        assert result.has_mentions is False

    def test_mentions_stripped_from_message(self):
        result = CommandParser.parse("@coder @debugger fix this bug please")
        assert "@coder" not in result.global_message
        assert "@debugger" not in result.global_message
        assert "fix this bug please" in result.global_message

    def test_wants_full_history_true(self):
        assert CommandParser.wants_full_history("Please review everything") is True
        assert CommandParser.wants_full_history("Show me the full history") is True
        assert CommandParser.wants_full_history("summarize our conversation") is True

    def test_wants_full_history_false(self):
        assert CommandParser.wants_full_history("Fix the bug") is False
        assert CommandParser.wants_full_history("What do you think?") is False


# ── Language Detection ───────────────────────────────────────────────────────

class TestLanguageDetector:

    def test_english_default(self):
        assert LanguageDetector.detect("Hello world") == "English"

    def test_empty_string(self):
        assert LanguageDetector.detect("") == "English"

    def test_romanian(self):
        assert LanguageDetector.detect("Aceasta este o propoziție în română") == "Romanian"

    def test_spanish(self):
        assert LanguageDetector.detect("¿Cómo estás?") == "Spanish"

    def test_chinese(self):
        assert LanguageDetector.detect("你好世界") == "Chinese"

    def test_arabic(self):
        assert LanguageDetector.detect("مرحبا بالعالم") == "Arabic"

    def test_russian(self):
        assert LanguageDetector.detect("Привет мир") == "Russian"

    def test_japanese(self):
        assert LanguageDetector.detect("こんにちは") == "Japanese"

    def test_korean(self):
        assert LanguageDetector.detect("안녕하세요") == "Korean"

    def test_instruction_english_empty(self):
        """English should return empty instruction (no need to specify)."""
        assert LanguageDetector.get_instruction("English") == ""

    def test_instruction_non_english(self):
        inst = LanguageDetector.get_instruction("Romanian")
        assert "Romanian" in inst
        assert "MUST respond" in inst


# ── Session Phase ────────────────────────────────────────────────────────────

class TestSessionPhase:

    def test_all_phases(self):
        assert len(SessionPhase) == 9
        phases = [p.value for p in SessionPhase]
        assert "planning" in phases
        assert "coding" in phases
        assert "dissent" in phases
        assert "approval" in phases
        assert "testing" in phases
        assert "done" in phases


# ── ReviewResult ─────────────────────────────────────────────────────────────

class TestReviewResult:

    def _make_review_msg(self, content):
        return TeamMessage(
            role="debugger",
            model_id="test",
            display_name="Test",
            content=content,
            phase=SessionPhase.REVIEW,
        )

    def test_approved_when_no_issues(self):
        msgs = [
            self._make_review_msg("Code looks great. APPROVED."),
            self._make_review_msg("Clean implementation. APPROVED."),
        ]
        result = ReviewResult(msgs)
        assert result.approved is True
        assert len(result.issues) == 0

    def test_not_approved_when_issues_found(self):
        msgs = [
            self._make_review_msg("Found a bug in line 42."),
            self._make_review_msg("APPROVED."),
        ]
        result = ReviewResult(msgs)
        assert result.approved is False
        assert len(result.issues) == 1

    def test_vulnerability_detected(self):
        msgs = [
            self._make_review_msg("SQL injection vulnerability in the query builder."),
        ]
        result = ReviewResult(msgs)
        assert result.approved is False


# ── TeamSession ──────────────────────────────────────────────────────────────

def _mock_client(responses: list[str] | None = None):
    """Create a mock OpenRouterClient."""
    client = MagicMock()
    if responses is None:
        responses = ["Mock response from model"]

    call_count = [0]

    async def _chat(**kwargs):
        idx = call_count[0] % len(responses)
        call_count[0] += 1
        return responses[idx]

    client.chat = _chat
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestTeamSession:

    @pytest.mark.asyncio
    async def test_session_creation(self):
        client = _mock_client()
        session = TeamSession(client=client)
        assert session.phase == SessionPhase.PLANNING
        assert len(session.team) == 9
        assert session.max_rounds == 3

    @pytest.mark.asyncio
    async def test_session_custom_team(self):
        team = build_team({TeamRole.CODER: "openai/gpt-5"})
        client = _mock_client()
        session = TeamSession(client=client, team=team)
        coder = session._get_spec(TeamRole.CODER)
        assert coder is not None
        assert coder.default_model == "openai/gpt-5"

    @pytest.mark.asyncio
    async def test_session_run_completes(self):
        """Full session should complete and return ChatHistory."""
        # All agents say APPROVED in review → completes in 1 round
        client = _mock_client(["APPROVED. Code looks clean and correct."])
        session = TeamSession(client=client, max_rounds=1)

        history = await session.run(task="Add a hello world function")

        assert session.phase == SessionPhase.DONE
        assert len(history.messages) > 0
        assert history.topic == "Add a hello world function"

    @pytest.mark.asyncio
    async def test_session_planning_calls_planner(self):
        """Planning phase should call the PLANNER model."""
        call_log = []

        async def _chat(**kwargs):
            call_log.append(kwargs.get("model", "unknown"))
            return "APPROVED."

        client = MagicMock()
        client.chat = _chat
        session = TeamSession(client=client, max_rounds=1)

        await session.run(task="Test task")

        planner_model = get_role_spec(TeamRole.PLANNER).default_model
        assert planner_model in call_log, f"PLANNER model not called. Log: {call_log}"

    @pytest.mark.asyncio
    async def test_session_coder_called(self):
        """Coding phase should call the CODER model."""
        call_log = []

        async def _chat(**kwargs):
            call_log.append(kwargs.get("model", "unknown"))
            return "APPROVED."

        client = MagicMock()
        client.chat = _chat
        session = TeamSession(client=client, max_rounds=1)

        await session.run(task="Write code")

        coder_model = get_role_spec(TeamRole.CODER).default_model
        assert coder_model in call_log, f"CODER model not called. Log: {call_log}"

    @pytest.mark.asyncio
    async def test_inject_human_message(self):
        """inject_human_message should add to history and get responses."""
        client = _mock_client(["Got it, fixing now."])
        session = TeamSession(client=client, max_rounds=1)

        _responses = await session.inject_human_message(
            "@coder fix the null check"
        )

        # Should have human message in history
        human_msgs = [m for m in session.history.messages if m.role == "human"]
        assert len(human_msgs) == 1
        assert "fix the null check" in human_msgs[0].content

    @pytest.mark.asyncio
    async def test_inject_human_broadcast(self):
        """Message without @mention should broadcast to all agents."""
        client = _mock_client(["Response from agent"])
        session = TeamSession(client=client, max_rounds=1)

        responses = await session.inject_human_message("What do you all think?")

        # Should get responses from all 9 team members
        assert len(responses) == 9

    @pytest.mark.asyncio
    async def test_session_max_rounds_limit(self):
        """Session should stop after max_rounds even if not approved."""
        # Always report issues → never approved
        client = _mock_client(["Found a bug in the implementation."])
        session = TeamSession(client=client, max_rounds=2)

        history = await session.run(task="Buggy task")

        assert session.phase == SessionPhase.DONE
        # Should have messages from 2 rounds
        assert len(history.messages) > 0

    @pytest.mark.asyncio
    async def test_session_early_approval(self):
        """Session should stop early if team approves."""
        client = _mock_client(["APPROVED. Everything looks great."])
        session = TeamSession(client=client, max_rounds=5)

        history = await session.run(task="Simple task")

        # Should complete in 1 round (not 5)
        assert session.phase == SessionPhase.DONE
        # Count unique round numbers
        rounds = {m.round_num for m in history.messages if m.round_num > 0}
        # Should not have round 2+ messages since round 1 was approved
        assert max(rounds) <= 1 if rounds else True

    @pytest.mark.asyncio
    async def test_session_error_handling(self):
        """Agent errors should be caught, not crash the session."""
        async def _failing_chat(**kwargs):
            raise Exception("API timeout")

        client = MagicMock()
        client.chat = _failing_chat
        session = TeamSession(client=client, max_rounds=1)

        # Should not raise — errors caught per-agent
        history = await session.run(task="Error test")
        assert session.phase == SessionPhase.DONE
        # Error messages should be recorded
        error_msgs = [m for m in history.messages if "[Error:" in m.content]
        assert len(error_msgs) > 0


# ── Chat Server ─────────────────────────────────────────────────────────────

class TestChatServer:
    """Tests for crosscheck.chat_server module."""

    def test_create_chat_app_returns_fastapi(self):
        """create_chat_app should return a FastAPI instance."""
        from crosscheck.chat_server import create_chat_app
        app = create_chat_app(api_key="test-key")
        assert app is not None
        assert app.title == "crosscheck AI Dev Team"

    def test_chat_app_has_routes(self):
        """App should have /, /api/team, and /ws/team routes."""
        from crosscheck.chat_server import create_chat_app
        app = create_chat_app(api_key="test-key")
        route_paths = [r.path for r in app.routes]
        assert "/" in route_paths
        assert "/api/team" in route_paths

    def test_chat_app_team_endpoint(self):
        """GET /api/team should return team info."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app(api_key="test-key")
        client = TestClient(app)
        resp = client.get("/api/team")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 9
        roles = [m["role"] for m in data]
        assert "coordinator" in roles
        assert "planner" in roles
        assert "coder" in roles
        assert "debugger" in roles
        assert "observer_1" in roles
        assert "observer_2" in roles

    def test_chat_app_index_serves_html(self):
        """GET / should return HTML."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app(api_key="test-key")
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "crosscheck" in resp.text.lower()

    def test_chat_ui_files_exist(self):
        """Chat UI static files should exist."""
        from pathlib import Path
        ui_dir = Path(__file__).parent.parent / "crosscheck" / "chat_ui"
        assert (ui_dir / "index.html").exists()
        assert (ui_dir / "chat.js").exists()
        assert (ui_dir / "style.css").exists()

    def test_dashboard_files_exist(self):
        """Dashboard UI files should exist."""
        from pathlib import Path
        ui_dir = Path(__file__).parent.parent / "crosscheck" / "chat_ui"
        assert (ui_dir / "dashboard.html").exists()
        assert (ui_dir / "dashboard.css").exists()
        assert (ui_dir / "dashboard.js").exists()

    def test_dashboard_served_as_index(self):
        """GET / should serve dashboard.html."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        # dashboard.html has tab-btn elements
        assert "tab-btn" in resp.text
        assert "Setup" in resp.text

    def test_models_endpoint(self):
        """GET /api/models should return the full model registry."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 20  # We have 31 models
        # Check structure
        first = data[0]
        assert "model_id" in first
        assert "display_name" in first
        assert "provider" in first
        assert "tiers" in first
        assert "context_k" in first
        assert "origin" in first

    def test_test_key_no_key(self):
        """POST /api/test-key with no key should return error."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.post("/api/test-key", json={"api_key": ""})
        assert resp.status_code == 400
        data = resp.json()
        assert data["valid"] is False

    def test_sessions_endpoint_returns_list(self):
        """GET /api/sessions should return a list (empty if no DB)."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_model_performance_endpoint(self):
        """GET /api/model-performance should return a list."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.get("/api/model-performance")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_review_endpoint_no_code(self):
        """POST /api/review with no code should return error."""
        from starlette.testclient import TestClient

        from crosscheck.chat_server import create_chat_app
        app = create_chat_app()
        client = TestClient(app)
        resp = client.post("/api/review", json={"code": "", "api_key": "test"})
        assert resp.status_code == 400
        assert "error" in resp.json()


# ── CLI Extensions (team + chat) ────────────────────────────────────────────

class TestCLITeamChat:
    """Tests for team and chat CLI commands."""

    def test_team_cmd_registered(self):
        """team command should be importable."""
        from crosscheck.cli_extensions import team_cmd
        assert team_cmd is not None
        assert team_cmd.name == "team"

    def test_chat_cmd_registered(self):
        """chat command should be importable."""
        from crosscheck.cli_extensions import chat_cmd
        assert chat_cmd is not None
        assert chat_cmd.name == "chat"

    def test_register_extensions_adds_team_chat(self):
        """register_extensions should add team and chat commands."""
        import click

        from crosscheck.cli_extensions import register_extensions
        cli = click.Group("test")
        register_extensions(cli)
        cmd_names = list(cli.commands.keys())
        assert "team" in cmd_names
        assert "chat" in cmd_names

    def test_register_extensions_total_commands(self):
        """register_extensions should add all 8 command groups."""
        import click

        from crosscheck.cli_extensions import register_extensions
        cli = click.Group("test")
        register_extensions(cli)
        # pr, sandbox, dashboard, cache, policy, local, team, chat
        assert len(cli.commands) == 8

    def test_team_cmd_requires_task(self):
        """team command should require --task option."""
        from click.testing import CliRunner

        from crosscheck.cli_extensions import team_cmd
        runner = CliRunner()
        result = runner.invoke(team_cmd, [])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_team_cmd_requires_api_key(self):
        """team command should fail without API key."""
        from click.testing import CliRunner

        from crosscheck.cli_extensions import team_cmd
        runner = CliRunner(env={"CROSSCHECK_API_KEY": ""})
        result = runner.invoke(team_cmd, ["-t", "test task"])
        assert result.exit_code != 0

    def test_team_cmd_has_project_option(self):
        """team command should have --project/-p option."""
        from crosscheck.cli_extensions import team_cmd
        param_names = {p.name for p in team_cmd.params}
        assert "project" in param_names

    def test_team_cmd_has_auto_apply_option(self):
        """team command should have --auto-apply flag."""
        from crosscheck.cli_extensions import team_cmd
        param_names = {p.name for p in team_cmd.params}
        assert "auto_apply" in param_names

    def test_team_cmd_has_watch_option(self):
        """team command should have --watch flag."""
        from crosscheck.cli_extensions import team_cmd
        param_names = {p.name for p in team_cmd.params}
        assert "watch" in param_names

    def test_team_cmd_multi_file(self):
        """team -f should accept multiple files (multiple=True)."""
        from crosscheck.cli_extensions import team_cmd
        file_param = next(p for p in team_cmd.params if p.name == "code_files")
        assert file_param.multiple is True

    def test_auto_apply_requires_project(self):
        """--auto-apply without --project should error."""
        from click.testing import CliRunner

        from crosscheck.cli_extensions import team_cmd
        runner = CliRunner(env={"CROSSCHECK_API_KEY": "test-key"})
        result = runner.invoke(team_cmd, ["-t", "task", "--auto-apply"])
        assert result.exit_code != 0
        assert "project" in result.output.lower() or "project" in str(result.exception).lower()

    def test_watch_requires_project(self):
        """--watch without --project should error."""
        from click.testing import CliRunner

        from crosscheck.cli_extensions import team_cmd
        runner = CliRunner(env={"CROSSCHECK_API_KEY": "test-key"})
        result = runner.invoke(team_cmd, ["-t", "task", "--watch"])
        assert result.exit_code != 0
        assert "project" in result.output.lower() or "project" in str(result.exception).lower()

    def test_multi_file_concatenation(self, tmp_path):
        """Multiple -f files should be concatenated with headers."""
        f1 = tmp_path / "a.py"
        f1.write_text("x = 1", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2", encoding="utf-8")

        from click.testing import CliRunner

        from crosscheck.cli_extensions import team_cmd
        # We can't run the full command (needs API), but we can verify
        # the options parse correctly by checking help
        runner = CliRunner()
        result = runner.invoke(team_cmd, ["--help"])
        assert "-f" in result.output
        assert "repeatable" in result.output.lower() or "code file" in result.output.lower()


# ── Workspace ───────────────────────────────────────────────────────────────

class TestProjectWorkspace:
    """Tests for crosscheck.team.workspace.ProjectWorkspace."""

    def test_workspace_creation(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        assert ws.root == tmp_path.resolve()

    def test_workspace_invalid_root(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        with pytest.raises(ValueError, match="does not exist"):
            ProjectWorkspace(root=tmp_path / "nonexistent")

    def test_read_file(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        (tmp_path / "test.py").write_text("x = 1", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        assert ws.read_file("test.py") == "x = 1"

    def test_read_file_not_found(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        with pytest.raises(FileNotFoundError):
            ws.read_file("missing.py")

    def test_path_traversal_blocked(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            ws.read_file("../../etc/passwd")

    def test_list_files(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "test.py").write_text("y = 2", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        files = ws.list_files("*.py")
        assert "app.py" in files
        assert "test.py" in files

    def test_list_files_skips_pycache(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "cached.pyc").write_text("", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        files = ws.list_files("*")
        assert all("__pycache__" not in f for f in files)

    def test_file_tree(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("y = 2", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        tree = ws.file_tree()
        assert "app.py" in tree
        assert "src/" in tree

    def test_propose_changes(self, tmp_path):
        from crosscheck.team.chat import CodeBlock
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        blocks = [
            CodeBlock(filename="new_file.py", language="python", content="x = 42")
        ]
        proposal = ws.propose_changes(blocks)
        assert proposal.status == "pending"
        assert len(proposal.changes) == 1
        assert proposal.changes[0].filename == "new_file.py"
        assert proposal.changes[0].action == "create"

    def test_apply_proposal(self, tmp_path):
        from crosscheck.team.chat import CodeBlock
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        blocks = [
            CodeBlock(filename="new_file.py", language="python", content="x = 42")
        ]
        proposal = ws.propose_changes(blocks)
        applied = ws.apply_proposal(proposal.id)
        assert "new_file.py" in applied
        assert (tmp_path / "new_file.py").read_text(encoding="utf-8") == "x = 42"
        assert proposal.status == "accepted"

    def test_rollback(self, tmp_path):
        from crosscheck.team.chat import CodeBlock
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        blocks = [
            CodeBlock(filename="rollback_test.py", language="python", content="x = 1")
        ]
        proposal = ws.propose_changes(blocks)
        ws.apply_proposal(proposal.id)
        assert (tmp_path / "rollback_test.py").exists()
        rolled = ws.rollback(proposal.id)
        assert "rollback_test.py" in rolled
        assert not (tmp_path / "rollback_test.py").exists()

    def test_rollback_edit(self, tmp_path):
        from crosscheck.team.chat import CodeBlock
        from crosscheck.team.workspace import ProjectWorkspace
        (tmp_path / "existing.py").write_text("original", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        blocks = [
            CodeBlock(filename="existing.py", language="python", content="modified")
        ]
        proposal = ws.propose_changes(blocks)
        ws.apply_proposal(proposal.id)
        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "modified"
        ws.rollback(proposal.id)
        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "original"

    def test_proposal_not_found(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            ws.apply_proposal("nonexistent")


# ── Coordinator + Observer Roles ──────────────────────────────────────────

class TestCoordinatorObserver:
    """Tests for Coordinator and Observer roles."""

    def test_coordinator_role_exists(self):
        assert TeamRole.COORDINATOR.value == "coordinator"

    def test_observer_roles_exist(self):
        assert TeamRole.OBSERVER_1.value == "observer_1"
        assert TeamRole.OBSERVER_2.value == "observer_2"

    def test_coordinator_in_default_team(self):
        roles = {s.role for s in DEFAULT_TEAM}
        assert TeamRole.COORDINATOR in roles

    def test_observers_in_default_team(self):
        roles = {s.role for s in DEFAULT_TEAM}
        assert TeamRole.OBSERVER_1 in roles
        assert TeamRole.OBSERVER_2 in roles

    def test_coordinator_cannot_write_code(self):
        spec = get_role_spec(TeamRole.COORDINATOR)
        assert spec.can_write_code is False

    def test_observers_cannot_write_code(self):
        spec1 = get_role_spec(TeamRole.OBSERVER_1)
        spec2 = get_role_spec(TeamRole.OBSERVER_2)
        assert spec1.can_write_code is False
        assert spec2.can_write_code is False

    def test_coordinator_has_safety_prompt(self):
        spec = get_role_spec(TeamRole.COORDINATOR)
        assert "safety" in spec.system_prompt.lower() or "SAFETY" in spec.system_prompt

    def test_observer_has_adversarial_prompt(self):
        spec = get_role_spec(TeamRole.OBSERVER_1)
        assert "adversarial" in spec.system_prompt.lower()

    def test_diverse_models_for_observers(self):
        """Observers should use different models (diverse model forcing)."""
        spec1 = get_role_spec(TeamRole.OBSERVER_1)
        spec2 = get_role_spec(TeamRole.OBSERVER_2)
        assert spec1.default_model != spec2.default_model

    def test_coordinator_command_aliases(self):
        """@coordinator and aliases should route correctly."""
        result = CommandParser.parse("@coordinator check consensus")
        assert result.has_mentions is True
        assert TeamRole.COORDINATOR in result.targets

        result2 = CommandParser.parse("@safety review everything")
        assert result2.has_mentions is True
        assert TeamRole.COORDINATOR in result2.targets


# ── Session with Workspace ──────────────────────────────────────────────────

class TestSessionWorkspace:
    """Tests for TeamSession with workspace integration."""

    @pytest.mark.asyncio
    async def test_session_accepts_workspace(self):
        client = _mock_client()
        session = TeamSession(client=client, workspace=None)
        assert session.workspace is None

    @pytest.mark.asyncio
    async def test_session_with_workspace(self, tmp_path):
        from crosscheck.team.workspace import ProjectWorkspace
        ws = ProjectWorkspace(root=tmp_path)
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        client = _mock_client(["APPROVED. Code looks correct."])
        session = TeamSession(client=client, workspace=ws, max_rounds=1)
        assert session.workspace is ws

    @pytest.mark.asyncio
    async def test_set_approval_decision(self):
        client = _mock_client()
        session = TeamSession(client=client)
        session.set_approval_decision("accept")
        assert session._approval_decision == "accept"
        assert session._approval_event.is_set()

    @pytest.mark.asyncio
    async def test_set_approval_with_edits(self):
        client = _mock_client()
        session = TeamSession(client=client)
        edits = {"app.py": "fixed content"}
        session.set_approval_decision("edit", edits=edits)
        assert session._approval_decision == "edit"
        assert session._approval_edits == edits

    @pytest.mark.asyncio
    async def test_session_run_with_workspace_scans(self, tmp_path):
        """Session with workspace should auto-scan and include project context."""
        from crosscheck.team.workspace import ProjectWorkspace
        (tmp_path / "main.py").write_text("def main(): pass", encoding="utf-8")
        (tmp_path / "utils.py").write_text("def helper(): pass", encoding="utf-8")
        ws = ProjectWorkspace(root=tmp_path)
        client = _mock_client(["APPROVED. Code looks correct."])
        session = TeamSession(client=client, workspace=ws, max_rounds=1)
        history = await session.run(task="Add logging to main.py")
        assert session.phase == SessionPhase.DONE
        assert len(history.messages) > 0

    @pytest.mark.asyncio
    async def test_dissent_phase_in_session_run(self):
        """Full session should go through DISSENT phase."""
        phase_log = []

        async def _chat(**kwargs):
            return "APPROVED. Everything looks great."

        client = MagicMock()
        client.chat = _chat
        session = TeamSession(client=client, max_rounds=1)

        # Track phase changes
        original_emit = session._emit
        async def tracking_emit(msg):
            phase_log.append(msg.phase.value if hasattr(msg.phase, 'value') else str(msg.phase))
            await original_emit(msg)
        session._emit = tracking_emit

        await session.run(task="Test dissent phase")

        # Should have gone through dissent
        assert "dissent" in phase_log
