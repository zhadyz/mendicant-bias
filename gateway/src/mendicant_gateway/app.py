"""
mendicant_gateway.app
=====================
Mendicant Bias V5 — FastAPI Gateway Application

REST API providing HTTP access to the Mendicant intelligence middleware system.

Routes
------
GET  /health                        -> System health check
GET  /api/mendicant/status           -> Full middleware status
GET  /api/mendicant/agents           -> List all named agents
GET  /api/mendicant/agents/{name}    -> Get specific agent profile
GET  /api/mendicant/middleware        -> Middleware configuration details
GET  /api/mendicant/patterns/stats   -> Pattern store statistics
POST /api/mendicant/classify         -> Classify a task via SmartTaskRouter
POST /api/mendicant/route            -> Route tools for a query via SemanticToolRouter
POST /api/mendicant/verify           -> Run verification check
POST /api/mendicant/recommend        -> Get strategy recommendations

Phase 3 — Claude Code HTTP Hooks:
POST /hooks/session-start            -> CC SessionStart hook
POST /hooks/pre-tool-use             -> CC PreToolUse hook
POST /hooks/post-tool-use            -> CC PostToolUse hook
GET  /hooks/status                   -> Hook system status
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from mendicant_core import MendicantConfig, __version__ as core_version
from mendicant_core.agents import AgentLoader, AgentProfile
from mendicant_core.patterns import PatternStore
from mendicant_core.middleware import (
    AdaptiveLearningMiddleware,
    SmartTaskRouterMiddleware,
)
from mendicant_core.middleware.registry import RegistryQuery
from mendicant_gateway.hooks import hooks_router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state — populated at startup
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {}


def _load_mendicant_config() -> MendicantConfig:
    """
    Load MendicantConfig from YAML or environment, falling back to defaults.
    """
    config_path = os.environ.get("MENDICANT_CONFIG")

    if config_path and Path(config_path).exists():
        try:
            from mendicant_runtime.config import load_config

            full = load_config(config_path)
            return MendicantConfig.from_dict(full.get("mendicant", {}))
        except Exception as exc:
            logger.warning("[Gateway] Failed to load config from %s: %s", config_path, exc)

    # Try default search paths via runtime loader
    try:
        from mendicant_runtime.config import load_config

        full = load_config()
        return MendicantConfig.from_dict(full.get("mendicant", {}))
    except Exception:
        pass

    return MendicantConfig()


def _load_agent_loader() -> AgentLoader | None:
    """Attempt to load the agent profiles directory."""
    # Search common locations for agent profiles
    candidates = [
        Path("agents/profiles"),
        Path("src/agents/profiles"),
        Path.cwd() / "agents" / "profiles",
    ]
    mapping_candidates = [
        Path("agents/agent_mapping.json"),
        Path("config/agent_mapping.json"),
        Path.cwd() / "agents" / "agent_mapping.json",
    ]

    profiles_dir = None
    for c in candidates:
        if c.exists():
            profiles_dir = c
            break

    mapping_path = None
    for c in mapping_candidates:
        if c.exists():
            mapping_path = c
            break

    if profiles_dir is None:
        logger.info("[Gateway] No agent profiles directory found; agent endpoints will return empty results")
        return None

    try:
        return AgentLoader(profiles_dir=profiles_dir, mapping_path=mapping_path)
    except Exception as exc:
        logger.warning("[Gateway] Failed to load agents: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    startup_time = time.monotonic()

    # Load config
    config = _load_mendicant_config()
    _state["config"] = config
    logger.info("[Gateway] MendicantConfig loaded")

    # Build middleware instances for API use
    _state["smart_task_router"] = config.build_smart_task_router_middleware()
    _state["adaptive_learning"] = config.build_adaptive_learning_middleware()
    _state["context_budget"] = config.build_context_budget_middleware()

    # Pattern store
    _state["pattern_store"] = PatternStore(
        store_path=config.adaptive_learning.store_path,
        max_records=config.adaptive_learning.max_patterns,
    )

    # Tool registry query
    _state["registry_query"] = RegistryQuery(
        registry_path=config.semantic_tool_router.registry_path,
        embedding_model=config.semantic_tool_router.embedding_model,
    )

    # Agent loader
    _state["agent_loader"] = _load_agent_loader()

    _state["startup_time"] = time.monotonic() - startup_time
    logger.info("[Gateway] Startup complete in %.2fs", _state["startup_time"])

    yield

    # Shutdown
    _state.clear()
    logger.info("[Gateway] Shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Mendicant Bias Gateway",
    description=(
        "REST API for the Mendicant Bias V5 intelligence middleware system. "
        "Provides task classification, tool routing, verification, and "
        "strategy recommendation via HTTP.  Phase 3 adds CC hook endpoints "
        "for inline execution within the Claude Code pipeline."
    ),
    version="5.0.0",
    lifespan=lifespan,
)

# Phase 3 — Claude Code HTTP hook endpoints
app.include_router(hooks_router)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    """Request body for task classification."""

    task_text: str = Field(..., description="The task text to classify", min_length=1)


class ClassifyResponse(BaseModel):
    """Response for task classification."""

    task_type: str
    verification_enabled: bool
    subagent_enabled: bool
    thinking_enabled: bool
    routing_metadata: dict[str, Any]


class RouteRequest(BaseModel):
    """Request body for tool routing."""

    query: str = Field(..., description="The query to route tools for", min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    domain: str | None = Field(default=None, description="Optional domain filter")


class RouteResponse(BaseModel):
    """Response for tool routing."""

    query: str
    tools: list[dict[str, Any]]
    tool_count: int


class VerifyRequest(BaseModel):
    """Request body for verification."""

    task: str = Field(..., description="The task description", min_length=1)
    output: str = Field(..., description="The output to verify", min_length=1)


class VerifyResponse(BaseModel):
    """Response for verification."""

    verdict: str
    confidence: float
    reasoning: str
    feedback: str


class RecommendRequest(BaseModel):
    """Request body for strategy recommendation."""

    task_text: str = Field(..., description="The task text to get recommendations for", min_length=1)
    top_n: int = Field(default=5, ge=1, le=20)
    task_type: str | None = Field(default=None, description="Optional task type filter")


class RecommendResponse(BaseModel):
    """Response for strategy recommendation."""

    query: str
    recommendations: list[dict[str, Any]]
    count: int


class AgentSummary(BaseModel):
    """Summary of a named agent."""

    name: str
    description: str
    model: str
    color: str
    domains: list[str]
    tools: list[str]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    system: str
    version: str
    uptime_seconds: float | None = None


class StatusResponse(BaseModel):
    """Full middleware status response."""

    system: str
    version: str
    middleware: dict[str, Any]
    agents: dict[str, Any]
    patterns: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes — Health & Status
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health():
    """System health check."""
    uptime = time.monotonic() - _state.get("_app_start", time.monotonic())
    return HealthResponse(
        status="healthy",
        system="mendicant-bias",
        version="5.0.0",
        uptime_seconds=round(uptime, 2),
    )


@app.get("/api/mendicant/status", response_model=StatusResponse)
async def mendicant_status():
    """Full middleware status with configuration details."""
    config: MendicantConfig = _state["config"]
    agent_loader: AgentLoader | None = _state.get("agent_loader")
    pattern_store: PatternStore = _state["pattern_store"]

    # Agent info
    if agent_loader:
        agent_info = {
            "count": len(agent_loader.list_agents()),
            "names": agent_loader.list_agents(),
            "domains": agent_loader.list_domains(),
        }
    else:
        agent_info = {"count": 0, "names": [], "domains": []}

    # Pattern info
    pattern_stats = pattern_store.get_stats()

    return StatusResponse(
        system="mendicant-bias",
        version="5.0.0",
        middleware={
            "fr1_semantic_tool_router": config.semantic_tool_router.model_dump(),
            "fr2_verification": config.verification.model_dump(),
            "fr3_adaptive_learning": config.adaptive_learning.model_dump(),
            "fr4_context_budget": config.context_budget.model_dump(),
            "fr5_smart_task_router": config.smart_task_router.model_dump(),
        },
        agents=agent_info,
        patterns=pattern_stats,
    )


# ---------------------------------------------------------------------------
# Routes — Agents
# ---------------------------------------------------------------------------


@app.get("/api/mendicant/agents", response_model=list[AgentSummary])
async def list_agents():
    """List all named agents with descriptions and domains."""
    agent_loader: AgentLoader | None = _state.get("agent_loader")
    if agent_loader is None:
        return []

    results: list[AgentSummary] = []
    for name in agent_loader.list_agents():
        profile = agent_loader.get_profile(name)
        if profile:
            results.append(
                AgentSummary(
                    name=profile.name,
                    description=profile.description,
                    model=profile.model,
                    color=profile.color,
                    domains=profile.domains,
                    tools=profile.tools,
                )
            )
    return results


@app.get("/api/mendicant/agents/{name}", response_model=AgentSummary)
async def get_agent(name: str):
    """Get a specific agent profile by name."""
    agent_loader: AgentLoader | None = _state.get("agent_loader")
    if agent_loader is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found (no agent loader)")

    profile = agent_loader.get_profile(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    return AgentSummary(
        name=profile.name,
        description=profile.description,
        model=profile.model,
        color=profile.color,
        domains=profile.domains,
        tools=profile.tools,
    )


# ---------------------------------------------------------------------------
# Routes — Middleware
# ---------------------------------------------------------------------------


@app.get("/api/mendicant/middleware")
async def get_middleware():
    """Middleware configuration details."""
    config: MendicantConfig = _state["config"]

    return {
        "middleware_count": 5,
        "chain_order": [
            "SmartTaskRouter (before_agent)",
            "SemanticToolRouter (before_agent)",
            "ContextBudget (before_model)",
            "Verification (after_agent)",
            "AdaptiveLearning (after_agent)",
        ],
        "configurations": {
            "fr1_semantic_tool_router": {
                "description": "Embedding-based tool selection",
                "hook": "before_agent",
                "config": config.semantic_tool_router.model_dump(),
            },
            "fr2_verification": {
                "description": "Blind two-stage LLM quality gate",
                "hook": "after_agent",
                "enabled": config.verification.enabled,
                "config": config.verification.model_dump(),
            },
            "fr3_adaptive_learning": {
                "description": "Pattern recording and strategy recommendation",
                "hook": "after_agent",
                "config": config.adaptive_learning.model_dump(),
            },
            "fr4_context_budget": {
                "description": "Token enforcement with compression",
                "hook": "before_model",
                "config": config.context_budget.model_dump(),
            },
            "fr5_smart_task_router": {
                "description": "Task classification and runtime flag setting",
                "hook": "before_agent",
                "config": config.smart_task_router.model_dump(),
            },
        },
    }


# ---------------------------------------------------------------------------
# Routes — Pattern Stats
# ---------------------------------------------------------------------------


@app.get("/api/mendicant/patterns/stats")
async def get_pattern_stats():
    """Pattern store statistics."""
    pattern_store: PatternStore = _state["pattern_store"]
    return pattern_store.get_stats()


# ---------------------------------------------------------------------------
# Routes — Classify
# ---------------------------------------------------------------------------


@app.post("/api/mendicant/classify", response_model=ClassifyResponse)
async def classify_task(request: ClassifyRequest):
    """
    Classify a task using the SmartTaskRouter middleware.

    Runs the two-stage classification (keyword heuristics + optional
    embedding similarity) and returns the task type and runtime flags.
    """
    router: SmartTaskRouterMiddleware = _state["smart_task_router"]

    # Use the internal classification methods directly
    text = request.task_text
    keyword_type, keyword_conf = router._classify_keywords(text)

    embedding_type: str | None = None
    embedding_sim: float = 0.0
    try:
        embedding_type, embedding_sim = router._classify_embedding(text)
    except Exception:
        pass

    final_type = router._blend(keyword_type, keyword_conf, embedding_type, embedding_sim)

    # Look up runtime flags
    from mendicant_core.middleware.smart_task_router import _FLAGS

    flags = _FLAGS.get(final_type, _FLAGS["SIMPLE"])

    return ClassifyResponse(
        task_type=final_type,
        verification_enabled=flags["verification_enabled"],
        subagent_enabled=flags["subagent_enabled"],
        thinking_enabled=flags["thinking_enabled"],
        routing_metadata={
            "task_text_preview": text[:120],
            "keyword_classification": keyword_type,
            "keyword_confidence": round(keyword_conf, 3),
            "embedding_classification": embedding_type,
            "embedding_similarity": round(embedding_sim, 3),
            "final_type": final_type,
        },
    )


# ---------------------------------------------------------------------------
# Routes — Route
# ---------------------------------------------------------------------------


@app.post("/api/mendicant/route", response_model=RouteResponse)
async def route_tools(request: RouteRequest):
    """
    Route tools for a query using the semantic tool registry.

    Performs embedding-based search (with keyword fallback) against the
    tool registry and returns the most relevant tools.
    """
    registry: RegistryQuery = _state["registry_query"]

    tools = registry.search(
        query=request.query,
        top_k=request.top_k,
        domain=request.domain,
    )

    return RouteResponse(
        query=request.query,
        tools=tools,
        tool_count=len(tools),
    )


# ---------------------------------------------------------------------------
# Routes — Verify
# ---------------------------------------------------------------------------


@app.post("/api/mendicant/verify", response_model=VerifyResponse)
async def verify_output(request: VerifyRequest):
    """
    Run the two-stage Aletheia-style verification on a task/output pair.

    Stage 1: Blind pre-analysis of what correct output should look like.
    Stage 2: Grade the actual output against the criteria.
    """
    config: MendicantConfig = _state["config"]

    if not config.verification.enabled:
        return VerifyResponse(
            verdict="SKIPPED",
            confidence=0.0,
            reasoning="Verification is disabled in configuration",
            feedback="",
        )

    try:
        verifier = config.build_verification_middleware()

        # Run pre-analysis
        criteria = verifier._run_pre_analysis(request.task)
        if not criteria:
            return VerifyResponse(
                verdict="ERROR",
                confidence=0.0,
                reasoning="Pre-analysis returned empty — verifier LLM may not be configured",
                feedback="",
            )

        # Run grading
        result = verifier._run_grading(request.task, request.output, criteria)

        return VerifyResponse(
            verdict=result.get("verdict", "CORRECT"),
            confidence=float(result.get("confidence", 0.0)),
            reasoning=result.get("reasoning", ""),
            feedback=result.get("feedback", ""),
        )
    except Exception as exc:
        logger.error("[Gateway] Verification failed: %s", exc)
        return VerifyResponse(
            verdict="ERROR",
            confidence=0.0,
            reasoning=f"Verification failed: {exc}",
            feedback="",
        )


# ---------------------------------------------------------------------------
# Routes — Recommend
# ---------------------------------------------------------------------------


@app.post("/api/mendicant/recommend", response_model=RecommendResponse)
async def recommend_strategy(request: RecommendRequest):
    """
    Get strategy recommendations based on historical patterns.

    Embeds the task text and finds the most similar historical patterns
    from the adaptive learning store.
    """
    adaptive: AdaptiveLearningMiddleware = _state["adaptive_learning"]

    recommendations = adaptive.recommend_strategy(
        query=request.task_text,
        top_n=request.top_n,
        task_type=request.task_type,
    )

    # Clean up recommendations for JSON response — remove embeddings (large)
    cleaned: list[dict[str, Any]] = []
    for rec in recommendations:
        clean = {k: v for k, v in rec.items() if k != "embedding"}
        cleaned.append(clean)

    return RecommendResponse(
        query=request.task_text,
        recommendations=cleaned,
        count=len(cleaned),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Mendicant Bias Gateway with uvicorn."""
    import uvicorn

    host = os.environ.get("MENDICANT_HOST", "0.0.0.0")
    port = int(os.environ.get("MENDICANT_PORT", "8001"))
    reload = os.environ.get("MENDICANT_RELOAD", "true").lower() in ("true", "1", "yes")

    logger.info("[Gateway] Starting on %s:%d (reload=%s)", host, port, reload)

    uvicorn.run(
        "mendicant_gateway.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
