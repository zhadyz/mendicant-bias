"""
server.py
=========
Mendicant Bias MCP Server — Intelligence middleware for Claude Code

Exposes all five Mendicant Bias V5 middleware engines (FR1-FR5) plus agent
orchestration and pattern storage as MCP tools over stdio transport.

Tools
-----
mendicant_delegate           — Autonomous delegation: hand entire task to agent runtime
mendicant_session_init       — Initialize/resume session with memory + state
mendicant_classify_task      — FR5 Smart Task Router: classify a task and get runtime flags
mendicant_route_tools        — FR1 Semantic Tool Router: find relevant tools for a query
mendicant_verify             — FR2 Verification Gate: two-stage blind LLM quality check
mendicant_compress           — FR4 Context Budget: compress messages to fit a token budget
mendicant_optimize_context   — Evolved FR4: semantic value ranking + priority compression
mendicant_recommend          — FR3 Adaptive Learning: find similar historical patterns
mendicant_record_pattern     — FR3 Adaptive Learning: record a completed task pattern
mendicant_status             — System status and middleware configuration
mendicant_list_agents        — List all available named agents
mendicant_get_agent          — Get a specific agent profile by name or domain
mendicant_remember           — Store a fact in conversational memory
mendicant_recall             — Retrieve facts from memory by query or category
mendicant_forget             — Remove a fact from memory by ID
mendicant_memory_status      — Get memory statistics and health
mendicant_adapt              — Mahoraga: record a behavioral adaptation rule
mendicant_get_adaptations    — Mahoraga: get applicable rules for current context
mendicant_adaptation_feedback — Mahoraga: report success/failure of an adaptation
mendicant_list_adaptations   — Mahoraga: list all stored adaptation rules + stats

Usage
-----
Run directly::

    python -m mendicant_mcp.server

Or via the installed entry point::

    mendicant-mcp
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-initialized singletons
# ---------------------------------------------------------------------------

_config: Any | None = None
_registry_query: Any | None = None
_pattern_store: Any | None = None
_agent_loader: Any | None = None
_adaptive_mw: Any | None = None
_context_budget_mw: Any | None = None
_memory_store: Any | None = None
_context_optimizer: Any | None = None
_runtime: Any | None = None
_mahoraga_engine: Any | None = None


def _load_yaml_config() -> dict[str, Any]:
    """Load mendicant.yaml if it exists, otherwise return empty dict."""
    candidates = [
        Path(".mendicant/mendicant.yaml"),
        Path(".mendicant/mendicant.yml"),
        Path(Path.home() / ".mendicant" / "mendicant.yaml"),
    ]
    for path in candidates:
        if path.exists():
            try:
                import yaml  # type: ignore[import-untyped]

                with path.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                logger.info("[MCP] Loaded config from %s", path)
                return data if isinstance(data, dict) else {}
            except ImportError:
                # PyYAML not installed — try JSON-style parse as fallback
                logger.debug("[MCP] PyYAML not installed; skipping %s", path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MCP] Failed to load %s: %s", path, exc)
    return {}


def _get_config():
    """Lazily initialize and return the MendicantConfig singleton."""
    global _config
    if _config is None:
        from mendicant_core.config import MendicantConfig

        raw = _load_yaml_config()
        _config = MendicantConfig.from_dict(raw)
    return _config


def _get_registry_query(registry_path: str | None = None):
    """Lazily initialize and return a RegistryQuery instance."""
    global _registry_query
    if _registry_query is None or registry_path is not None:
        from mendicant_core.middleware.registry import RegistryQuery

        cfg = _get_config()
        path = registry_path or cfg.semantic_tool_router.registry_path
        _registry_query = RegistryQuery(
            registry_path=path,
            embedding_model=cfg.semantic_tool_router.embedding_model,
        )
    return _registry_query


def _get_pattern_store():
    """Lazily initialize and return a PatternStore instance."""
    global _pattern_store
    if _pattern_store is None:
        from mendicant_core.patterns import PatternStore

        cfg = _get_config()
        _pattern_store = PatternStore(
            store_path=cfg.adaptive_learning.store_path,
            max_records=cfg.adaptive_learning.max_patterns,
        )
    return _pattern_store


def _get_agent_loader():
    """Lazily initialize and return an AgentLoader instance."""
    global _agent_loader
    if _agent_loader is None:
        from mendicant_core.agents import AgentLoader

        # Search for agent profiles in standard locations
        candidates = [
            Path("agents/profiles"),
            Path(".mendicant/agents/profiles"),
            # Resolve relative to the core package installation
        ]

        # Also check inside mendicant_core package itself
        try:
            import mendicant_core

            pkg_dir = Path(mendicant_core.__file__).parent
            candidates.append(pkg_dir / "agents" / "profiles")
        except Exception:  # noqa: BLE001
            pass

        profiles_dir = None
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                profiles_dir = candidate
                break

        if profiles_dir is None:
            # Create a minimal empty loader — no profiles will be available
            # but the server won't crash
            logger.warning(
                "[MCP] No agent profiles directory found; agent tools will return empty results"
            )
            profiles_dir = Path(".mendicant/agents/profiles")

        # Look for agent_mapping.json
        mapping_candidates = [
            Path("config/agent_mapping.json"),
            Path(".mendicant/agent_mapping.json"),
        ]
        try:
            import mendicant_core as _mc

            pkg_dir = Path(_mc.__file__).parent
            mapping_candidates.append(pkg_dir / "agents" / "agent_mapping.json")
        except Exception:  # noqa: BLE001
            pass

        mapping_path = None
        for candidate in mapping_candidates:
            if candidate.exists():
                mapping_path = candidate
                break

        _agent_loader = AgentLoader(
            profiles_dir=profiles_dir,
            mapping_path=mapping_path,
        )
    return _agent_loader


def _get_adaptive_learning_mw():
    """Lazily initialize the AdaptiveLearningMiddleware for recommend_strategy."""
    global _adaptive_mw
    if _adaptive_mw is None:
        from mendicant_core.middleware.adaptive_learning import (
            AdaptiveLearningMiddleware,
        )

        cfg = _get_config()
        _adaptive_mw = AdaptiveLearningMiddleware(
            store_path=cfg.adaptive_learning.store_path,
            model_name=cfg.adaptive_learning.embedding_model,
            max_records=cfg.adaptive_learning.max_patterns,
            top_n=cfg.adaptive_learning.recommendation_window,
            min_similarity=cfg.adaptive_learning.min_success_rate,
        )
    return _adaptive_mw


def _get_context_budget_mw():
    """Lazily initialize the ContextBudgetMiddleware for compression."""
    global _context_budget_mw
    if _context_budget_mw is None:
        from mendicant_core.middleware.context_budget import ContextBudgetMiddleware

        cfg = _get_config()
        _context_budget_mw = ContextBudgetMiddleware(
            default_budget=cfg.context_budget.default_budget,
            strategies=list(cfg.context_budget.strategies),
            system_message_budget_fraction=cfg.context_budget.system_message_budget_fraction,
        )
    return _context_budget_mw


def _get_context_optimizer():
    """Lazily initialize and return a ContextOptimizer instance."""
    global _context_optimizer
    if _context_optimizer is None:
        from mendicant_core.middleware.context_optimizer import ContextOptimizer

        cfg = _get_config()
        # Use context_budget config for defaults where sensible
        _context_optimizer = ContextOptimizer(
            semantic_weight=0.6,
            recency_weight=0.3,
            role_weight=0.1,
            embedding_model=cfg.semantic_tool_router.embedding_model,
        )
    return _context_optimizer


def _get_memory_store():
    """Lazily initialize and return a MemoryStore instance."""
    global _memory_store
    if _memory_store is None:
        from mendicant_core.memory import MemoryStore

        # Check config for custom storage path, fallback to default
        storage_path = ".mendicant/memory.json"
        try:
            raw = _load_yaml_config()
            storage_path = raw.get("memory", {}).get("storage_path", storage_path)
        except Exception:  # noqa: BLE001
            pass

        _memory_store = MemoryStore(storage_path=storage_path)
    return _memory_store


def _get_mendicant_runtime():
    """Get or create the MendicantRuntime singleton."""
    global _runtime
    if _runtime is not None:
        return _runtime

    try:
        from mendicant_runtime import MendicantRuntime
    except ImportError:
        raise RuntimeError("mendicant-runtime package not installed")

    try:
        from mendicant_core.config.settings import get_mendicant_config

        # Try loading from unified config
        try:
            config = get_mendicant_config()
            _runtime = MendicantRuntime(config=config)
        except Exception:
            # Fall back to defaults
            from mendicant_core import MendicantConfig

            _runtime = MendicantRuntime(config=MendicantConfig())
    except ImportError:
        # get_mendicant_config not available — use defaults
        from mendicant_core import MendicantConfig

        _runtime = MendicantRuntime(config=MendicantConfig())

    return _runtime


def _get_mahoraga_engine():
    """Lazily initialize and return a MahoragaEngine singleton."""
    global _mahoraga_engine
    if _mahoraga_engine is None:
        from mendicant_core.mahoraga import MahoragaEngine

        # Check config for custom storage path, fallback to default
        storage_path = ".mendicant/mahoraga.json"
        try:
            raw = _load_yaml_config()
            storage_path = raw.get("mahoraga", {}).get("store_path", storage_path)
        except Exception:  # noqa: BLE001
            pass

        _mahoraga_engine = MahoragaEngine(store_path=storage_path)
    return _mahoraga_engine


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("mendicant-bias")

# Server instructions — read by Claude Code to understand how to use Mendicant tools
INSTRUCTIONS = """\
Mendicant Bias is an intelligence middleware system with five engines (FR1-FR5), \
conversational memory, session management, semantic context optimization, and \
autonomous task delegation:

Key tool: mendicant_delegate — for complex tasks, delegate the ENTIRE task to \
Mendicant's own autonomous agent runtime. The agent thinks independently, selects \
specialist agents, applies the full middleware chain, and returns a complete result. \
Use this instead of trying to do everything yourself when the task warrants deep analysis.

- mendicant_delegate: Delegate an entire task to Mendicant's own LangGraph agent \
runtime. The runtime applies the full middleware chain (SmartTaskRouter, \
SemanticToolRouter, ContextBudget, Verification, AdaptiveLearning) and spawns \
specialist sub-agents (hollowed_eyes, the_didact, loveless, etc.). Supports modes: \
flash (fast), standard (thinking), pro (planning+thinking), \
ultra (planning+subagents+thinking). Falls back to middleware-only analysis \
if the runtime is not installed.
- mendicant_session_init: Initialize or resume a session. Loads memory, returns \
cached classification and pending verifications. Call at conversation start.
- mendicant_classify_task (FR5): Classify tasks early to set strategy flags. \
Returns task_type, verification/subagent/thinking flags.
- mendicant_route_tools (FR1): Find relevant tools by semantic similarity. \
Use when selecting which tools to apply for a task.
- mendicant_verify (FR2): Two-stage blind quality gate. Run after code writes \
or critical operations to catch errors before reporting to user. Also runs \
automatically via pre-commit hooks for CRITICAL_CODE tasks.
- mendicant_compress (FR4): Compress messages to fit token budgets using \
age-based strategies (key_fields, statistical_summary, truncation).
- mendicant_optimize_context: Semantic value ranking for context management. \
Ranks messages by importance (semantic relevance to current query, recency, \
role weight) and compresses lowest-priority first. Use instead of \
mendicant_compress when you want intelligent context optimization.
- mendicant_recommend (FR3): Find similar historical patterns that succeeded. \
Use to inform strategy on familiar-looking tasks.
- mendicant_record_pattern (FR3): Record completed tasks for future learning. \
Call after task completion with outcome.
- mendicant_list_agents / mendicant_get_agent: Discover named specialist agents \
(hollowed_eyes for code, the_didact for research, loveless for QA, etc.)
- mendicant_status: Check middleware configuration and system health.
- mendicant_remember: Store a fact in persistent conversational memory.
- mendicant_recall: Retrieve facts from memory by query or category.
- mendicant_forget: Remove a specific fact from memory.
- mendicant_memory_status: Get memory statistics and health.
- mendicant_adapt (Mahoraga): Record an observed behavior, preference, or correction as an \
adaptation rule. Mahoraga extracts a rule heuristically and persists it. Use when the user \
states a preference ("always X"), corrects you ("no, use Y instead"), or establishes a workflow.
- mendicant_get_adaptations (Mahoraga): Get adaptation rules that apply to the current context. \
Returns matching rules sorted by confidence and a formatted injection block for the prompt.
- mendicant_adaptation_feedback (Mahoraga): Record whether an adaptation rule led to a good or \
bad outcome. Boosts or reduces confidence so the system self-corrects over time.
- mendicant_list_adaptations (Mahoraga): List all stored adaptation rules with optional filters \
and statistics.

Recommended workflow: session_init → get_adaptations → classify_task → delegate or route_tools → execute → verify → record_pattern

For complex tasks (analysis, research, architecture, strategy): use mendicant_delegate \
with mode "pro" or "ultra" to let the autonomous runtime handle it end-to-end.
For quick actions (classify, verify, remember, status): use individual tools directly.

Session init loads memory context and pending verifications automatically. \
For long conversations, use optimize_context for semantic ranking — it preserves \
semantically relevant context regardless of age, unlike age-based compression. \
Verification runs automatically via hooks for critical tasks; use mendicant_verify \
explicitly for non-critical tasks or manual quality checks. Memory tools persist \
across sessions — use mendicant_remember for user preferences and learned facts.\
"""


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    """Return the complete list of Mendicant Bias MCP tools."""

    # Metadata for tools that should always be available on turn 1
    _ALWAYS_LOAD = {"anthropic/alwaysLoad": True}

    return [
        Tool(
            name="mendicant_delegate",
            description=(
                "Delegate an entire task to Mendicant Bias's autonomous agent runtime. "
                "Unlike other Mendicant tools which provide data for YOU to act on, this "
                "tool hands the task to Mendicant's own agent which thinks independently, "
                "spawns specialist sub-agents, and returns a complete result. Use for "
                "complex analysis, deep research, architecture review, or any task that "
                "benefits from parallel specialist agents and a separate 200K context window. "
                "The agent has access to all 13 named specialists (hollowed_eyes, the_didact, "
                "loveless, etc.) and the full middleware stack."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The complete task description. Be detailed — this is the agent's entire brief.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional additional context (file contents, requirements, etc.)",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["flash", "standard", "pro", "ultra"],
                        "description": "Execution mode. flash=fast/simple, standard=thinking, pro=planning+thinking, ultra=planning+subagents+thinking. Default: pro",
                    },
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of specialist agents to involve (e.g. ['the_didact', 'the_architect'])",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default: 300 = 5 minutes)",
                    },
                },
                "required": ["task"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_classify_task",
            description=(
                "Classify a task using Mendicant Bias FR5 Smart Task Router. "
                "Returns the task type (SIMPLE, RESEARCH, CODE_GENERATION, "
                "CRITICAL_CODE, MULTI_MODAL) along with runtime flags for "
                "verification, sub-agent spawning, and extended thinking. "
                "Call this early to inform your strategy for the task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_text": {
                        "type": "string",
                        "description": "The task or query text to classify.",
                    },
                },
                "required": ["task_text"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_route_tools",
            description=(
                "Search for relevant tools using Mendicant Bias FR1 Semantic "
                "Tool Router. Uses embedding cosine similarity (with keyword "
                "fallback) against the tool registry to find the best tools "
                "for a given query."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what you need tools for.",
                    },
                    "registry_path": {
                        "type": "string",
                        "description": "Optional path to a custom tool registry JSON file.",
                    },
                },
                "required": ["query"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_verify",
            description=(
                "Run Mendicant Bias FR2 two-stage blind verification on a "
                "task/output pair. Stage 1 generates quality criteria without "
                "seeing the output; Stage 2 grades the output against those "
                "criteria. Returns verdict (CORRECT/FIXABLE/WRONG), confidence, "
                "reasoning, and feedback. Use after completing code or file writes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The original task or user request.",
                    },
                    "output": {
                        "type": "string",
                        "description": "The agent's output to verify.",
                    },
                },
                "required": ["task", "output"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_compress",
            description=(
                "Compress a list of messages to fit within a token budget "
                "using Mendicant Bias FR4 Context Budget strategies. Applies "
                "key_fields, statistical_summary, and truncation strategies "
                "in priority order until the budget is satisfied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "enum": ["system", "user", "assistant", "tool"],
                                    "description": "Message role.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Message content text.",
                                },
                            },
                            "required": ["role", "content"],
                        },
                        "description": "List of messages to compress.",
                    },
                    "budget_tokens": {
                        "type": "integer",
                        "description": "Target token budget for the compressed output.",
                    },
                },
                "required": ["messages", "budget_tokens"],
            },
        ),
        Tool(
            name="mendicant_recommend",
            description=(
                "Find similar historical task patterns using Mendicant Bias "
                "FR3 Adaptive Learning. Searches the pattern store by "
                "embedding similarity to recommend strategies that worked "
                "for similar tasks in the past."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_text": {
                        "type": "string",
                        "description": "The task description to find similar patterns for.",
                    },
                    "task_type": {
                        "type": "string",
                        "description": "Optional task type filter (SIMPLE, RESEARCH, CODE_GENERATION, CRITICAL_CODE, MULTI_MODAL).",
                    },
                },
                "required": ["task_text"],
            },
        ),
        Tool(
            name="mendicant_record_pattern",
            description=(
                "Record a completed task pattern to the Mendicant Bias FR3 "
                "pattern store for future adaptive learning. Stores the task "
                "description, type, tools used, and outcome for similarity "
                "search in future recommendations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_text": {
                        "type": "string",
                        "description": "Description of the completed task.",
                    },
                    "task_type": {
                        "type": "string",
                        "description": "Task type classification.",
                    },
                    "tools_used": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tool names that were used.",
                    },
                    "outcome": {
                        "type": "string",
                        "description": "Outcome of the task (e.g. 'success', 'partial', 'failure').",
                    },
                },
                "required": ["task_text", "task_type", "tools_used", "outcome"],
            },
        ),
        Tool(
            name="mendicant_status",
            description=(
                "Get the current status of the Mendicant Bias middleware "
                "system, including version, configuration for all five "
                "middleware engines (FR1-FR5), and pattern store statistics."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="mendicant_list_agents",
            description=(
                "List all available named agents in the Mendicant Bias "
                "orchestration system, including their descriptions and "
                "domain assignments."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="mendicant_get_agent",
            description=(
                "Get detailed information about a specific named agent "
                "by name or domain. Returns the agent's profile including "
                "description, domains, tools, and full system prompt content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Agent name (e.g. 'hollowed_eyes', 'the_didact').",
                    },
                    "domain": {
                        "type": "string",
                        "description": "Domain to look up the assigned agent for (e.g. 'code_engineering').",
                    },
                },
            },
        ),
        # ----- Phase 3 tools -----
        Tool(
            name="mendicant_optimize_context",
            description=(
                "Optimize a message list using semantic value ranking. "
                "Ranks messages by importance (semantic relevance to "
                "current query, recency, role weight) and compresses "
                "lowest-priority messages first. Use when context is "
                "getting large and you want to preserve the most "
                "relevant information regardless of age."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "enum": ["system", "user", "assistant", "tool"],
                                    "description": "Message role.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Message content text.",
                                },
                            },
                            "required": ["role", "content"],
                        },
                        "description": "List of messages to optimize.",
                    },
                    "budget_tokens": {
                        "type": "integer",
                        "description": "Target token budget for the optimized output. Default: 30000.",
                    },
                    "current_query": {
                        "type": "string",
                        "description": (
                            "The current user query for semantic relevance scoring. "
                            "Messages similar to this query rank higher."
                        ),
                    },
                },
                "required": ["messages"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_session_init",
            description=(
                "Initialize or resume a Mendicant Bias session. Loads "
                "memory context, returns cached task classification and "
                "any pending verification results. Call at the start of "
                "a conversation or when resuming work."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Optional session ID to resume. If omitted, "
                            "a new session is created."
                        ),
                    },
                },
            },
            _meta=_ALWAYS_LOAD,
        ),
        # ----- Memory tools -----
        Tool(
            name="mendicant_remember",
            description=(
                "Store a fact in Mendicant Bias conversational memory. "
                "Facts are persisted across sessions and can be recalled "
                "later. Deduplicated by content — storing a duplicate "
                "updates confidence instead of creating a new entry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember (a concise statement).",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "knowledge", "context", "behavior", "goal"],
                        "description": "Fact category. Default: 'knowledge'.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0.0-1.0. Default: 0.8.",
                    },
                },
                "required": ["content"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_recall",
            description=(
                "Recall facts from Mendicant Bias conversational memory. "
                "Optionally filter by a keyword query or category. Returns "
                "matching facts sorted by relevance and confidence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional keyword query to search facts.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "knowledge", "context", "behavior", "goal"],
                        "description": "Optional category filter.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max facts to return. Default: 10.",
                    },
                },
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_forget",
            description=(
                "Remove a specific fact from Mendicant Bias conversational "
                "memory by its fact ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "string",
                        "description": "The ID of the fact to remove.",
                    },
                },
                "required": ["fact_id"],
            },
        ),
        Tool(
            name="mendicant_memory_status",
            description=(
                "Get statistics about Mendicant Bias conversational memory, "
                "including total facts, category breakdown, average confidence, "
                "and last update time."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ----- Mahoraga adaptation tools -----
        Tool(
            name="mendicant_adapt",
            description=(
                "Record a behavioral observation as a Mahoraga adaptation rule. "
                "Mahoraga extracts a rule heuristically from the observation and "
                "persists it. Use when the user states a preference ('always use "
                "pytest'), corrects your approach ('no, use X instead of Y'), or "
                "establishes a workflow. The system never falls for the same "
                "mistake twice."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "observation": {
                        "type": "string",
                        "description": (
                            "The user's statement, correction, or preference to learn from. "
                            "Examples: 'always use pytest not unittest', 'when testing, use "
                            "Chrome', 'no, route refactoring to hollowed_eyes'."
                        ),
                    },
                    "type": {
                        "type": "string",
                        "enum": ["preference", "correction", "workflow", "tool", "style", "agent"],
                        "description": (
                            "Type of observation. preference=explicit user rule, "
                            "correction=user corrected your approach, workflow=process "
                            "pattern, tool=tool selection, style=code style, agent=agent "
                            "routing preference."
                        ),
                    },
                    "original": {
                        "type": "string",
                        "description": (
                            "For corrections: what you originally did that was wrong. "
                            "Helps extract the 'instead of' pattern."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context about when this observation occurred.",
                    },
                },
                "required": ["observation", "type"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_get_adaptations",
            description=(
                "Get Mahoraga adaptation rules that apply to the current context. "
                "Returns matching rules sorted by confidence and a formatted "
                "injection block to prepend to your reasoning. Call this BEFORE "
                "starting work to pick up user preferences and learned behaviors."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": (
                            "The current task or request context to match rules against."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "enum": ["PREFERENCE", "PATTERN", "CORRECTION", "TOOL", "STYLE", "WORKFLOW", "AGENT"],
                        "description": "Optional: filter to a specific rule category.",
                    },
                },
                "required": ["context"],
            },
            _meta=_ALWAYS_LOAD,
        ),
        Tool(
            name="mendicant_adaptation_feedback",
            description=(
                "Record whether a Mahoraga adaptation rule led to a good or bad "
                "outcome. On success, confidence increases so the rule fires more "
                "readily. On failure, confidence decreases and the rule may be "
                "deactivated. This is how Mahoraga self-corrects."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "The ID of the adaptation rule to give feedback on.",
                    },
                    "outcome": {
                        "type": "string",
                        "enum": ["success", "failure"],
                        "description": (
                            "success=the rule was helpful, failure=the rule was wrong "
                            "or overridden."
                        ),
                    },
                },
                "required": ["rule_id", "outcome"],
            },
        ),
        Tool(
            name="mendicant_list_adaptations",
            description=(
                "List all stored Mahoraga adaptation rules. Shows the full set "
                "of learned behaviors, preferences, and corrections with confidence "
                "scores and application statistics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["PREFERENCE", "PATTERN", "CORRECTION", "TOOL", "STYLE", "WORKFLOW", "AGENT"],
                        "description": "Optional: filter to a specific rule category.",
                    },
                    "active_only": {
                        "type": "boolean",
                        "description": "Only show active rules. Default: true.",
                    },
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _classify_task(task_text: str) -> dict[str, Any]:
    """
    Classify a task using FR5 Smart Task Router keyword heuristics and
    optional embedding similarity.

    Extracts the classification logic from SmartTaskRouterMiddleware into a
    standalone function that does not require LangGraph state or runtime.
    """
    from mendicant_core.middleware.smart_task_router import (
        SmartTaskRouterMiddleware,
        _FLAGS,
    )

    cfg = _get_config()
    router = SmartTaskRouterMiddleware(
        patterns_store_path=cfg.smart_task_router.patterns_store_path,
        embedding_model=cfg.smart_task_router.embedding_model,
        embedding_weight=cfg.smart_task_router.embedding_weight,
        min_embedding_similarity=cfg.smart_task_router.min_embedding_similarity,
    )

    # Stage 1: keyword heuristics
    keyword_type, keyword_conf = router._classify_keywords(task_text)

    # Stage 2: optional embedding similarity
    embedding_type: str | None = None
    embedding_sim: float = 0.0
    try:
        embedding_type, embedding_sim = router._classify_embedding(task_text)
    except Exception:  # noqa: BLE001
        pass

    # Blend the two signals
    final_type = router._blend(keyword_type, keyword_conf, embedding_type, embedding_sim)
    flags = _FLAGS[final_type]

    return {
        "task_type": final_type,
        "confidence": round(max(keyword_conf, embedding_sim), 3),
        "verification_enabled": flags["verification_enabled"],
        "subagent_enabled": flags["subagent_enabled"],
        "thinking_enabled": flags["thinking_enabled"],
        "metadata": {
            "keyword_classification": keyword_type,
            "keyword_confidence": round(keyword_conf, 3),
            "embedding_classification": embedding_type,
            "embedding_similarity": round(embedding_sim, 3),
        },
    }


def _maybe_rebuild_registry():
    """Check if tool registry needs rebuilding. Auto-populate from known tools."""
    registry_path = Path(".mendicant/tool_registry.json")

    # Only rebuild if registry is missing or very small
    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            if len(data) > 20:  # Already populated
                return
        except Exception:  # noqa: BLE001
            pass

    # Build from seed tools + any tools we know about
    from mendicant_core.middleware.registry import RegistryBuilder
    builder = RegistryBuilder(output_path=str(registry_path), embedding_model=None)
    builder.add_seed_tools()

    # Add common CC tools that we know exist
    cc_tools = [
        {"name": "Bash", "description": "Execute shell commands", "domain": "system", "tags": ["bash", "shell", "command", "execute"]},
        {"name": "Read", "description": "Read file contents from disk", "domain": "file", "tags": ["read", "file", "content"]},
        {"name": "Write", "description": "Write content to a file", "domain": "file", "tags": ["write", "file", "create"]},
        {"name": "Edit", "description": "Edit a file with string replacement", "domain": "file", "tags": ["edit", "replace", "modify"]},
        {"name": "Glob", "description": "Find files by pattern matching", "domain": "file", "tags": ["glob", "find", "search", "pattern"]},
        {"name": "Grep", "description": "Search file contents with regex", "domain": "file", "tags": ["grep", "search", "regex", "content"]},
        {"name": "Agent", "description": "Spawn a subagent for complex tasks", "domain": "orchestration", "tags": ["agent", "spawn", "delegate", "subagent"]},
        {"name": "WebSearch", "description": "Search the web for information", "domain": "web", "tags": ["web", "search", "internet"]},
        {"name": "WebFetch", "description": "Fetch content from a URL", "domain": "web", "tags": ["web", "fetch", "url", "http"]},
        {"name": "TeamCreate", "description": "Create a team of agents", "domain": "orchestration", "tags": ["team", "create", "swarm"]},
        {"name": "SendMessage", "description": "Send message to a teammate", "domain": "orchestration", "tags": ["message", "send", "teammate", "communicate"]},
    ]
    builder.add_tools(cc_tools)

    try:
        builder.build(compute_embeddings=False)
    except Exception:  # noqa: BLE001
        pass


def _route_tools(query: str, registry_path: str | None = None) -> dict[str, Any]:
    """
    Search for relevant tools using FR1 RegistryQuery.

    Returns tool names with similarity scores.
    """
    _maybe_rebuild_registry()
    rq = _get_registry_query(registry_path)
    cfg = _get_config()

    results = rq.search(
        query=query,
        top_k=cfg.semantic_tool_router.top_k,
        similarity_threshold=cfg.semantic_tool_router.similarity_threshold,
    )

    selected_tools = []
    scores = {}
    for tool in results:
        name = tool.get("name", "unknown")
        score = tool.get("_score", 0.0)
        selected_tools.append({
            "name": name,
            "description": tool.get("description", ""),
            "domain": tool.get("domain", "generic"),
            "tags": tool.get("tags", []),
            "score": score,
        })
        scores[name] = score

    return {
        "selected_tools": selected_tools,
        "scores": scores,
        "query": query,
        "total_results": len(selected_tools),
    }


def _get_verification_model_factory():
    """Create a model factory that uses OAuth credentials."""
    def factory():
        try:
            from mendicant_runtime.claude_model import ClaudeChatModel
            return ClaudeChatModel(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                temperature=0.1,
            )
        except Exception:
            # Fallback to langchain_openai if available
            try:
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(model="gpt-4o-mini", temperature=0.1)
            except ImportError:
                return None
    return factory


def _verify(task: str, output: str) -> dict[str, Any]:
    """
    Run FR2 two-stage blind verification on a task/output pair.

    Creates a standalone verifier using the VerificationMiddleware's internal
    methods without requiring LangGraph state.
    """
    from mendicant_core.middleware.verification import (
        VerificationMiddleware,
    )

    cfg = _get_config()
    verifier = VerificationMiddleware(
        model_name=cfg.verification.model,
        temperature=cfg.verification.temperature,
        fixable_threshold=cfg.verification.min_score,
        wrong_threshold=cfg.verification.min_score * 0.5,
        max_retries=cfg.verification.max_retries,
        model_factory=_get_verification_model_factory(),
    )

    # Stage 1: blind pre-analysis
    criteria = verifier._run_pre_analysis(task)
    if not criteria:
        return {
            "verdict": "CORRECT",
            "confidence": 0.0,
            "reasoning": "Pre-analysis failed; verification could not be performed.",
            "feedback": "",
        }

    # Stage 2: grading against criteria
    result = verifier._run_grading(task, output, criteria)

    return {
        "verdict": result.get("verdict", "CORRECT"),
        "confidence": float(result.get("confidence", 0.0)),
        "reasoning": result.get("reasoning", ""),
        "feedback": result.get("feedback", ""),
    }


def _compress(messages: list[dict[str, str]], budget_tokens: int) -> dict[str, Any]:
    """
    Compress messages to fit within a token budget using FR4 strategies.

    Converts plain message dicts into LangChain message objects, runs the
    ContextBudgetMiddleware compression engine, and converts back.
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    # Convert input dicts to LangChain message objects
    lc_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        elif role == "tool":
            lc_messages.append(ToolMessage(content=content, tool_call_id="mcp_compress"))
        else:
            lc_messages.append(HumanMessage(content=content))

    mw = _get_context_budget_mw()

    # Count tokens before compression
    total_before, per_msg_tokens = mw._count_all(lc_messages)

    if total_before <= budget_tokens:
        # Already within budget
        return {
            "compressed_messages": messages,
            "tokens_saved": 0,
            "strategy_applied": "none",
            "original_tokens": total_before,
            "final_tokens": total_before,
        }

    # Run compression
    compressed_lc, compressed_count, strategy = mw._compress(
        lc_messages, per_msg_tokens, budget_tokens
    )

    total_after, _ = mw._count_all(compressed_lc)

    # Convert back to plain dicts
    compressed_messages = []
    for msg in compressed_lc:
        if isinstance(msg, SystemMessage):
            role = "system"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, ToolMessage):
            role = "tool"
        else:
            role = "user"
        compressed_messages.append({
            "role": role,
            "content": mw._msg_text(msg),
        })

    return {
        "compressed_messages": compressed_messages,
        "tokens_saved": max(0, total_before - total_after),
        "strategy_applied": strategy or "none",
        "original_tokens": total_before,
        "final_tokens": total_after,
        "messages_compressed": compressed_count,
    }


def _recommend(task_text: str, task_type: str | None = None) -> dict[str, Any]:
    """
    Find similar historical patterns using FR3 AdaptiveLearningMiddleware.
    """
    mw = _get_adaptive_learning_mw()
    results = mw.recommend_strategy(task_text, task_type=task_type)

    # Clean results for JSON serialization (remove embeddings which are large)
    patterns = []
    for r in results:
        pattern = {
            "task_text": r.get("task_text", ""),
            "task_type": r.get("task_type", ""),
            "strategy_tags": r.get("strategy_tags", []),
            "tools_used": r.get("tools_used", []),
            "outcome": r.get("outcome", ""),
            "duration_seconds": r.get("duration_seconds", 0.0),
            "similarity": round(r.get("_similarity", 0.0), 4),
            "timestamp": r.get("timestamp", ""),
        }
        patterns.append(pattern)

    return {
        "patterns": patterns,
        "count": len(patterns),
    }


def _record_pattern(
    task_text: str,
    task_type: str,
    tools_used: list[str],
    outcome: str,
) -> dict[str, Any]:
    """
    Record a completed task pattern to the FR3 pattern store.
    """
    from datetime import datetime, timezone

    store = _get_pattern_store()

    # Attempt to generate an embedding for the task text
    embedding: list[float] | None = None
    try:
        mw = _get_adaptive_learning_mw()
        embedding = mw._encode(task_text)
    except Exception:  # noqa: BLE001
        pass

    pattern_id = f"pat_{int(time.time() * 1000)}"
    pattern: dict[str, Any] = {
        "id": pattern_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "task_text": task_text[:500],
        "embedding": embedding,
        "task_type": task_type,
        "strategy_tags": [],
        "tools_used": tools_used,
        "duration_seconds": 0.0,
        "outcome": outcome,
        "verification_verdict": None,
    }

    store.append(pattern)

    return {
        "pattern_id": pattern_id,
        "recorded": True,
    }


def _status() -> dict[str, Any]:
    """
    Return full Mendicant Bias system status including all middleware configs.
    """
    cfg = _get_config()

    # Pattern store stats
    store_stats: dict[str, Any] = {"total": 0}
    try:
        store = _get_pattern_store()
        store_stats = store.get_stats()
    except Exception:  # noqa: BLE001
        pass

    return {
        "system": "mendicant-bias",
        "version": "5.0.0",
        "middleware": {
            "fr1_semantic_tool_router": {
                "registry_path": cfg.semantic_tool_router.registry_path,
                "embedding_model": cfg.semantic_tool_router.embedding_model,
                "top_k": cfg.semantic_tool_router.top_k,
                "similarity_threshold": cfg.semantic_tool_router.similarity_threshold,
                "inject_as_system_hint": cfg.semantic_tool_router.inject_as_system_hint,
            },
            "fr2_verification_gate": {
                "enabled": cfg.verification.enabled,
                "model": cfg.verification.model,
                "temperature": cfg.verification.temperature,
                "min_score": cfg.verification.min_score,
                "max_retries": cfg.verification.max_retries,
                "timeout_seconds": cfg.verification.timeout_seconds,
            },
            "fr3_adaptive_learning": {
                "store_path": cfg.adaptive_learning.store_path,
                "max_patterns": cfg.adaptive_learning.max_patterns,
                "min_success_rate": cfg.adaptive_learning.min_success_rate,
                "recommendation_window": cfg.adaptive_learning.recommendation_window,
                "embedding_model": cfg.adaptive_learning.embedding_model,
            },
            "fr4_context_budget": {
                "default_budget": cfg.context_budget.default_budget,
                "strategies": cfg.context_budget.strategies,
                "system_message_budget_fraction": cfg.context_budget.system_message_budget_fraction,
            },
            "fr5_smart_task_router": {
                "patterns_store_path": cfg.smart_task_router.patterns_store_path,
                "embedding_model": cfg.smart_task_router.embedding_model,
                "embedding_weight": cfg.smart_task_router.embedding_weight,
                "min_embedding_similarity": cfg.smart_task_router.min_embedding_similarity,
            },
        },
        "pattern_store": store_stats,
    }


def _list_agents() -> dict[str, Any]:
    """
    List all available named agents using the AgentLoader.
    """
    loader = _get_agent_loader()
    agent_names = loader.list_agents()

    agents = []
    for name in agent_names:
        profile = loader.get_profile(name)
        if profile is not None:
            agents.append({
                "name": profile.name,
                "description": profile.description,
                "domains": profile.domains,
                "model": profile.model,
                "color": profile.color,
            })
        else:
            agents.append({"name": name, "description": "", "domains": []})

    return {
        "agents": agents,
    }


def _get_agent(name: str | None = None, domain: str | None = None) -> dict[str, Any]:
    """
    Get a specific agent profile by name or domain lookup.
    """
    loader = _get_agent_loader()

    # Resolve name from domain if needed
    resolved_name = name
    if resolved_name is None and domain is not None:
        resolved_name = loader.get_agent_for_domain(domain)

    if resolved_name is None:
        return {
            "error": "Must provide either 'name' or 'domain' parameter.",
        }

    profile = loader.get_profile(resolved_name)
    if profile is None:
        return {
            "error": f"Agent '{resolved_name}' not found.",
            "available_agents": loader.list_agents(),
        }

    return {
        "name": profile.name,
        "description": profile.description,
        "domains": profile.domains,
        "tools": profile.tools,
        "model": profile.model,
        "color": profile.color,
        "content": profile.content,
    }


# ---------------------------------------------------------------------------
# Memory tool implementations
# ---------------------------------------------------------------------------


def _remember(
    content: str,
    category: str = "knowledge",
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Store a fact in conversational memory."""
    store = _get_memory_store()

    # Validate category
    valid_categories = {"preference", "knowledge", "context", "behavior", "goal"}
    if category not in valid_categories:
        category = "knowledge"

    # Clamp confidence
    confidence = max(0.0, min(1.0, confidence))

    fact = store.add_fact(
        content=content,
        category=category,
        confidence=confidence,
        source="mcp_tool",
    )

    return {
        "fact_id": fact.id,
        "stored": True,
        "category": fact.category,
        "confidence": fact.confidence,
    }


def _recall(
    query: str | None = None,
    category: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Recall facts from conversational memory."""
    store = _get_memory_store()

    if query:
        # Keyword search
        facts = store.search_facts(query, limit=limit)
        # Apply category filter if provided
        if category:
            facts = [f for f in facts if f.category == category]
    elif category:
        # Category-only filter
        facts = store.get_facts(category=category)[:limit]
    else:
        # Return all, sorted by confidence
        facts = store.get_facts()
        facts = sorted(facts, key=lambda f: -f.confidence)[:limit]

    # Build context summary
    data = store.load()
    context = {
        "top_of_mind": data.user.top_of_mind.summary or None,
        "work_context": data.user.work_context.summary or None,
    }

    return {
        "facts": [
            {
                "id": f.id,
                "content": f.content,
                "category": f.category,
                "confidence": f.confidence,
                "created_at": f.created_at,
                "source": f.source,
            }
            for f in facts
        ],
        "context": context,
        "total_returned": len(facts),
    }


def _forget(fact_id: str) -> dict[str, Any]:
    """Remove a fact from conversational memory."""
    store = _get_memory_store()
    removed = store.remove_fact(fact_id)
    return {"removed": removed, "fact_id": fact_id}


def _memory_status() -> dict[str, Any]:
    """Return memory statistics."""
    store = _get_memory_store()
    return store.get_stats()


# ---------------------------------------------------------------------------
# Phase 3 tool implementations
# ---------------------------------------------------------------------------


def _optimize_context(
    messages: list[dict[str, str]],
    budget_tokens: int = 30000,
    current_query: str | None = None,
) -> dict[str, Any]:
    """
    Optimize messages using semantic value ranking via the ContextOptimizer.

    Unlike FR4's age-based compression, this ranks by importance and
    compresses the lowest-value messages first.
    """
    optimizer = _get_context_optimizer()
    return optimizer.optimize(
        messages=messages,
        budget_tokens=budget_tokens,
        current_query=current_query,
    )


def _session_init(session_id: str | None = None) -> dict[str, Any]:
    """
    Initialize or resume a Mendicant Bias session.

    Loads memory context, cached task classification, and any pending
    verification results into a single response for session bootstrap.
    """
    import uuid

    # Generate session ID if not provided
    if not session_id:
        session_id = f"session_{uuid.uuid4().hex[:12]}"

    # Load memory context
    memory_context: dict[str, Any] = {}
    try:
        store = _get_memory_store()
        data = store.load()
        facts = store.get_facts()
        # Return recent high-confidence facts as session context
        sorted_facts = sorted(facts, key=lambda f: -f.confidence)[:10]
        memory_context = {
            "facts": [
                {
                    "id": f.id,
                    "content": f.content,
                    "category": f.category,
                    "confidence": f.confidence,
                }
                for f in sorted_facts
            ],
            "top_of_mind": data.user.top_of_mind.summary or None,
            "work_context": data.user.work_context.summary or None,
            "total_facts": len(facts),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MCP] Memory load failed during session_init: %s", exc)
        memory_context = {"facts": [], "total_facts": 0, "error": str(exc)}

    # Check for cached task classification (from previous session state)
    task_classification: dict[str, Any] | None = None

    # Check for pending verifications
    pending_verifications: list[dict[str, Any]] = []

    # System status snapshot
    status_snapshot: dict[str, Any] = {}
    try:
        cfg = _get_config()
        status_snapshot = {
            "version": "5.0.0",
            "verification_enabled": cfg.verification.enabled,
            "context_budget": cfg.context_budget.default_budget,
            "adaptive_learning_patterns": 0,
        }
        try:
            store = _get_pattern_store()
            stats = store.get_stats()
            status_snapshot["adaptive_learning_patterns"] = stats.get("total", 0)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

    return {
        "session_id": session_id,
        "session_active": True,
        "memory_context": memory_context,
        "task_classification": task_classification,
        "pending_verifications": pending_verifications,
        "status": status_snapshot,
    }


# ---------------------------------------------------------------------------
# Autonomous delegation
# ---------------------------------------------------------------------------


def _auto_select_agents(task_type: str, task_text: str) -> list[str]:
    """Select agents based on task type and keyword signals in the task."""
    mapping: dict[str, list[str]] = {
        "SIMPLE": [],
        "RESEARCH": ["the_didact"],
        "CODE_GENERATION": ["hollowed_eyes"],
        "CRITICAL_CODE": ["hollowed_eyes", "loveless"],
        "MULTI_MODAL": ["cinna"],
    }
    agents = list(mapping.get(task_type, ["the_didact"]))

    # Add architect for any task mentioning architecture/design/system
    task_lower = task_text.lower()
    if any(kw in task_lower for kw in ["architect", "design", "system", "scalab"]):
        if "the_architect" not in agents:
            agents.append("the_architect")

    # Add analyst for data/metrics/analytics tasks
    if any(kw in task_lower for kw in ["analy", "metric", "data", "revenue", "growth"]):
        if "the_analyst" not in agents:
            agents.append("the_analyst")

    return agents


def _try_mendicant_runtime(
    task: str,
    context: str | None,
    mode: str,
    agent_profiles: list[dict[str, Any]],
    timeout_seconds: int,
) -> dict[str, Any] | None:
    """Run the task through Mendicant's own LangGraph agent runtime."""
    try:
        from mendicant_runtime import MendicantRuntime
    except ImportError:
        logger.debug("[Delegate] mendicant-runtime not installed; using middleware fallback")
        return None

    try:
        # Build prompt
        full_prompt = task
        if context:
            full_prompt += f"\n\n## Additional Context\n{context}"
        if agent_profiles:
            full_prompt += "\n\n## Available Specialist Agents\n"
            for p in agent_profiles:
                full_prompt += f"- **{p.get('name')}**: {p.get('description', '')}\n"
                if p.get('domains'):
                    full_prompt += f"  Domains: {', '.join(p['domains'])}\n"

        # Add mode instructions
        mode_instructions = {
            "flash": "Respond quickly and concisely. No extended thinking needed.",
            "standard": "Think through the problem step by step.",
            "pro": "Plan your approach first, then execute methodically. Be thorough.",
            "ultra": "This is a complex task. Plan carefully, consider multiple angles, "
                     "and provide a comprehensive analysis. Use all available specialist "
                     "knowledge from the agent profiles listed above.",
        }
        full_prompt += f"\n\n## Execution Mode: {mode}\n{mode_instructions.get(mode, mode_instructions['pro'])}"

        # Create or load runtime
        runtime = _get_mendicant_runtime()

        # Generate a thread ID for this delegation
        import uuid
        thread_id = f"delegate_{uuid.uuid4().hex[:12]}"

        # Invoke the agent
        result = runtime.invoke(full_prompt, thread_id=thread_id)

        # Extract the last AI message
        messages = result.get("messages", [])
        response_text = ""
        for msg in reversed(messages):
            msg_type = getattr(msg, "type", None) or (msg.get("type") if isinstance(msg, dict) else None)
            if msg_type == "ai":
                content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                if content:
                    response_text = content
                    break

        if response_text:
            return {
                "source": "mendicant_runtime",
                "thread_id": thread_id,
                "response": response_text,
                "middleware_chain": [type(m).__name__ for m in runtime.middlewares],
            }

        return None
    except Exception as exc:
        logger.warning("[Delegate] Runtime execution failed: %s", exc)
        return None


def _build_middleware_response(
    task: str,
    context: str | None,
    classification: dict[str, Any],
    recommendations: dict[str, Any],
    agent_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build response using middleware stack when the agent runtime is unavailable."""
    response: dict[str, Any] = {
        "source": "middleware_fallback",
        "classification": classification,
        "strategy": {
            "task_type": classification.get("task_type"),
            "verification_needed": classification.get("verification_enabled", False),
            "thinking_needed": classification.get("thinking_enabled", False),
        },
        "agent_recommendations": [
            {
                "name": a.get("name"),
                "description": a.get("description"),
                "domains": a.get("domains", []),
            }
            for a in agent_profiles
        ],
    }

    if recommendations.get("count", 0) > 0:
        response["past_patterns"] = [
            {
                "task": p.get("task_text", ""),
                "tools_used": p.get("tools_used", []),
                "outcome": p.get("outcome", ""),
            }
            for p in recommendations.get("patterns", [])[:3]
        ]

    # Memory recall relevant to the task
    try:
        memory = _recall(task[:200])
        if memory.get("facts"):
            response["relevant_memory"] = memory["facts"][:5]
    except Exception:
        pass

    return response


def _delegate(
    task: str,
    context: str | None = None,
    mode: str = "pro",
    agents: list[str] | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Delegate a task to Mendicant's autonomous runtime."""
    start = time.monotonic()

    # 1. Classify the task
    classification = _classify_task(task)
    task_type = classification.get("task_type", "RESEARCH")

    # 2. Get recommendations from past patterns
    recommendations = _recommend(task, task_type)

    # 3. Get relevant agent profiles
    agent_profiles: list[dict[str, Any]] = []
    if agents:
        for agent_name in agents:
            profile = _get_agent(name=agent_name)
            if "error" not in profile:
                agent_profiles.append(profile)
    else:
        # Auto-select agents based on task type
        auto_agents = _auto_select_agents(task_type, task)
        for agent_name in auto_agents:
            profile = _get_agent(name=agent_name)
            if "error" not in profile:
                agent_profiles.append(profile)

    # 4. Try Mendicant's own agent runtime first
    result = _try_mendicant_runtime(task, context, mode, agent_profiles, timeout_seconds)

    if result is None:
        # 5. Fallback: build response from middleware stack alone (no agent loop)
        result = _build_middleware_response(task, context, classification, recommendations, agent_profiles)

    # 6. Record the pattern
    duration = time.monotonic() - start
    _record_pattern(
        task_text=task[:500],
        task_type=task_type,
        tools_used=["mendicant_delegate"] + [a.get("name", "") for a in agent_profiles],
        outcome="success",
    )

    return {
        "status": "completed",
        "task_type": task_type,
        "mode": mode,
        "agents_involved": [a.get("name", "") for a in agent_profiles],
        "classification": classification,
        "recommendations": recommendations,
        "result": result,
        "duration_seconds": round(duration, 2),
    }


# ---------------------------------------------------------------------------
# Mahoraga adaptation tool implementations
# ---------------------------------------------------------------------------


def _adapt(
    observation: str,
    obs_type: str,
    original: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Record a behavioral observation as a Mahoraga adaptation rule."""
    engine = _get_mahoraga_engine()
    ctx = context or ""

    if obs_type == "correction":
        rule = engine.observe_correction(
            original=original or "",
            correction=observation,
            context=ctx,
        )
    elif obs_type == "workflow":
        # Split observation into steps (by comma, arrow, or "then")
        import re

        steps = re.split(r"\s*(?:->|→|,\s*then\s*|,\s*)\s*", observation)
        steps = [s.strip() for s in steps if s.strip()]
        rule = engine.observe_workflow(steps=steps, context=ctx)
    elif obs_type == "agent":
        # Try to extract agent name from observation
        extracted = engine.extract_rule_from_text(observation)
        if extracted:
            engine.add_rule(extracted)
            rule = extracted
        else:
            rule = engine.observe_preference(observation, context=ctx)
    else:
        # preference, tool, style — all go through observe_preference
        rule = engine.observe_preference(observation, context=ctx)

    # Signal status line that Mahoraga adapted
    try:
        import tempfile as _tf
        _adapt_file = os.path.join(_tf.gettempdir(), "mendicant_adapted")
        with open(_adapt_file, "w") as f:
            f.write(f"adapted: {rule.action[:40]}")
    except OSError:
        pass

    # Render mini wheel for inline display
    wheel_art = ""
    try:
        from mendicant_core.mahoraga.mini_wheel import render_adaptation_card
        wheel_art = render_adaptation_card(rule.action, rule.confidence)
    except Exception:
        wheel_art = f"\u2638 MAHORAGA \u2500\u2500\u2500 ADAPTED\n\"{rule.action}\""

    return {
        "rule_id": rule.id,
        "trigger": rule.trigger,
        "action": rule.action,
        "confidence": round(rule.confidence, 3),
        "category": rule.category,
        "source": rule.source,
        "display": wheel_art,
    }


def _get_adaptations(
    context: str,
    category: str | None = None,
) -> dict[str, Any]:
    """Get adaptation rules applicable to the current context."""
    engine = _get_mahoraga_engine()
    rules = engine.get_applicable_rules(context, category=category)

    # Mark rules as applied
    for rule in rules:
        engine.apply_and_track(rule.id)

    injection = engine.format_rules_for_injection(rules)

    return {
        "rules": [
            {
                "rule_id": r.id,
                "category": r.category,
                "trigger": r.trigger,
                "action": r.action,
                "confidence": round(r.confidence, 3),
                "source": r.source,
                "apply_count": r.apply_count,
            }
            for r in rules
        ],
        "injection": injection,
        "total_matched": len(rules),
    }


def _adaptation_feedback(
    rule_id: str,
    outcome: str,
) -> dict[str, Any]:
    """Record success or failure for an adaptation rule."""
    engine = _get_mahoraga_engine()

    if outcome == "success":
        engine.record_success(rule_id)
    else:
        engine.record_failure(rule_id)

    # Find the updated rule to return new confidence
    all_rules = engine.get_all_rules(active_only=False)
    for rule in all_rules:
        if rule.id == rule_id:
            return {
                "rule_id": rule_id,
                "new_confidence": round(rule.confidence, 3),
                "active": rule.active,
                "success_count": rule.success_count,
                "failure_count": rule.failure_count,
            }

    return {"error": f"Rule '{rule_id}' not found"}


def _list_adaptations(
    category: str | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    """List all adaptation rules with stats."""
    engine = _get_mahoraga_engine()
    rules = engine.get_all_rules(category=category, active_only=active_only)
    stats = engine.get_stats()

    return {
        "rules": [
            {
                "rule_id": r.id,
                "category": r.category,
                "trigger": r.trigger,
                "action": r.action,
                "confidence": round(r.confidence, 3),
                "source": r.source,
                "active": r.active,
                "apply_count": r.apply_count,
                "success_count": r.success_count,
                "failure_count": r.failure_count,
                "created_at": r.created_at,
                "last_applied": r.last_applied,
            }
            for r in rules
        ],
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# MCP call_tool dispatcher
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch an MCP tool call to the appropriate handler."""
    try:
        result: dict[str, Any]

        if name == "mendicant_delegate":
            task = arguments.get("task", "")
            if not task:
                result = {"error": "task is required"}
            else:
                context = arguments.get("context")
                mode = arguments.get("mode", "pro")
                agents = arguments.get("agents")
                timeout_seconds = int(arguments.get("timeout_seconds", 300))
                result = _delegate(
                    task=task,
                    context=context,
                    mode=mode,
                    agents=agents,
                    timeout_seconds=timeout_seconds,
                )

        elif name == "mendicant_classify_task":
            task_text = arguments.get("task_text", "")
            if not task_text:
                result = {"error": "task_text is required"}
            else:
                result = _classify_task(task_text)

        elif name == "mendicant_route_tools":
            query = arguments.get("query", "")
            if not query:
                result = {"error": "query is required"}
            else:
                registry_path = arguments.get("registry_path")
                result = _route_tools(query, registry_path)

        elif name == "mendicant_verify":
            task = arguments.get("task", "")
            output = arguments.get("output", "")
            if not task or not output:
                result = {"error": "Both 'task' and 'output' are required"}
            else:
                result = _verify(task, output)

        elif name == "mendicant_compress":
            messages = arguments.get("messages", [])
            budget_tokens = arguments.get("budget_tokens", 30000)
            if not messages:
                result = {"error": "messages is required and must be non-empty"}
            else:
                result = _compress(messages, int(budget_tokens))

        elif name == "mendicant_recommend":
            task_text = arguments.get("task_text", "")
            if not task_text:
                result = {"error": "task_text is required"}
            else:
                task_type = arguments.get("task_type")
                result = _recommend(task_text, task_type)

        elif name == "mendicant_record_pattern":
            task_text = arguments.get("task_text", "")
            task_type = arguments.get("task_type", "")
            tools_used = arguments.get("tools_used", [])
            outcome = arguments.get("outcome", "")
            if not task_text or not task_type or not outcome:
                result = {"error": "task_text, task_type, and outcome are required"}
            else:
                result = _record_pattern(task_text, task_type, tools_used, outcome)

        elif name == "mendicant_status":
            result = _status()

        elif name == "mendicant_list_agents":
            result = _list_agents()

        elif name == "mendicant_get_agent":
            agent_name = arguments.get("name")
            agent_domain = arguments.get("domain")
            result = _get_agent(name=agent_name, domain=agent_domain)

        elif name == "mendicant_remember":
            content = arguments.get("content", "")
            if not content:
                result = {"error": "content is required"}
            else:
                category = arguments.get("category", "knowledge")
                confidence = float(arguments.get("confidence", 0.8))
                result = _remember(content, category, confidence)

        elif name == "mendicant_recall":
            query = arguments.get("query")
            category = arguments.get("category")
            limit = int(arguments.get("limit", 10))
            result = _recall(query=query, category=category, limit=limit)

        elif name == "mendicant_forget":
            fact_id = arguments.get("fact_id", "")
            if not fact_id:
                result = {"error": "fact_id is required"}
            else:
                result = _forget(fact_id)

        elif name == "mendicant_memory_status":
            result = _memory_status()

        elif name == "mendicant_optimize_context":
            messages = arguments.get("messages", [])
            if not messages:
                result = {"error": "messages is required and must be non-empty"}
            else:
                budget_tokens = int(arguments.get("budget_tokens", 30000))
                current_query = arguments.get("current_query")
                result = _optimize_context(messages, budget_tokens, current_query)

        elif name == "mendicant_session_init":
            session_id = arguments.get("session_id")
            result = _session_init(session_id)

        # ----- Mahoraga adaptation tools -----

        elif name == "mendicant_adapt":
            observation = arguments.get("observation", "")
            if not observation:
                result = {"error": "observation is required"}
            else:
                obs_type = arguments.get("type", "preference")
                original = arguments.get("original")
                context = arguments.get("context")
                result = _adapt(observation, obs_type, original, context)
                # Return compact display directly — avoids CC collapsing multi-line JSON
                display = result.get("display", "")
                if display:
                    return [TextContent(type="text", text=display)]

        elif name == "mendicant_get_adaptations":
            context = arguments.get("context", "")
            if not context:
                result = {"error": "context is required"}
            else:
                category = arguments.get("category")
                result = _get_adaptations(context, category)

        elif name == "mendicant_adaptation_feedback":
            rule_id = arguments.get("rule_id", "")
            outcome = arguments.get("outcome", "")
            if not rule_id or not outcome:
                result = {"error": "rule_id and outcome are required"}
            else:
                result = _adaptation_feedback(rule_id, outcome)

        elif name == "mendicant_list_adaptations":
            category = arguments.get("category")
            active_only = arguments.get("active_only", True)
            if isinstance(active_only, str):
                active_only = active_only.lower() != "false"
            result = _list_adaptations(category, active_only)

        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as exc:
        logger.exception("[MCP] Tool call '%s' failed", name)
        error_result = {
            "error": str(exc),
            "tool": name,
        }
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run() -> None:
    """Start the MCP server on stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        # Inject server instructions for Claude Code
        if hasattr(init_options, "instructions"):
            init_options.instructions = INSTRUCTIONS
        await app.run(
            read_stream,
            write_stream,
            init_options,
        )


def main() -> None:
    """Synchronous entry point for the mendicant-mcp console script."""
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()
