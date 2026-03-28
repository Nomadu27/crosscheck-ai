"""
tests/test_core.py
------------------
Unit tests for MultiAgentSession with mocked OpenRouter calls.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crosscheck import MultiAgentSession, Task, Mode, SessionResult


MOCK_DECOMPOSE = json.dumps({
    "session_goal": "Review the code for correctness",
    "analyzer_tasks": [
        {"id": 1, "angle": "Security", "instruction": "Check for vulnerabilities"},
        {"id": 2, "angle": "Logic",    "instruction": "Check for bugs"},
        {"id": 3, "angle": "Performance", "instruction": "Check for inefficiencies"},
        {"id": 4, "angle": "Style",    "instruction": "Check code style"},
    ]
})

MOCK_ANALYSIS = json.dumps({
    "angle": "Security",
    "score": 6,
    "verdict": "ISSUES_FOUND",
    "issues": [{"severity": "major", "location": "line 1", "description": "Division by zero", "fix": "Add zero check"}],
    "positive_findings": ["Clean structure"],
    "summary": "Found division by zero risk",
})

MOCK_SYNTHESIS_REVISE = json.dumps({
    "verdict": "REVISE",
    "overall_score": 6.0,
    "summary": "Code has a critical bug",
    "critical_issues": ["Division by zero at line 1"],
    "coder_instructions": "Add a zero check before division",
    "approved_aspects": ["Clean structure"],
})

MOCK_SYNTHESIS_APPROVED = json.dumps({
    "verdict": "APPROVED",
    "overall_score": 9.0,
    "summary": "Code is now correct",
    "critical_issues": [],
    "coder_instructions": "",
    "approved_aspects": ["Clean structure", "Correct logic"],
})

MOCK_REVISED_CODE = "def foo(x):\n    if x == 0:\n        return 0\n    return 1 / x"


@pytest.fixture
def mock_client_chat():
    """Mock OpenRouterClient.chat to return canned responses.
    Sequence (monitor=False, no tiebreaker):
      Round 1: decompose, 4×analysis, synthesize→REVISE, coder
      Round 2: decompose, 4×analysis, synthesize→APPROVED
    """
    call_count = {"n": 0}
    responses = [
        MOCK_DECOMPOSE,          # 0  supervisor decompose round 1
        MOCK_ANALYSIS,           # 1  analyzer 1
        MOCK_ANALYSIS,           # 2  analyzer 2
        MOCK_ANALYSIS,           # 3  analyzer 3
        MOCK_ANALYSIS,           # 4  analyzer 4
        MOCK_SYNTHESIS_REVISE,   # 5  supervisor synthesize → REVISE
        MOCK_REVISED_CODE,       # 6  coder output (raw text, not JSON)
        MOCK_DECOMPOSE,          # 7  supervisor decompose round 2
        MOCK_ANALYSIS,           # 8  analyzer 1 round 2
        MOCK_ANALYSIS,           # 9  analyzer 2 round 2
        MOCK_ANALYSIS,           # 10 analyzer 3 round 2
        MOCK_ANALYSIS,           # 11 analyzer 4 round 2
        MOCK_SYNTHESIS_APPROVED, # 12 supervisor synthesize → APPROVED
    ]

    async def fake_chat(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(responses):
            return responses[idx]
        return MOCK_SYNTHESIS_APPROVED

    return fake_chat


@pytest.mark.asyncio
async def test_session_approves_after_revision(mock_client_chat):
    """Session should run 2 rounds and return APPROVED."""
    with patch("crosscheck.client.OpenRouterClient.chat", new=mock_client_chat):
        session = MultiAgentSession(
            api_key    = "test-key",
            review_type= Task.CODE,
            mode       = Mode.BALANCED,
            max_rounds = 5,
            monitor    = False,
        )
        result = await session.run_async("def foo(): return 1/0")

    assert result.verdict == "APPROVED"
    assert result.total_rounds == 2
    assert result.final_score >= 8.0


@pytest.mark.asyncio
async def test_session_stops_at_max_rounds(mock_client_chat):
    """Session should stop at max_rounds even without APPROVED.
    Mode.FAST has 3 analyzers so each round = decompose + 3×analysis + synthesize.
    """
    # Round 1: decompose, 3×analysis, synthesize→REVISE, coder
    # Round 2: decompose, 3×analysis, synthesize→REVISE (max_rounds hit)
    responses = [
        MOCK_DECOMPOSE,   MOCK_ANALYSIS, MOCK_ANALYSIS, MOCK_ANALYSIS,
        MOCK_SYNTHESIS_REVISE, MOCK_REVISED_CODE,
        MOCK_DECOMPOSE,   MOCK_ANALYSIS, MOCK_ANALYSIS, MOCK_ANALYSIS,
        MOCK_SYNTHESIS_REVISE,
    ]

    with patch("crosscheck.client.OpenRouterClient.chat") as mock:
        mock.side_effect = responses

        session = MultiAgentSession(
            api_key    = "test-key",
            review_type= Task.CODE,
            mode       = Mode.FAST,
            max_rounds = 2,
            monitor    = False,
        )
        result = await session.run_async("bad code")

    assert result.verdict == "MAX_ROUNDS_REACHED"
    assert result.total_rounds == 2


@pytest.mark.asyncio
async def test_session_callbacks_called(mock_client_chat):
    """All callbacks should be invoked at least once."""
    calls = {
        "round_start": 0,
        "analyzer": 0,
        "verdict": 0,
        "round_complete": 0,
    }

    with patch("crosscheck.client.OpenRouterClient.chat", new=mock_client_chat):
        session = MultiAgentSession(
            api_key    = "test-key",
            review_type= Task.CODE,
            mode       = Mode.BALANCED,
            max_rounds = 5,
            monitor    = False,
            on_round_start          = lambda _: calls.update({"round_start": calls["round_start"] + 1}),
            on_analyzer_complete    = lambda _: calls.update({"analyzer":    calls["analyzer"]    + 1}),
            on_supervisor_verdict   = lambda _: calls.update({"verdict":     calls["verdict"]     + 1}),
            on_round_complete       = lambda _: calls.update({"round_complete": calls["round_complete"] + 1}),
        )
        await session.run_async("def foo(): return 1/0")

    assert calls["round_start"]    >= 1
    assert calls["analyzer"]       >= 1
    assert calls["verdict"]        >= 1
    assert calls["round_complete"] >= 1


def test_session_run_sync(mock_client_chat):
    """Sync .run() wrapper should work correctly."""
    with patch("crosscheck.client.OpenRouterClient.chat", new=mock_client_chat):
        session = MultiAgentSession(
            api_key="test-key", review_type=Task.CODE, mode=Mode.BALANCED,
            max_rounds=5, monitor=False,
        )
        result = session.run("def foo(): return 1/0")
    assert isinstance(result, SessionResult)


def test_model_config_uses_preset():
    """Mode preset should be reflected in model_config."""
    session = MultiAgentSession(api_key="x", mode=Mode.QUALITY)
    cfg = session.model_config
    assert "claude-opus" in cfg["coder"]
    assert len(cfg["supervisors"]) >= 1
    assert len(cfg["analyzers"]) >= 3


def test_model_config_custom_override():
    """Explicit model overrides should take priority over mode preset."""
    session = MultiAgentSession(
        api_key    = "x",
        mode       = Mode.FAST,
        supervisors= ["openai/gpt-4.1"],
        coder      = "anthropic/claude-sonnet-4.6",
    )
    cfg = session.model_config
    assert cfg["supervisors"] == ["openai/gpt-4.1"]
    assert cfg["coder"] == "anthropic/claude-sonnet-4.6"
