"""
Mendicant Bias V6 — Middleware Engines

Five functional requirement (FR) middleware for LangGraph agents:

- FR1: SemanticToolRouterMiddleware — Embedding-based tool selection
- FR2: VerificationMiddleware — Blind two-stage LLM quality gate
- FR3: AdaptiveLearningMiddleware — Pattern recording & strategy recommendation
- FR4: ContextBudgetMiddleware — Token enforcement with compression
- FR5: SmartTaskRouterMiddleware — Task classification & runtime flag setting

Plus production middleware ported from DeerFlow:

- DanglingToolCallMiddleware — Patches missing ToolMessage responses
- GuardrailMiddleware — Pre-tool-call authorization with pluggable providers
- SummarizationMiddleware — Context reduction via message summarization
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
from mendicant_core.middleware.dangling_tool_call import DanglingToolCallMiddleware
from mendicant_core.middleware.guardrails import (
    GuardrailMiddleware,
    GuardrailProvider,
    GuardrailRequest,
    GuardrailDecision,
    GuardrailReason,
    AllowlistProvider,
    DenylistProvider,
)
from mendicant_core.middleware.summarization import SummarizationMiddleware

__all__ = [
    # FR1-FR5 (Mendicant originals)
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
    # DeerFlow-ported middleware
    "DanglingToolCallMiddleware",
    "GuardrailMiddleware",
    "GuardrailProvider",
    "GuardrailRequest",
    "GuardrailDecision",
    "GuardrailReason",
    "AllowlistProvider",
    "DenylistProvider",
    "SummarizationMiddleware",
]
