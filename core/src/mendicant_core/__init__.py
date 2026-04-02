"""
Mendicant Bias V5 — Intelligence Middleware for AI Agent Systems
================================================================

A standalone Python package providing five novel middleware engines for
LangGraph-based agents, plus a named-agent orchestration system.

Middleware (FR1-FR5):
    - SemanticToolRouterMiddleware — Embedding-based tool selection
    - VerificationMiddleware — Blind two-stage LLM quality gate
    - AdaptiveLearningMiddleware — Pattern recording & strategy recommendation
    - ContextBudgetMiddleware — Token enforcement with compression
    - SmartTaskRouterMiddleware — Task classification & runtime flag setting

Orchestration:
    - MendicantOrchestrator — Task routing with named agent assignment
    - AgentLoader — Named agent profile management
    - PatternStore — Persistent pattern storage with similarity search

Configuration:
    - MendicantConfig — Unified Pydantic configuration for all middleware

Quick Start::

    from mendicant_core import MendicantConfig

    config = MendicantConfig.from_dict({
        "context_budget": {"default_budget": 20000},
        "verification": {"model": "claude-sonnet-4-20250514"},
    })

    # Build middleware stack for LangGraph
    middlewares = config.build_all_middleware()

Named after Mendicant Bias, the Forerunner Contender-class AI from Halo.
"""

__version__ = "5.0.0"

from mendicant_core.config import MendicantConfig
from mendicant_core.memory import (
    MemoryData,
    MemoryInjector,
    MemoryStore,
    MemoryUpdater,
)
from mendicant_core.middleware import (
    AdaptiveLearningMiddleware,
    ContextBudgetMiddleware,
    RegistryBuilder,
    RegistryQuery,
    SemanticToolRouterMiddleware,
    SmartTaskRouterMiddleware,
    VerificationMiddleware,
)

__all__ = [
    "__version__",
    "MendicantConfig",
    # Memory
    "MemoryData",
    "MemoryInjector",
    "MemoryStore",
    "MemoryUpdater",
    # Middleware
    "SemanticToolRouterMiddleware",
    "VerificationMiddleware",
    "AdaptiveLearningMiddleware",
    "ContextBudgetMiddleware",
    "SmartTaskRouterMiddleware",
    "RegistryBuilder",
    "RegistryQuery",
]
