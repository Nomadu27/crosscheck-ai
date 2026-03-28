"""
tests/test_team.py
------------------
Tests for the crosscheck.team package (v2.0.0 AI Dev Team).

Covers: roles, chat messages, command parser, language detection,
and TeamSession orchestration. All mocked — no API key needed.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from crosscheck.team.roles import (
    TeamRole, RoleSpec, DEFAULT_TEAM, get_role_spec, build_team,
)
from crosscheck.team.chat import (
    SessionPhase, TeamMessage, CodeBlock, ChatHistory, extract_code_blocks,
)
from crosscheck.team.command_parser import CommandParser, ParseResult
from crosscheck.team.language import LanguageDetector
from crosscheck.team.session import TeamSession, ReviewResult


# ── Roles ────────────────────────────────────────────────────────────────────

class TestTeamRole:

    def test_all_roles_defined(self):
        """TeamRole enum has 7 values (6 agents + HUMAN)."""
        assert len(TeamRole) == 7
        assert TeamRole.HUMAN in TeamRole

    def test_role_values_are_strings(self):
        assert TeamRole.PLANNER.value == "planner"
        assert TeamRole.CODER.value == "coder"

    def test_default_team_has_six_agents(self):
        """DEFAULT_TEAM has 6 RoleSpecs (no HUMAN)."""
        assert len(DEFAULT_TEAM) == 6
        roles = {s.role for s in DEFAULT_TEAM}
        assert TeamRole.HUMAN not in roles

    def test_default_team_all_roles_covered(self):
        """Every non-HUMAN role has a default spec."""
        roles = {s.role for s in DEFAULT_TEAM}
        expected = {TeamRole.PLANNER, TeamRole.ARCHITECT, TeamRole.CODER,
                    TeamRole.DEBUGGER, TeamRole.SECURITY, TeamRole.ANALYST}
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
        assert len(team) == 6
        assert team[0].default_model == DEFAULT_TEAM[0].default_model

    def test_build_team_with_override(self):
        team = build_team({TeamRole.CODER: "openai/gpt-5"})
        coder = next(s for s in team if s.role == TeamRole.CODER)
        assert coder.default_model == "openai/gpt-5"
        # Other roles unchanged
        planner = next(s for s in team if s.role == TeamRole.PLANNER)
        assert planner.default_model == DEFAULT_TEAM[0].default_model

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
        assert len(SessionPhase) == 6
        phases = [p.value for p in SessionPhase]
        assert "planning" in phases
        assert "coding" in phases
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
        assert len(session.team) == 6
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

        responses = await session.inject_human_message(
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

        # Should get responses from all 6 team members
        assert len(responses) == 6

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
