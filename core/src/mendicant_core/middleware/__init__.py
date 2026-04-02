"""
Mendicant Bias V5 — Middleware Engines

Five functional requirement (FR) middleware for LangGraph agents:

- FR1: SemanticToolRouterMiddleware — Embedding-based tool selection
- FR2: VerificationMiddleware — Blind two-stage LLM quality gate
- FR3: AdaptiveLearningMiddleware — Pattern recording & strategy recommendation
- FR4: ContextBudgetMiddleware — Token enforcement with compression
- FR5: SmartTaskRouterMiddleware — Task classification & runtime flag setting
"""

from mendicant_core.middleware.semantic_tool_router import (
    SemanticToolRouterMiddleware,
    SemanticToolRouterState,
)
from mendicant_core.middleware.verification import (
    VerificationMiddleware,
    VerificationState,
)
from mendicant_core.middleware.adaptive_learning import (
    AdaptiveLearningMiddleware,
    AdaptiveLearningState,
)
from mendicant_core.middleware.context_budget import (
    ContextBudgetMiddleware,
    ContextBudgetState,
)
from mendicant_core.middleware.smart_task_router import (
    SmartTaskRouterMiddleware,
    SmartTaskRouterState,
)
from mendicant_core.middleware.registry import RegistryBuilder, RegistryQuery

__all__ = [
    "SemanticToolRouterMiddleware",
    "SemanticToolRouterState",
    "VerificationMiddleware",
    "VerificationState",
    "AdaptiveLearningMiddleware",
    "AdaptiveLearningState",
    "ContextBudgetMiddleware",
    "ContextBudgetState",
    "SmartTaskRouterMiddleware",
    "SmartTaskRouterState",
    "RegistryBuilder",
    "RegistryQuery",
]
