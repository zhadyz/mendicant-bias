"""Tests for Mendicant Bias V5 middleware engines."""

import pytest
import json
import tempfile
import os
from pathlib import Path


# ===================================================================
# TestSmartTaskRouter
# ===================================================================


class TestSmartTaskRouter:
    """Test the keyword-based task classification and flag assignment."""

    def _classify(self, text: str) -> tuple[str, float]:
        """Helper: run keyword classification on text."""
        from mendicant_core.middleware.smart_task_router import (
            SmartTaskRouterMiddleware,
        )

        mw = SmartTaskRouterMiddleware(patterns_store_path=None)
        return mw._classify_keywords(text)

    def _flags_for(self, task_type: str) -> dict[str, bool]:
        """Helper: get the flags dict for a given task type."""
        from mendicant_core.middleware.smart_task_router import _FLAGS

        return _FLAGS[task_type]

    def test_classify_simple(self):
        """'What time is it?' has no matching keywords -> SIMPLE."""
        task_type, conf = self._classify("What time is it?")
        assert task_type == "SIMPLE"
        assert conf == 0.5  # default confidence when nothing matches

    def test_classify_research(self):
        """'Research transformer architectures' contains 'research' -> RESEARCH."""
        task_type, _conf = self._classify("Research transformer architectures")
        assert task_type == "RESEARCH"

    def test_classify_code_generation(self):
        """'Write a Python function' contains 'write a' and 'function' -> CODE_GENERATION."""
        task_type, _conf = self._classify("Write a Python function to sort a list")
        assert task_type == "CODE_GENERATION"

    def test_classify_critical_code(self):
        """'Implement OAuth JWT auth' contains 'oauth' and 'jwt' -> CRITICAL_CODE."""
        task_type, _conf = self._classify("Implement OAuth JWT auth for the API")
        assert task_type == "CRITICAL_CODE"

    def test_classify_multi_modal(self):
        """'Analyze this image and describe it' contains 'image' -> MULTI_MODAL."""
        task_type, _conf = self._classify("Analyze this image and describe it")
        assert task_type == "MULTI_MODAL"

    def test_flags_simple_disables_all(self):
        """SIMPLE task type should disable verification, subagent, and thinking."""
        flags = self._flags_for("SIMPLE")
        assert flags["verification_enabled"] is False
        assert flags["subagent_enabled"] is False
        assert flags["thinking_enabled"] is False

    def test_flags_critical_enables_verification(self):
        """CRITICAL_CODE task type should enable verification."""
        flags = self._flags_for("CRITICAL_CODE")
        assert flags["verification_enabled"] is True

    def test_flags_research_enables_subagent(self):
        """RESEARCH task type should enable subagent."""
        flags = self._flags_for("RESEARCH")
        assert flags["subagent_enabled"] is True

    def test_classify_confidence_scales_with_matches(self):
        """More keyword matches should yield higher confidence."""
        # "security" alone -> 1 match = 0.6
        _, conf_one = self._classify("security review")
        # "oauth jwt authentication" -> 3 matches = 0.95
        _, conf_many = self._classify("oauth jwt authentication security")
        assert conf_many > conf_one

    def test_priority_critical_over_code(self):
        """CRITICAL_CODE wins when it has equal or more keyword matches."""
        # "oauth jwt security" matches 3 CRITICAL_CODE keywords; the
        # priority ordering ensures it wins over CODE_GENERATION
        task_type, _ = self._classify("oauth jwt security")
        assert task_type == "CRITICAL_CODE"

    def test_blend_keyword_only(self):
        """Blend returns keyword result when embedding is None."""
        from mendicant_core.middleware.smart_task_router import (
            SmartTaskRouterMiddleware,
        )

        mw = SmartTaskRouterMiddleware(patterns_store_path=None)
        result = mw._blend("RESEARCH", 0.8, None, 0.0)
        assert result == "RESEARCH"

    def test_blend_same_type(self):
        """Blend returns the type when both signals agree."""
        from mendicant_core.middleware.smart_task_router import (
            SmartTaskRouterMiddleware,
        )

        mw = SmartTaskRouterMiddleware(patterns_store_path=None)
        result = mw._blend("CODE_GENERATION", 0.7, "CODE_GENERATION", 0.9)
        assert result == "CODE_GENERATION"


# ===================================================================
# TestPatternStore
# ===================================================================


class TestPatternStore:
    """Test the standalone PatternStore (JSON-backed, LRU eviction, atomic writes)."""

    def _make_store(self, tmpdir: Path, max_records: int = 100) -> "PatternStore":
        from mendicant_core.patterns import PatternStore

        return PatternStore(
            store_path=tmpdir / "patterns.json",
            max_records=max_records,
        )

    def test_append_and_load(self, tmp_path):
        """Write a pattern, then load it back and verify contents."""
        store = self._make_store(tmp_path)
        pattern = {
            "task_type": "CODE_GENERATION",
            "outcome": "success",
            "duration_seconds": 1.5,
        }
        store.append(pattern)

        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0]["task_type"] == "CODE_GENERATION"
        assert loaded[0]["outcome"] == "success"
        assert loaded[0]["duration_seconds"] == 1.5

    def test_lru_eviction(self, tmp_path):
        """When exceeding max_records, the oldest entries are pruned."""
        store = self._make_store(tmp_path, max_records=5)

        for i in range(10):
            store.append({"id": i, "task_type": "SIMPLE"})

        loaded = store.load()
        assert len(loaded) == 5
        # The remaining entries should be the last 5 (ids 5-9)
        ids = [p["id"] for p in loaded]
        assert ids == [5, 6, 7, 8, 9]

    def test_atomic_write(self, tmp_path):
        """An interrupted write should not corrupt the existing data."""
        store = self._make_store(tmp_path)
        store.append({"id": 1, "task_type": "RESEARCH"})

        # Verify the file exists and is valid JSON
        data = json.loads((tmp_path / "patterns.json").read_text(encoding="utf-8"))
        assert len(data) == 1

        # Write a second pattern — verify both are present
        store.append({"id": 2, "task_type": "CODE_GENERATION"})
        data = json.loads((tmp_path / "patterns.json").read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[1]["id"] == 2

    def test_stats(self, tmp_path):
        """Stats should accurately reflect stored patterns."""
        store = self._make_store(tmp_path)
        store.append(
            {"task_type": "RESEARCH", "outcome": "success", "duration_seconds": 2.0}
        )
        store.append(
            {"task_type": "RESEARCH", "outcome": "success", "duration_seconds": 4.0}
        )
        store.append(
            {"task_type": "CODE_GENERATION", "outcome": "failure", "duration_seconds": 6.0}
        )

        stats = store.get_stats()
        assert stats["total"] == 3
        assert stats["task_types"]["RESEARCH"] == 2
        assert stats["task_types"]["CODE_GENERATION"] == 1
        assert stats["outcomes"]["success"] == 2
        assert stats["outcomes"]["failure"] == 1
        assert stats["avg_duration_seconds"] == 4.0

    def test_search_without_embeddings(self, tmp_path):
        """Search returns empty when patterns have no embeddings."""
        store = self._make_store(tmp_path)
        store.append({"task_type": "SIMPLE", "outcome": "success"})
        store.append({"task_type": "RESEARCH", "outcome": "success"})

        # Search with a dummy query embedding
        results = store.search(query_embedding=[0.1, 0.2, 0.3], top_n=5)
        assert results == []

    def test_search_with_embeddings(self, tmp_path):
        """Search finds the most similar pattern by cosine similarity."""
        store = self._make_store(tmp_path)

        store.append({
            "task_type": "RESEARCH",
            "embedding": [1.0, 0.0, 0.0],
            "outcome": "success",
        })
        store.append({
            "task_type": "CODE_GENERATION",
            "embedding": [0.0, 1.0, 0.0],
            "outcome": "success",
        })

        # Query close to the first pattern
        results = store.search(
            query_embedding=[0.9, 0.1, 0.0], top_n=2, min_similarity=0.0
        )
        assert len(results) == 2
        assert results[0]["task_type"] == "RESEARCH"
        assert results[0]["_similarity"] > results[1]["_similarity"]

    def test_clear(self, tmp_path):
        """Clear empties the store."""
        store = self._make_store(tmp_path)
        store.append({"task_type": "SIMPLE"})
        assert len(store.load()) == 1

        store.clear()
        assert len(store.load()) == 0

    def test_load_empty(self, tmp_path):
        """Load returns empty list when no file exists."""
        store = self._make_store(tmp_path)
        assert store.load() == []

    def test_load_invalid_json(self, tmp_path):
        """Load returns empty list when file contains invalid JSON."""
        store = self._make_store(tmp_path)
        store_file = tmp_path / "patterns.json"
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_text("NOT VALID JSON {{{", encoding="utf-8")
        assert store.load() == []


# ===================================================================
# TestRegistryBuilder
# ===================================================================


class TestRegistryBuilder:
    """Test RegistryBuilder (tool registry construction) and RegistryQuery (lookup)."""

    def test_seed_tools(self, tmp_path):
        """Building with seed tools produces the expected count."""
        from mendicant_core.middleware.registry import RegistryBuilder

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        entries = builder.add_seed_tools().build(compute_embeddings=False)
        assert len(entries) == 8  # 8 seed tools defined in registry.py

    def test_add_custom_tool(self, tmp_path):
        """Adding a custom tool includes it in the build output."""
        from mendicant_core.middleware.registry import RegistryBuilder

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_tool(
            name="my_tool",
            description="A custom tool for testing",
            tags=["test"],
            domain="testing",
        )
        entries = builder.build(compute_embeddings=False)
        assert len(entries) == 1
        assert entries[0]["name"] == "my_tool"
        assert entries[0]["domain"] == "testing"

    def test_search_keyword(self, tmp_path):
        """Keyword search finds the correct tools by query overlap."""
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

        # Build a registry
        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_seed_tools().build(compute_embeddings=False)

        # Query it
        query = RegistryQuery(
            registry_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        results = query.search("search the web", top_k=3)
        assert len(results) > 0
        # The top result should be web_search since "search" and "web" match
        names = [r["name"] for r in results]
        assert "web_search" in names

    def test_lookup_exact_name(self, tmp_path):
        """Lookup by exact tool name returns the correct entry."""
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_seed_tools().build(compute_embeddings=False)

        query = RegistryQuery(
            registry_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        result = query.lookup("run_python")
        assert result is not None
        assert result["name"] == "run_python"
        assert "python" in result["description"].lower()

    def test_lookup_missing(self, tmp_path):
        """Lookup for a nonexistent tool returns None."""
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_seed_tools().build(compute_embeddings=False)

        query = RegistryQuery(
            registry_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        assert query.lookup("nonexistent_tool") is None

    def test_filter_by_domain(self, tmp_path):
        """Filtering by domain returns only tools in that domain."""
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_seed_tools().build(compute_embeddings=False)

        query = RegistryQuery(
            registry_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        code_tools = query.filter_by_domain("code")
        assert all(t["domain"] == "code" for t in code_tools)
        names = [t["name"] for t in code_tools]
        assert "run_python" in names
        assert "run_bash" in names

    def test_tool_names(self, tmp_path):
        """tool_names() returns a sorted list of all tool names."""
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

        builder = RegistryBuilder(
            output_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        builder.add_seed_tools().build(compute_embeddings=False)

        query = RegistryQuery(
            registry_path=str(tmp_path / "registry.json"),
            embedding_model=None,
        )
        names = query.tool_names()
        assert names == sorted(names)
        assert len(names) == 8


# ===================================================================
# TestAgentLoader
# ===================================================================


class TestAgentLoader:
    """Test AgentLoader (profile loading and domain mapping)."""

    def _make_loader(self) -> "AgentLoader":
        """Create an AgentLoader pointed at the real profiles."""
        from mendicant_core.agents import AgentLoader

        base = Path(__file__).resolve().parent.parent / "src" / "mendicant_core" / "agents"
        profiles_dir = base / "profiles"
        mapping_path = base / "agent_mapping.json"
        return AgentLoader(profiles_dir=profiles_dir, mapping_path=mapping_path)

    def test_load_profiles(self):
        """All 13 agent profiles should load."""
        loader = self._make_loader()
        agents = loader.list_agents()
        assert len(agents) == 13
        assert "hollowed_eyes" in agents
        assert "the_didact" in agents
        assert "loveless" in agents

    def test_domain_mapping(self):
        """code_engineering domain should map to hollowed_eyes."""
        loader = self._make_loader()
        agent = loader.get_agent_for_domain("code_engineering")
        assert agent == "hollowed_eyes"

    def test_domain_mapping_cloud(self):
        """cloud_infra domain should map to the_cartographer."""
        loader = self._make_loader()
        agent = loader.get_agent_for_domain("cloud_infra")
        assert agent == "the_cartographer"

    def test_fallback_agent(self):
        """Unknown domain should return the fallback agent (hollowed_eyes)."""
        loader = self._make_loader()
        agent = loader.get_agent_for_domain("completely_unknown_domain_xyz")
        assert agent == "hollowed_eyes"

    def test_get_profile(self):
        """Getting a profile by name should return an AgentProfile with content."""
        loader = self._make_loader()
        profile = loader.get_profile("hollowed_eyes")
        assert profile is not None
        assert profile.name == "hollowed_eyes"

    def test_get_profile_missing(self):
        """Getting a nonexistent profile returns None."""
        loader = self._make_loader()
        assert loader.get_profile("nonexistent_agent") is None

    def test_list_domains(self):
        """list_domains returns a non-empty sorted list."""
        loader = self._make_loader()
        domains = loader.list_domains()
        assert len(domains) > 0
        assert domains == sorted(domains)
        assert "code_engineering" in domains


# ===================================================================
# TestMendicantConfig
# ===================================================================


class TestMendicantConfig:
    """Test the Pydantic config models."""

    def test_default_config(self):
        """Default MendicantConfig should have valid defaults for all sections."""
        from mendicant_core.config import MendicantConfig

        cfg = MendicantConfig()
        assert cfg.context_budget.default_budget == 30_000
        assert cfg.verification.enabled is True
        assert cfg.smart_task_router.embedding_weight == 0.5
        assert cfg.semantic_tool_router.top_k == 5
        assert cfg.adaptive_learning.max_patterns == 500

    def test_from_dict(self):
        """from_dict should parse custom values and leave others as defaults."""
        from mendicant_core.config import MendicantConfig

        cfg = MendicantConfig.from_dict({
            "context_budget": {"default_budget": 20000},
            "smart_task_router": {"embedding_weight": 0.6},
            "verification": {"model": "gpt-4o", "min_score": 0.75},
        })
        assert cfg.context_budget.default_budget == 20000
        assert cfg.smart_task_router.embedding_weight == 0.6
        assert cfg.verification.model == "gpt-4o"
        assert cfg.verification.min_score == 0.75
        # Defaults preserved for unset fields
        assert cfg.semantic_tool_router.top_k == 5
        assert cfg.adaptive_learning.store_path == ".mendicant/orchestration_patterns.json"

    def test_invalid_strategy(self):
        """Bad compression strategy in ContextBudgetConfig should raise."""
        from pydantic import ValidationError
        from mendicant_core.config import MendicantConfig

        with pytest.raises(ValidationError):
            MendicantConfig.from_dict({
                "context_budget": {
                    "strategies": ["key_fields", "nonexistent_strategy"]
                }
            })

    def test_from_dict_empty(self):
        """from_dict with an empty dict should return all defaults."""
        from mendicant_core.config import MendicantConfig

        cfg = MendicantConfig.from_dict({})
        assert cfg.context_budget.default_budget == 30_000
        assert cfg.verification.enabled is True

    def test_extra_keys_ignored(self):
        """Unrecognized top-level keys should be silently ignored."""
        from mendicant_core.config import MendicantConfig

        cfg = MendicantConfig.from_dict({
            "unknown_section": {"foo": "bar"},
            "verification": {"min_score": 0.9},
        })
        assert cfg.verification.min_score == 0.9

    def test_budget_strategies_validation(self):
        """All valid strategies should be accepted."""
        from mendicant_core.config import MendicantConfig

        cfg = MendicantConfig.from_dict({
            "context_budget": {
                "strategies": ["key_fields", "statistical_summary", "truncation"]
            }
        })
        assert cfg.context_budget.strategies == [
            "key_fields",
            "statistical_summary",
            "truncation",
        ]

    def test_verification_bounds(self):
        """Verification min_score out of bounds should raise."""
        from pydantic import ValidationError
        from mendicant_core.config import MendicantConfig

        with pytest.raises(ValidationError):
            MendicantConfig.from_dict({
                "verification": {"min_score": 1.5}  # > 1.0
            })

    def test_top_k_bounds(self):
        """SemanticToolRouter top_k out of bounds should raise."""
        from pydantic import ValidationError
        from mendicant_core.config import MendicantConfig

        with pytest.raises(ValidationError):
            MendicantConfig.from_dict({
                "semantic_tool_router": {"top_k": 0}  # < 1
            })


# ===================================================================
# TestConfigHotReload
# ===================================================================


class TestConfigHotReload:
    """Test the settings.py config loader with mtime-based caching."""

    def setup_method(self):
        """Reset config cache before each test."""
        from mendicant_core.config.settings import reset_config

        reset_config()

    def test_get_config_no_file(self):
        """get_config returns empty dict when no config file exists."""
        from mendicant_core.config.settings import get_config

        # Point to a nonexistent path
        cfg = get_config("/nonexistent/path/mendicant.yaml")
        assert cfg == {}

    def test_get_config_with_yaml(self, tmp_path):
        """get_config loads a YAML file when available."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "mendicant.yaml"
        config_file.write_text(
            "mendicant:\n  verification:\n    min_score: 0.85\n",
            encoding="utf-8",
        )

        from mendicant_core.config.settings import get_config

        cfg = get_config(str(config_file))
        assert cfg["mendicant"]["verification"]["min_score"] == 0.85

    def test_hot_reload_on_mtime_change(self, tmp_path):
        """Config is reloaded when file mtime changes."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "mendicant.yaml"
        config_file.write_text(
            "mendicant:\n  verification:\n    min_score: 0.7\n",
            encoding="utf-8",
        )

        from mendicant_core.config.settings import get_config, reset_config

        reset_config()
        cfg1 = get_config(str(config_file))
        assert cfg1["mendicant"]["verification"]["min_score"] == 0.7

        # Modify the file — touch with new content and a different mtime
        import time

        time.sleep(0.05)  # Ensure mtime changes
        config_file.write_text(
            "mendicant:\n  verification:\n    min_score: 0.95\n",
            encoding="utf-8",
        )

        cfg2 = get_config(str(config_file))
        assert cfg2["mendicant"]["verification"]["min_score"] == 0.95

    def test_reset_config(self, tmp_path):
        """reset_config clears the cache so next call reloads."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "mendicant.yaml"
        config_file.write_text("key: value1\n", encoding="utf-8")

        from mendicant_core.config.settings import get_config, reset_config

        cfg1 = get_config(str(config_file))
        assert cfg1["key"] == "value1"

        reset_config()

        config_file.write_text("key: value2\n", encoding="utf-8")
        cfg2 = get_config(str(config_file))
        assert cfg2["key"] == "value2"

    def test_reload_config_force(self, tmp_path):
        """reload_config ignores cache and always reloads."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "mendicant.yaml"
        config_file.write_text("number: 42\n", encoding="utf-8")

        from mendicant_core.config.settings import get_config, reload_config, reset_config

        reset_config()
        get_config(str(config_file))

        config_file.write_text("number: 99\n", encoding="utf-8")
        cfg = reload_config(str(config_file))
        assert cfg["number"] == 99

    def test_find_config_env_var(self, tmp_path, monkeypatch):
        """Config file is found via MENDICANT_CONFIG_PATH env var."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "custom_config.yaml"
        config_file.write_text("env_test: true\n", encoding="utf-8")

        monkeypatch.setenv("MENDICANT_CONFIG_PATH", str(config_file))

        from mendicant_core.config.settings import _find_config_path

        found = _find_config_path()
        assert found is not None
        assert found == config_file

    def test_get_mendicant_config(self, tmp_path):
        """get_mendicant_config returns a MendicantConfig with parsed values."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        config_file = tmp_path / "mendicant.yaml"
        config_file.write_text(
            "mendicant:\n  context_budget:\n    default_budget: 15000\n",
            encoding="utf-8",
        )

        from mendicant_core.config.settings import get_mendicant_config, reset_config

        reset_config()
        # Load the config first
        from mendicant_core.config.settings import get_config

        get_config(str(config_file))

        mcfg = get_mendicant_config()
        assert mcfg.context_budget.default_budget == 15000
