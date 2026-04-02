"""
server.py
=========
Mendicant Bias MCP Server — Intelligence middleware for Claude Code

Exposes all five Mendicant Bias V5 middleware engines (FR1-FR5) plus agent
orchestration and pattern storage as MCP tools over stdio transport.

Tools
-----
mendicant_classify_task   — FR5 Smart Task Router: classify a task and get runtime flags
mendicant_route_tools     — FR1 Semantic Tool Router: find relevant tools for a query
mendicant_verify          — FR2 Verification Gate: two-stage blind LLM quality check
mendicant_compress        — FR4 Context Budget: compress messages to fit a token budget
mendicant_recommend       — FR3 Adaptive Learning: find similar historical patterns
mendicant_record_pattern  — FR3 Adaptive Learning: record a completed task pattern
mendicant_status          — System status and middleware configuration
mendicant_list_agents     — List all available named agents
mendicant_get_agent       — Get a specific agent profile by name or domain

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


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

app = Server("mendicant-bias")

# Server instructions — read by Claude Code to understand how to use Mendicant tools
INSTRUCTIONS = """\
Mendicant Bias is an intelligence middleware system with five engines (FR1-FR5):

- mendicant_classify_task (FR5): Classify tasks early to set strategy flags. \
Returns task_type, verification/subagent/thinking flags. Call this first.
- mendicant_route_tools (FR1): Find relevant tools by semantic similarity. \
Use when selecting which tools to apply for a task.
- mendicant_verify (FR2): Two-stage blind quality gate. Run after code writes \
or critical operations to catch errors before reporting to user.
- mendicant_compress (FR4): Compress messages to fit token budgets. Use when \
context is getting large.
- mendicant_recommend (FR3): Find similar historical patterns that succeeded. \
Use to inform strategy on familiar-looking tasks.
- mendicant_record_pattern (FR3): Record completed tasks for future learning. \
Call after task completion with outcome.
- mendicant_list_agents / mendicant_get_agent: Discover named specialist agents \
(hollowed_eyes for code, the_didact for research, loveless for QA, etc.)
- mendicant_status: Check middleware configuration and system health.

Recommended workflow: classify_task → route_tools → execute → verify → record_pattern\
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


def _route_tools(query: str, registry_path: str | None = None) -> dict[str, Any]:
    """
    Search for relevant tools using FR1 RegistryQuery.

    Returns tool names with similarity scores.
    """
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
# MCP call_tool dispatcher
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch an MCP tool call to the appropriate handler."""
    try:
        result: dict[str, Any]

        if name == "mendicant_classify_task":
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
