"""
tests/test_supervisor.py
------------------------
Tests for SupervisorAgent: single-supervisor, dual-supervisor voting,
reconciliation logic, JSON parsing, error handling.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crosscheck.agents.supervisor import SupervisorAgent, SynthesisResult, DecomposeResult
from crosscheck.models import Task


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_synthesis_raw(verdict: str, score: float) -> str:
    return json.dumps({
        "verdict":            verdict,
        "overall_score":      score,
        "summary":            f"Score {score}: {verdict}",
        "critical_issues":    ["issue A"] if verdict == "REVISE" else [],
        "coder_instructions": "Fix issue A" if verdict == "REVISE" else "",
        "approved_aspects":   ["Clean structure"],
    })


def _make_decompose_raw() -> str:
    return json.dumps({
        "session_goal":   "Review the code",
        "analyzer_tasks": [
            {"id": 1, "angle": "Security",    "instruction": "Check for vulnerabilities"},
            {"id": 2, "angle": "Logic",       "instruction": "Check for logic errors"},
            {"id": 3, "angle": "Performance", "instruction": "Check for bottlenecks"},
            {"id": 4, "angle": "Style",       "instruction": "Check for style issues"},
        ],
    })


def _mock_client(responses: list[str]) -> MagicMock:
    """Create a mock OpenRouterClient that returns responses in order."""
    client = MagicMock()
    client.total_tokens   = 1000
    client.total_cost_usd = 0.01
    call_count = [0]

    async def _chat(**kwargs):
        idx = call_count[0] % len(responses)
        call_count[0] += 1
        return responses[idx]

    client.chat = _chat
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__  = AsyncMock(return_value=False)
    return client


def _make_supervisor(models: list[str], client=None) -> SupervisorAgent:
    if client is None:
        client = _mock_client([_make_synthesis_raw("APPROVED", 9.0)])
    return SupervisorAgent(
        client     = client,
        models     = models,
        task       = Task.CODE,
        max_rounds = 3,
    )


# ── Constructor ───────────────────────────────────────────────────────────────

def test_supervisor_requires_at_least_one_model():
    with pytest.raises(ValueError, match="at least one"):
        SupervisorAgent(
            client=MagicMock(), models=[], task=Task.CODE, max_rounds=3
        )


def test_supervisor_caps_at_two_models():
    client = _mock_client([_make_synthesis_raw("APPROVED", 8.0)])
    sup = SupervisorAgent(
        client     = client,
        models     = ["a", "b", "c", "d"],
        task       = Task.CODE,
        max_rounds = 3,
    )
    assert len(sup.models) == 2


def test_supervisor_accepts_non_anthropic_model():
    """Any model ID is valid — no restrictions."""
    client = _mock_client([_make_synthesis_raw("APPROVED", 9.0)])
    sup = SupervisorAgent(
        client     = client,
        models     = ["x-ai/grok-4", "deepseek/deepseek-r1"],
        task       = Task.CODE,
        max_rounds = 3,
    )
    assert sup.models == ["x-ai/grok-4", "deepseek/deepseek-r1"]


# ── decompose ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decompose_returns_correct_structure():
    client = _mock_client([_make_decompose_raw()])
    sup    = _make_supervisor(["anthropic/claude-opus-4.6"], client)
    result = await sup.decompose("def foo(): pass", round_num=1)

    assert isinstance(result, DecomposeResult)
    assert result.session_goal != ""
    assert isinstance(result.analyzer_tasks, list)
    assert len(result.analyzer_tasks) == 4
    assert result.analyzer_tasks[0]["angle"] == "Security"


@pytest.mark.asyncio
async def test_decompose_uses_primary_model_only():
    """Decompose always uses models[0] — secondary supervisor not involved."""
    call_log = []

    async def _chat(**kwargs):
        call_log.append(kwargs.get("model", "unknown"))
        return _make_decompose_raw()

    client = MagicMock()
    client.chat = _chat
    sup = SupervisorAgent(
        client     = client,
        models     = ["model-A", "model-B"],
        task       = Task.CODE,
        max_rounds = 3,
    )
    await sup.decompose("some code", round_num=1)
    assert call_log == ["model-A"], "Decompose must only call the primary supervisor"


# ── Single supervisor synthesize ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_supervisor_approved():
    client = _mock_client([_make_synthesis_raw("APPROVED", 9.5)])
    sup    = _make_supervisor(["anthropic/claude-opus-4.6"], client)
    result = await sup.synthesize([{"angle": "Security", "score": 9.5}], round_num=1)

    assert result.verdict      == "APPROVED"
    assert result.overall_score == 9.5
    assert result.consensus    is True
    assert result.supervisor_votes == ["APPROVED"]


@pytest.mark.asyncio
async def test_single_supervisor_revise():
    client = _mock_client([_make_synthesis_raw("REVISE", 4.0)])
    sup    = _make_supervisor(["anthropic/claude-opus-4.6"], client)
    result = await sup.synthesize([{"angle": "Logic", "score": 4.0}], round_num=1)

    assert result.verdict      == "REVISE"
    assert result.overall_score == 4.0
    assert len(result.critical_issues) > 0


# ── Dual supervisor voting ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dual_both_approved_unanimous():
    """Both APPROVED → APPROVED, consensus=True."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 9.0),
        _make_synthesis_raw("APPROVED", 8.8),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.verdict         == "APPROVED"
    assert result.consensus       is True
    assert len(result.supervisor_votes) == 2
    assert result.supervisor_votes == ["APPROVED", "APPROVED"]


@pytest.mark.asyncio
async def test_dual_both_revise_unanimous():
    """Both REVISE → REVISE, consensus=True."""
    client = _mock_client([
        _make_synthesis_raw("REVISE", 3.0),
        _make_synthesis_raw("REVISE", 4.0),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.verdict   == "REVISE"
    assert result.consensus is True
    assert result.supervisor_votes == ["REVISE", "REVISE"]


@pytest.mark.asyncio
async def test_dual_split_low_scores_revise_wins():
    """Split vote with low scores → conservative REVISE."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 7.0),
        _make_synthesis_raw("REVISE",   6.5),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.verdict   == "REVISE"
    assert result.consensus is False


@pytest.mark.asyncio
async def test_dual_split_both_high_approved_wins():
    """Split vote but BOTH scores ≥ 8.5 → APPROVED (near-perfect exception)."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 9.2),
        _make_synthesis_raw("REVISE",   8.7),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.verdict   == "APPROVED"
    assert result.consensus is False    # split was recorded


@pytest.mark.asyncio
async def test_dual_split_one_high_one_low_revise_wins():
    """Split: one model ≥ 8.5, other < 8.5 → conservative REVISE."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 9.5),
        _make_synthesis_raw("REVISE",   6.0),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.verdict   == "REVISE"
    assert result.consensus is False


@pytest.mark.asyncio
async def test_dual_average_score_computed():
    """Overall score should be the average of both supervisors."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 8.0),
        _make_synthesis_raw("APPROVED", 9.0),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert result.overall_score == 8.5


@pytest.mark.asyncio
async def test_dual_critical_issues_merged():
    """Critical issues from both supervisors must be merged (deduplicated)."""
    raw1 = json.dumps({
        "verdict": "REVISE", "overall_score": 5.0,
        "summary": "Issues", "approved_aspects": [],
        "critical_issues":    ["Bug in line 5", "Missing null check"],
        "coder_instructions": "Fix bugs",
    })
    raw2 = json.dumps({
        "verdict": "REVISE", "overall_score": 4.0,
        "summary": "More issues", "approved_aspects": [],
        "critical_issues":    ["Missing null check", "SQL injection risk"],
        "coder_instructions": "Add validation",
    })
    client = _mock_client([raw1, raw2])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    # Should contain all 3 unique issues
    assert "Bug in line 5"       in result.critical_issues
    assert "Missing null check"  in result.critical_issues
    assert "SQL injection risk"  in result.critical_issues
    # No duplicates
    assert len(result.critical_issues) == 3


@pytest.mark.asyncio
async def test_dual_coder_instructions_merged_on_revise():
    """When both say REVISE, coder instructions from both must be combined."""
    raw1 = json.dumps({
        "verdict": "REVISE", "overall_score": 5.0,
        "summary": "", "critical_issues": [], "approved_aspects": [],
        "coder_instructions": "Fix the null pointer.",
    })
    raw2 = json.dumps({
        "verdict": "REVISE", "overall_score": 4.5,
        "summary": "", "critical_issues": [], "approved_aspects": [],
        "coder_instructions": "Add error handling.",
    })
    client = _mock_client([raw1, raw2])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert "Fix the null pointer." in result.coder_instructions
    assert "Add error handling."   in result.coder_instructions


@pytest.mark.asyncio
async def test_dual_supervisor_votes_recorded():
    """supervisor_votes must always have 2 entries for dual supervisors."""
    client = _mock_client([
        _make_synthesis_raw("APPROVED", 9.0),
        _make_synthesis_raw("REVISE",   7.0),
    ])
    sup    = _make_supervisor(["model-A", "model-B"], client)
    result = await sup.synthesize([{}], round_num=1)

    assert len(result.supervisor_votes) == 2
    assert "APPROVED" in result.supervisor_votes
    assert "REVISE"   in result.supervisor_votes


# ── JSON parsing ──────────────────────────────────────────────────────────────

def test_parse_json_clean():
    raw    = '{"verdict": "APPROVED", "overall_score": 9.0, "summary": "ok", "critical_issues": [], "coder_instructions": "", "approved_aspects": []}'
    parsed = SupervisorAgent._parse_json(raw, "test")
    assert parsed["verdict"] == "APPROVED"


def test_parse_json_strips_markdown_fences():
    raw    = "```json\n{\"verdict\": \"REVISE\", \"overall_score\": 5.0, \"summary\": \"\", \"critical_issues\": [], \"coder_instructions\": \"\", \"approved_aspects\": []}\n```"
    parsed = SupervisorAgent._parse_json(raw, "test")
    assert parsed["verdict"] == "REVISE"


def test_parse_json_invalid_raises_valueerror():
    with pytest.raises(ValueError, match="JSON parse failed"):
        SupervisorAgent._parse_json("this is not json at all", "test")


# ── _reconcile edge cases ─────────────────────────────────────────────────────

def test_reconcile_approved_aspects_deduped():
    r1 = SynthesisResult(
        verdict="APPROVED", overall_score=9.0, summary="",
        critical_issues=[], coder_instructions="",
        approved_aspects=["Clean code", "Good tests"],
    )
    r2 = SynthesisResult(
        verdict="APPROVED", overall_score=8.5, summary="",
        critical_issues=[], coder_instructions="",
        approved_aspects=["Good tests", "Strong typing"],
    )
    merged = SupervisorAgent._reconcile(r1, r2)
    seen   = set()
    for a in merged.approved_aspects:
        assert a not in seen, f"Duplicate: {a}"
        seen.add(a)


def test_reconcile_primary_is_higher_scorer():
    """The higher-scoring supervisor's summary should be used."""
    r1 = SynthesisResult(
        verdict="APPROVED", overall_score=9.5, summary="r1 summary",
        critical_issues=[], coder_instructions="",
        approved_aspects=[],
    )
    r2 = SynthesisResult(
        verdict="APPROVED", overall_score=7.0, summary="r2 summary",
        critical_issues=[], coder_instructions="",
        approved_aspects=[],
    )
    merged = SupervisorAgent._reconcile(r1, r2)
    assert merged.summary == "r1 summary"
