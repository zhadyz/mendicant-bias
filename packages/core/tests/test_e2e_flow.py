"""End-to-end flow test for Mendicant Bias.

Tests the complete pipeline:
  classify -> route -> (simulated execute) -> verify_structure -> record -> recommend

Does NOT make real API calls. Tests the plumbing, not the LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestEndToEndFlow:
    def test_full_pipeline(self, tmp_path):
        """Test: classify task -> route tools -> record pattern -> recommend."""
        # 1. Classify
        from mendicant_core.middleware.smart_task_router import SmartTaskRouterMiddleware
        router = SmartTaskRouterMiddleware(patterns_store_path=None)
        task = "Implement JWT authentication with rate limiting"
        task_type, confidence = router._classify_keywords(task)
        assert task_type == "CRITICAL_CODE"
        assert confidence >= 0.8

        # 2. Route tools
        from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery
        reg_path = str(tmp_path / "registry.json")
        builder = RegistryBuilder(output_path=reg_path, embedding_model=None)
        builder.add_seed_tools()
        builder.build(compute_embeddings=False)
        query = RegistryQuery(registry_path=reg_path, embedding_model=None)
        results = query.search("JWT authentication", top_k=3)
        assert len(results) > 0

        # 3. Agent selection
        from mendicant_core.agents import AgentLoader
        profiles_dir = Path("packages/core/src/mendicant_core/agents/profiles")
        mapping_path = Path("packages/core/src/mendicant_core/agents/agent_mapping.json")
        if profiles_dir.exists():
            loader = AgentLoader(profiles_dir, mapping_path)
            agent = loader.get_agent_for_domain("code_engineering")
            assert agent == "hollowed_eyes"

        # 4. Record pattern
        from mendicant_core.patterns import PatternStore
        store_path = str(tmp_path / "patterns.json")
        store = PatternStore(store_path)
        store.append({
            "id": "test_1",
            "task_text": task,
            "task_type": task_type,
            "tools_used": ["write_file", "run_bash"],
            "outcome": "success",
            "duration_seconds": 5.0,
        })

        # 5. Memory
        from mendicant_core.memory import MemoryStore
        mem_path = str(tmp_path / "memory.json")
        mem = MemoryStore(mem_path)
        mem.add_fact("JWT auth requires rate limiting", "knowledge", 0.9, "test")
        facts = mem.get_facts(category="knowledge")
        assert len(facts) == 1

        # 6. Context optimization
        from mendicant_core.middleware.context_optimizer import ContextOptimizer
        optimizer = ContextOptimizer()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": task},
            {"role": "tool", "content": "x" * 3000},
            {"role": "assistant", "content": "Implementation complete."},
        ]
        result = optimizer.optimize(messages, budget_tokens=200)
        assert result["manifest"]["tokens_saved"] > 0

        # 7. Session state
        from mendicant_core.session import SessionStateManager
        mgr = SessionStateManager()
        session = mgr.get_or_create("test")
        session.task_classification = {"task_type": task_type, "confidence": confidence}
        assert session.task_classification["task_type"] == "CRITICAL_CODE"

        # 8. Sandbox
        from mendicant_core.sandbox import LocalSandboxProvider
        sandbox_dir = str(tmp_path / "sandbox")
        provider = LocalSandboxProvider(sandbox_dir)
        sandbox = provider.acquire("test_thread")
        sandbox.write_file("/mnt/user-data/workspace/auth.py", "def authenticate(): pass")
        content = sandbox.read_file("/mnt/user-data/workspace/auth.py")
        assert "authenticate" in content
        provider.release(sandbox.id)

    def test_mcp_tools_integration(self, tmp_path):
        """Test MCP server tool functions directly."""
        try:
            from mendicant_mcp.server import (
                _classify_task, _route_tools, _record_pattern,
                _recommend, _status, _list_agents, _remember, _recall,
            )
        except ImportError:
            pytest.skip("mendicant_mcp package not available")

        # Classify
        result = _classify_task("Build a REST API")
        assert result["task_type"] in ("CODE_GENERATION", "CRITICAL_CODE")

        # Status
        status = _status()
        assert status["system"] == "mendicant-bias"
        assert "middleware" in status

        # Remember + Recall
        _remember("Test fact for e2e", "knowledge", 0.9)
        recalled = _recall("test fact")
        assert len(recalled.get("facts", [])) > 0

        # Record pattern
        _record_pattern("Build REST API", "CODE_GENERATION", ["write_file"], "success")

        # List agents
        agents = _list_agents()
        assert len(agents.get("agents", [])) > 0
