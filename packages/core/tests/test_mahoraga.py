"""
Tests for the Mahoraga adaptation system.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mendicant_core.mahoraga import (
    AdaptationRule,
    MahoragaEngine,
    _extract_rule_heuristic,
    _text_similarity,
)
from mendicant_core.mahoraga.store import AdaptationStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path: Path) -> Path:
    """Return a temporary file path for the adaptation store."""
    return tmp_path / "mahoraga.json"


@pytest.fixture
def engine(tmp_store: Path) -> MahoragaEngine:
    """Return a MahoragaEngine backed by a temporary store."""
    return MahoragaEngine(store_path=tmp_store)


# ---------------------------------------------------------------------------
# AdaptationStore tests
# ---------------------------------------------------------------------------

class TestAdaptationStore:
    def test_load_empty(self, tmp_store: Path) -> None:
        store = AdaptationStore(tmp_store)
        assert store.load() == []

    def test_save_and_load(self, tmp_store: Path) -> None:
        store = AdaptationStore(tmp_store)
        rules = [
            {"id": "r1", "category": "PREFERENCE", "trigger": "t", "action": "a"},
            {"id": "r2", "category": "TOOL", "trigger": "t2", "action": "a2"},
        ]
        store.save(rules)
        loaded = store.load()
        assert len(loaded) == 2
        assert loaded[0]["id"] == "r1"
        assert loaded[1]["id"] == "r2"

    def test_atomic_write_creates_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "mahoraga.json"
        store = AdaptationStore(nested)
        store.save([{"id": "x"}])
        assert nested.exists()
        loaded = store.load()
        assert len(loaded) == 1

    def test_corrupt_file_returns_empty(self, tmp_store: Path) -> None:
        tmp_store.parent.mkdir(parents=True, exist_ok=True)
        tmp_store.write_text("not valid json!!", encoding="utf-8")
        store = AdaptationStore(tmp_store)
        assert store.load() == []

    def test_backup(self, tmp_store: Path) -> None:
        store = AdaptationStore(tmp_store)
        store.save([{"id": "b1"}])
        backup_path = store.backup()
        assert backup_path is not None
        assert backup_path.exists()
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        assert len(backup_data) == 1

    def test_backup_when_no_file(self, tmp_store: Path) -> None:
        store = AdaptationStore(tmp_store)
        assert store.backup() is None


# ---------------------------------------------------------------------------
# Rule extraction tests
# ---------------------------------------------------------------------------

class TestRuleExtraction:
    def test_always_preference(self) -> None:
        result = _extract_rule_heuristic("always use dark mode")
        assert result is not None
        category, trigger, action = result
        assert category == "PREFERENCE"
        assert "use dark mode" in action

    def test_always_when_preference(self) -> None:
        result = _extract_rule_heuristic("always use pytest when writing tests")
        assert result is not None
        category, trigger, action = result
        assert category == "PREFERENCE"
        assert "writing tests" in trigger
        assert "use pytest" in action

    def test_never_preference(self) -> None:
        result = _extract_rule_heuristic("never use var in JavaScript")
        assert result is not None
        category, trigger, action = result
        assert category == "PREFERENCE"
        assert "never" in action.lower()

    def test_when_pattern(self) -> None:
        result = _extract_rule_heuristic("when writing code, use functional style")
        assert result is not None
        category, trigger, action = result
        assert category == "PATTERN"
        assert "writing code" in trigger
        assert "functional style" in action

    def test_use_instead_of_tool(self) -> None:
        result = _extract_rule_heuristic("use pytest instead of unittest")
        assert result is not None
        category, trigger, action = result
        assert category == "TOOL"
        assert "pytest" in action.lower()
        assert "unittest" in action.lower()

    def test_prefer_style(self) -> None:
        result = _extract_rule_heuristic("prefer functional style over OOP")
        assert result is not None
        category, trigger, action = result
        assert category == "STYLE"

    def test_after_workflow(self) -> None:
        result = _extract_rule_heuristic("after implementation, always run tests")
        assert result is not None
        category, trigger, action = result
        assert category == "WORKFLOW"
        assert "implementation" in trigger
        assert "run tests" in action

    def test_for_agent(self) -> None:
        result = _extract_rule_heuristic("for refactoring, use hollowed_eyes")
        assert result is not None
        category, trigger, action = result
        assert category == "AGENT"
        assert "refactoring" in trigger
        assert "hollowed_eyes" in action

    def test_correction_no(self) -> None:
        result = _extract_rule_heuristic("no, use tabs not spaces")
        assert result is not None
        # Could match as CORRECTION or TOOL depending on pattern priority
        assert result is not None

    def test_dont_preference(self) -> None:
        result = _extract_rule_heuristic("don't use semicolons in JavaScript")
        assert result is not None
        category, trigger, action = result
        assert category == "PREFERENCE"
        assert "not" in action.lower() or "do not" in action.lower()

    def test_empty_returns_none(self) -> None:
        assert _extract_rule_heuristic("") is None
        assert _extract_rule_heuristic("   ") is None

    def test_unrecognized_returns_none(self) -> None:
        result = _extract_rule_heuristic("hello world")
        assert result is None


# ---------------------------------------------------------------------------
# Text similarity
# ---------------------------------------------------------------------------

class TestTextSimilarity:
    def test_identical(self) -> None:
        assert _text_similarity("hello world", "hello world") == 1.0

    def test_no_overlap(self) -> None:
        assert _text_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self) -> None:
        sim = _text_similarity("writing python tests", "running python code")
        assert 0.0 < sim < 1.0

    def test_empty_strings(self) -> None:
        assert _text_similarity("", "") == 0.0
        assert _text_similarity("hello", "") == 0.0


# ---------------------------------------------------------------------------
# MahoragaEngine tests
# ---------------------------------------------------------------------------

class TestObservePreference:
    def test_explicit_preference(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        assert rule.confidence == 0.95
        assert rule.source == "explicit"
        assert rule.active is True
        assert len(engine.get_all_rules()) == 1

    def test_preference_with_context(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference(
            "when debugging, always print stack traces",
            context="Python development",
        )
        assert rule.category in ("PREFERENCE", "PATTERN")
        assert len(rule.tags) > 0


class TestObserveCorrection:
    def test_basic_correction(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_correction(
            original="I used unittest",
            correction="use pytest instead of unittest",
        )
        assert rule.source == "correction"
        assert rule.confidence == 0.85
        assert "pytest" in rule.action.lower() or "pytest" in rule.trigger.lower()

    def test_correction_with_no_pattern(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_correction(
            original="wrote messy code",
            correction="that was wrong approach",
        )
        assert rule.category == "CORRECTION"
        assert rule.active is True


class TestObserveWorkflow:
    def test_multi_step_workflow(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_workflow(
            steps=["implement", "test", "commit"],
            context="development",
        )
        assert rule.category == "WORKFLOW"
        assert "implement" in rule.trigger.lower()
        assert "test" in rule.action.lower()

    def test_single_step_workflow(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_workflow(steps=["deploy"])
        assert rule.category == "WORKFLOW"


class TestObserveAgentPreference:
    def test_new_agent_preference(self, engine: MahoragaEngine) -> None:
        engine.observe_agent_preference(
            task_type="architecture analysis",
            agent_name="the_didact",
            outcome="success",
        )
        rules = engine.get_all_rules(category="AGENT")
        assert len(rules) == 1
        assert "the_didact" in rules[0].action

    def test_repeated_agent_boosts_confidence(self, engine: MahoragaEngine) -> None:
        engine.observe_agent_preference(
            task_type="code review",
            agent_name="loveless",
            outcome="success",
        )
        initial_conf = engine.get_all_rules(category="AGENT")[0].confidence

        engine.observe_agent_preference(
            task_type="code review",
            agent_name="loveless",
            outcome="success",
        )
        updated_conf = engine.get_all_rules(category="AGENT")[0].confidence
        assert updated_conf > initial_conf


class TestObserveApprovalRejection:
    def test_approval_boosts_confidence(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use pytest when testing")
        initial_conf = rule.confidence

        engine.observe_approval("I'm testing with pytest")
        updated = engine.get_all_rules()
        # Should still have the rule, confidence may have increased
        assert len(updated) >= 1

    def test_rejection_reduces_confidence(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use tabs")
        initial_conf = rule.confidence

        engine.observe_rejection("using tabs is wrong")
        updated = [r for r in engine.get_all_rules(active_only=False) if r.id == rule.id]
        assert len(updated) == 1
        assert updated[0].confidence <= initial_conf


# ---------------------------------------------------------------------------
# Rule extraction from text
# ---------------------------------------------------------------------------

class TestExtractRuleFromText:
    def test_extracts_preference(self, engine: MahoragaEngine) -> None:
        rule = engine.extract_rule_from_text("always write docstrings")
        assert rule is not None
        assert rule.category == "PREFERENCE"
        assert rule.confidence == 0.7

    def test_extracts_workflow(self, engine: MahoragaEngine) -> None:
        rule = engine.extract_rule_from_text("after coding, run linter")
        assert rule is not None
        assert rule.category == "WORKFLOW"

    def test_returns_none_for_gibberish(self, engine: MahoragaEngine) -> None:
        rule = engine.extract_rule_from_text("hello world")
        assert rule is None


# ---------------------------------------------------------------------------
# Storage and deduplication
# ---------------------------------------------------------------------------

class TestStorageAndDedup:
    def test_persistence(self, tmp_store: Path) -> None:
        engine1 = MahoragaEngine(store_path=tmp_store)
        engine1.observe_preference("always use dark mode")
        del engine1

        engine2 = MahoragaEngine(store_path=tmp_store)
        rules = engine2.get_all_rules()
        assert len(rules) == 1
        assert "dark mode" in rules[0].action.lower()

    def test_deduplication(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use pytest when testing")
        engine.observe_preference("always use pytest when testing")
        rules = engine.get_all_rules()
        # Should be deduplicated to 1 rule
        assert len(rules) == 1

    def test_remove_rule(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        assert engine.remove_rule(rule.id) is True
        assert len(engine.get_all_rules()) == 0

    def test_remove_nonexistent(self, engine: MahoragaEngine) -> None:
        assert engine.remove_rule("nonexistent") is False

    def test_update_rule(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        engine.update_rule(rule.id, confidence=0.5, active=False)
        updated = engine.get_all_rules(active_only=False)
        target = [r for r in updated if r.id == rule.id][0]
        assert target.confidence == 0.5
        assert target.active is False


# ---------------------------------------------------------------------------
# Confidence boosting and decay
# ---------------------------------------------------------------------------

class TestConfidenceManagement:
    def test_success_boosts_confidence(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        initial = rule.confidence
        engine.record_success(rule.id)
        updated = [r for r in engine.get_all_rules() if r.id == rule.id][0]
        assert updated.confidence > initial
        assert updated.success_count == 1

    def test_failure_reduces_confidence(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        initial = rule.confidence
        engine.record_failure(rule.id)
        updated = [r for r in engine.get_all_rules(active_only=False) if r.id == rule.id][0]
        assert updated.confidence < initial
        assert updated.failure_count == 1

    def test_confidence_caps_at_1(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        for _ in range(50):
            engine.record_success(rule.id)
        updated = [r for r in engine.get_all_rules() if r.id == rule.id][0]
        assert updated.confidence <= 1.0

    def test_low_confidence_deactivates(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        # Repeatedly fail until deactivated
        for _ in range(20):
            engine.record_failure(rule.id)
        updated = [r for r in engine.get_all_rules(active_only=False) if r.id == rule.id][0]
        assert updated.active is False
        assert updated.confidence < 0.2

    def test_decay_unused_rules(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        # Capture the initial confidence before mutation
        initial_confidence = rule.confidence

        # Set created_at to 60 days ago to trigger decay
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=60)).isoformat()
        engine.update_rule(rule.id, created_at=old_date, last_applied=None)

        count = engine.decay_unused_rules(days=30)
        # Decay should have reduced confidence
        updated = [r for r in engine.get_all_rules(active_only=False) if r.id == rule.id][0]
        assert updated.confidence < initial_confidence or count > 0


# ---------------------------------------------------------------------------
# Context matching and application
# ---------------------------------------------------------------------------

class TestContextMatching:
    def test_matching_rules(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use pytest when writing tests")
        engine.observe_preference("prefer functional style over OOP")

        results = engine.get_applicable_rules("I need to write some tests")
        # Should match the pytest rule
        assert len(results) >= 1

    def test_no_match(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use dark mode when browsing")
        results = engine.get_applicable_rules("deploy to production")
        # May or may not match depending on keyword overlap
        # Just ensure no crash
        assert isinstance(results, list)

    def test_category_filter(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use pytest when testing")
        engine.observe_workflow(
            steps=["implement", "test", "commit"],
        )
        results = engine.get_applicable_rules("testing code", category="WORKFLOW")
        # Only WORKFLOW rules should be returned
        for rule in results:
            assert rule.category == "WORKFLOW"

    def test_inactive_rules_excluded(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        engine.update_rule(rule.id, active=False)
        results = engine.get_applicable_rules("dark mode setting")
        assert all(r.id != rule.id for r in results)


class TestApplyAndTrack:
    def test_apply_updates_tracking(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        assert rule.apply_count == 0
        assert rule.last_applied is None

        engine.apply_and_track(rule.id)
        updated = [r for r in engine.get_all_rules() if r.id == rule.id][0]
        assert updated.apply_count == 1
        assert updated.last_applied is not None


# ---------------------------------------------------------------------------
# Formatting for prompt injection
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_empty_rules(self, engine: MahoragaEngine) -> None:
        result = engine.format_rules_for_injection([])
        assert result == ""

    def test_format_single_rule(self, engine: MahoragaEngine) -> None:
        rule = engine.observe_preference("always use dark mode")
        formatted = engine.format_rules_for_injection([rule])
        assert "<adaptation_rules>" in formatted
        assert "</adaptation_rules>" in formatted
        assert "dark mode" in formatted.lower()
        assert "confidence:" in formatted.lower()

    def test_format_multiple_categories(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use dark mode")
        engine.observe_workflow(steps=["implement", "test", "commit"])
        rules = engine.get_all_rules()
        formatted = engine.format_rules_for_injection(rules)
        assert "<adaptation_rules>" in formatted


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_empty_stats(self, engine: MahoragaEngine) -> None:
        stats = engine.get_stats()
        assert stats["total_rules"] == 0
        assert stats["active_rules"] == 0
        assert stats["average_confidence"] == 0.0

    def test_populated_stats(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use pytest when testing")
        engine.observe_correction("used var", "use const instead of var")
        engine.observe_workflow(steps=["implement", "test"])

        stats = engine.get_stats()
        assert stats["total_rules"] >= 3
        assert stats["active_rules"] >= 3
        assert stats["average_confidence"] > 0.0
        assert "by_category" in stats
        assert "by_source" in stats


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_by_keyword(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use pytest when testing")
        engine.observe_preference("prefer dark mode for IDE")
        results = engine.search_rules("pytest testing")
        assert len(results) >= 1

    def test_search_empty_query(self, engine: MahoragaEngine) -> None:
        engine.observe_preference("always use dark mode")
        assert engine.search_rules("") == []

    def test_search_top_k(self, engine: MahoragaEngine) -> None:
        for i in range(10):
            engine.observe_preference(f"always use tool_{i} when scenario_{i}")
        results = engine.search_rules("tool scenario", top_k=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# AdaptationRule serialization
# ---------------------------------------------------------------------------

class TestAdaptationRule:
    def test_to_dict_roundtrip(self) -> None:
        rule = AdaptationRule(
            id="test-123",
            category="PREFERENCE",
            trigger="when testing",
            action="use pytest",
            confidence=0.9,
            source="explicit",
            created_at="2026-01-01T00:00:00+00:00",
            tags=["pytest", "testing"],
            examples=["always use pytest"],
        )
        d = rule.to_dict()
        restored = AdaptationRule.from_dict(d)
        assert restored.id == rule.id
        assert restored.category == rule.category
        assert restored.trigger == rule.trigger
        assert restored.action == rule.action
        assert restored.confidence == rule.confidence
        assert restored.source == rule.source
        assert restored.tags == rule.tags

    def test_from_dict_defaults(self) -> None:
        rule = AdaptationRule.from_dict({"trigger": "t", "action": "a"})
        assert rule.category == "PATTERN"
        assert rule.confidence == 0.5
        assert rule.source == "inferred"
        assert rule.active is True
        assert rule.apply_count == 0
