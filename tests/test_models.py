"""
tests/test_models.py
--------------------
Tests for the model registry, presets, and helpers.
"""

from crosscheck.models import (
    MODE_PRESETS,
    OBSERVER_DEFAULT_SUPERVISORS,
    REGISTRY,
    TASK_BEST,
    Mode,
    Task,
    Tier,
    get_by_id,
    get_by_origin,
    get_by_tier,
    validate_model_ids,
)

# ── Registry integrity ────────────────────────────────────────────────────────

def test_registry_not_empty():
    assert len(REGISTRY) >= 15

def test_no_duplicate_model_ids():
    ids = [m.model_id for m in REGISTRY]
    assert len(ids) == len(set(ids)), "Duplicate model IDs found"

def test_all_models_have_required_fields():
    for m in REGISTRY:
        assert m.model_id,     "Missing model_id"
        assert m.display_name, f"Missing display_name: {m.model_id}"
        assert m.provider,     f"Missing provider: {m.model_id}"
        assert m.tiers,        f"No tiers: {m.model_id}"
        assert m.tasks,        f"No tasks: {m.model_id}"
        assert m.origin in ("western", "chinese"), f"Bad origin: {m.model_id}"
        assert m.context_k > 0, f"context_k must be > 0: {m.model_id}"

def test_no_is_free_attribute():
    """is_free was removed from the registry in v0.2.0."""
    for m in REGISTRY:
        assert not hasattr(m, "is_free"), f"is_free found on {m.model_id}"


# ── Tier coverage ─────────────────────────────────────────────────────────────

def test_all_tiers_have_models():
    for tier in Tier:
        assert get_by_tier(tier), f"No models for tier: {tier}"

def test_supervisor_tier_has_anthropic():
    ids = [m.model_id for m in get_by_tier(Tier.SUPERVISOR)]
    assert any("anthropic" in mid for mid in ids)

def test_coder_tier_open_to_all_providers():
    """
    v0.2.0 design: ANY capable model can be Coder.
    Coder tier must include non-Anthropic models.
    """
    coders     = get_by_tier(Tier.CODER)
    providers  = {m.provider for m in coders}
    assert len(providers) > 1, \
        "Coder tier should include models from multiple providers"

def test_coder_tier_includes_anthropic():
    coders = get_by_tier(Tier.CODER)
    assert any("anthropic" in m.model_id for m in coders), \
        "Anthropic models should remain in Coder tier (still the recommended default)"

def test_coder_tier_includes_chinese():
    """Chinese models should be available as coders now."""
    coders = get_by_tier(Tier.CODER)
    assert any(m.origin == "chinese" for m in coders), \
        "At least one Chinese model should be tagged as Coder"

def test_analyzer_tier_is_largest():
    assert len(get_by_tier(Tier.ANALYZER)) >= len(get_by_tier(Tier.SUPERVISOR))
    assert len(get_by_tier(Tier.ANALYZER)) >= len(get_by_tier(Tier.CODER))


# ── Origin filtering ──────────────────────────────────────────────────────────

def test_origin_western_not_empty():
    assert len(get_by_origin("western")) >= 5

def test_origin_chinese_not_empty():
    assert len(get_by_origin("chinese")) >= 4

def test_origin_all_returns_full_registry():
    assert len(get_by_origin("all")) == len(REGISTRY)

def test_chinese_models_include_deepseek():
    ids = [m.model_id for m in get_by_origin("chinese")]
    assert any("deepseek" in mid for mid in ids)

def test_chinese_models_include_qwen():
    ids = [m.model_id for m in get_by_origin("chinese")]
    assert any("qwen" in mid for mid in ids)

def test_chinese_models_include_kimi():
    ids = [m.model_id for m in get_by_origin("chinese")]
    assert any("kimi" in mid or "moonshot" in mid for mid in ids)


# ── get_by_id ─────────────────────────────────────────────────────────────────

def test_get_by_id_known():
    m = get_by_id("anthropic/claude-opus-4.6")
    assert m is not None
    assert m.display_name == "Claude Opus 4.6"
    assert Tier.CODER      in m.tiers
    assert Tier.SUPERVISOR in m.tiers

def test_get_by_id_unknown():
    assert get_by_id("not/a-real-model-xyz") is None

def test_get_by_id_grok4():
    m = get_by_id("x-ai/grok-4")
    assert m is not None
    assert Tier.SUPERVISOR in m.tiers
    assert Tier.CODER      in m.tiers   # Grok 4 can also code

def test_get_by_id_deepseek():
    m = get_by_id("deepseek/deepseek-r1")
    assert m is not None
    assert m.origin == "chinese"

def test_get_by_id_kimi():
    m = get_by_id("moonshotai/kimi-k2")
    assert m is not None
    assert Tier.CODER in m.tiers        # Kimi K2 is a valid coder


# ── validate_model_ids ────────────────────────────────────────────────────────

def test_validate_all_known():
    ids = ["anthropic/claude-opus-4.6", "deepseek/deepseek-r1"]
    known, unknown = validate_model_ids(ids)
    assert set(known) == set(ids)
    assert unknown == []

def test_validate_mixed():
    ids = ["anthropic/claude-opus-4.6", "some/fake-model"]
    known, unknown = validate_model_ids(ids)
    assert "anthropic/claude-opus-4.6" in known
    assert "some/fake-model"            in unknown

def test_validate_empty():
    known, unknown = validate_model_ids([])
    assert known   == []
    assert unknown == []


# ── Mode presets ──────────────────────────────────────────────────────────────

def test_mode_presets_complete():
    for mode in Mode:
        p = MODE_PRESETS[mode]
        assert "supervisors" in p
        assert "analyzers"   in p
        assert "coder"       in p
        assert len(p["supervisors"]) >= 1
        assert len(p["analyzers"])   >= 2

def test_quality_mode_dual_supervisor():
    assert len(MODE_PRESETS[Mode.QUALITY]["supervisors"]) == 2

def test_fast_mode_has_fewer_analyzers():
    assert len(MODE_PRESETS[Mode.FAST]["analyzers"]) <= len(MODE_PRESETS[Mode.QUALITY]["analyzers"])

def test_mode_preset_coder_default_is_anthropic():
    """Default coder in presets should still be Anthropic (recommended default)."""
    for mode, preset in MODE_PRESETS.items():
        assert "anthropic" in preset["coder"], \
            f"Default coder for mode {mode.value} should be Anthropic"

def test_mode_preset_all_ids_in_registry():
    for mode, preset in MODE_PRESETS.items():
        all_ids = preset["supervisors"] + preset["analyzers"] + [preset["coder"]]
        for mid in all_ids:
            assert get_by_id(mid) is not None, \
                f"Mode preset {mode.value} references unknown ID: {mid}"


# ── Task-best presets ─────────────────────────────────────────────────────────

def test_task_best_complete():
    for task in Task:
        best = TASK_BEST[task]
        assert "supervisors" in best
        assert "analyzers"   in best
        assert "coder"       in best

def test_task_best_all_ids_in_registry():
    for task, best in TASK_BEST.items():
        all_ids = best["supervisors"] + best["analyzers"] + [best["coder"]]
        for mid in all_ids:
            assert get_by_id(mid) is not None, \
                f"Task best {task.value} references unknown ID: {mid}"


# ── Observer defaults ─────────────────────────────────────────────────────────

def test_observer_default_supervisors_not_empty():
    assert len(OBSERVER_DEFAULT_SUPERVISORS) == 2

def test_observer_default_supervisors_in_registry():
    for mid in OBSERVER_DEFAULT_SUPERVISORS:
        assert get_by_id(mid) is not None, \
            f"Observer default supervisor not in registry: {mid}"


# ── Grok 4 presence, Grok 3 absence ──────────────────────────────────────────

def test_grok4_in_registry():
    assert get_by_id("x-ai/grok-4") is not None

def test_grok4_fast_in_registry():
    assert get_by_id("x-ai/grok-4-fast") is not None

def test_grok41_fast_in_registry():
    assert get_by_id("x-ai/grok-4.1-fast") is not None

def test_no_grok3_in_registry():
    assert get_by_id("x-ai/grok-3")      is None
    assert get_by_id("x-ai/grok-3-mini") is None
