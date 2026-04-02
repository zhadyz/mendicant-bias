"""
mendicant_core.config
=====================
Mendicant Bias V5 — Unified Configuration

Pydantic v2 dataclass-style config models for all five middleware components
plus a ``MendicantConfig`` container that aggregates them.

Supports programmatic construction and ``from_dict()`` parsing from a plain
Python dict (loaded from YAML, JSON, environment injection, etc.).

Usage
-----
>>> from mendicant_core.config import MendicantConfig
>>> cfg = MendicantConfig.from_dict({
...     "context_budget": {"default_budget": 20000},
...     "smart_task_router": {"embedding_weight": 0.6},
... })
>>> cfg.context_budget.default_budget
20000
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# FR1 — Semantic Tool Router
# ---------------------------------------------------------------------------


class SemanticToolRouterConfig(BaseModel):
    """Configuration for SemanticToolRouterMiddleware."""

    registry_path: str = Field(
        default="tools/tools_schema.json",
        description="Path to the tool registry JSON file.",
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model used for embedding-based tool lookup.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of most-relevant tools to surface per query.",
    )
    similarity_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for a tool to be included.",
    )
    inject_as_system_hint: bool = Field(
        default=True,
        description="If True, prepend a system-hint message listing relevant tools.",
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# FR2 — Quality Gate Verification
# ---------------------------------------------------------------------------


class VerificationConfig(BaseModel):
    """Configuration for QualityGateMiddleware."""

    enabled: bool = Field(
        default=True,
        description="Master switch — set to False to disable all verification.",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="LLM model used as blind verifier.",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Temperature for the verifier LLM call.",
    )
    min_score: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum quality score (0–1) to pass verification.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Number of retry attempts before marking verification as failed.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Timeout for verifier LLM call in seconds.",
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# FR3 — Adaptive Learning
# ---------------------------------------------------------------------------


class AdaptiveLearningConfig(BaseModel):
    """Configuration for AdaptiveLearningMiddleware."""

    store_path: str = Field(
        default=".mendicant/orchestration_patterns.json",
        description="Path to the on-disk pattern store.",
    )
    max_patterns: int = Field(
        default=500,
        ge=10,
        description="Maximum number of patterns to retain (LRU eviction).",
    )
    min_success_rate: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum success rate before a pattern is recommended.",
    )
    recommendation_window: int = Field(
        default=20,
        ge=1,
        description="Number of recent patterns to scan when generating recommendations.",
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Model used to embed task descriptions for similarity matching.",
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# FR4 — Context Budget
# ---------------------------------------------------------------------------


class ContextBudgetConfig(BaseModel):
    """Configuration for ContextBudgetMiddleware."""

    default_budget: int = Field(
        default=30_000,
        ge=1_000,
        description="Default token budget per conversation thread.",
    )
    strategies: list[str] = Field(
        default_factory=lambda: ["key_fields", "statistical_summary", "truncation"],
        description=(
            "Compression strategies to apply in order when budget is exceeded. "
            "Valid values: 'key_fields', 'statistical_summary', 'truncation'."
        ),
    )
    system_message_budget_fraction: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of budget reserved for system messages (warning threshold)."
        ),
    )
    thread_budgets: dict[str, int] = Field(
        default_factory=dict,
        description="Per-thread budget overrides: {thread_id: token_count}.",
    )

    @model_validator(mode="after")
    def validate_strategies(self) -> "ContextBudgetConfig":
        valid = {"key_fields", "statistical_summary", "truncation"}
        bad = [s for s in self.strategies if s not in valid]
        if bad:
            raise ValueError(
                f"Unknown compression strategies: {bad}. Valid: {sorted(valid)}"
            )
        return self

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# FR5 — Smart Task Router
# ---------------------------------------------------------------------------


class SmartTaskRouterConfig(BaseModel):
    """Configuration for SmartTaskRouterMiddleware."""

    patterns_store_path: str | None = Field(
        default=".mendicant/orchestration_patterns.json",
        description=(
            "Path to adaptive-learning pattern store for embedding-assisted "
            "classification. Set to null to disable."
        ),
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model used for embedding similarity.",
    )
    embedding_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Weight (0–1) given to embedding similarity vs keyword heuristic "
            "confidence when both signals are available."
        ),
    )
    min_embedding_similarity: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for an embedding suggestion to be used.",
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Phase 3 — Hooks & Session
# ---------------------------------------------------------------------------


class HooksConfig(BaseModel):
    """Configuration for Claude Code HTTP hook integration (Phase 3)."""

    enabled: bool = Field(
        default=True,
        description="Master switch for the CC hooks system.",
    )
    gateway_url: str = Field(
        default="http://localhost:8001",
        description="Base URL of the Mendicant Bias gateway for hook endpoints.",
    )
    auto_verify_task_types: list[str] = Field(
        default_factory=lambda: ["CODE_GENERATION", "CRITICAL_CODE"],
        description=(
            "Task types that trigger automatic verification on Write/Edit hooks."
        ),
    )
    auto_verify_tool_matchers: list[str] = Field(
        default_factory=lambda: ["Write", "Edit"],
        description=(
            "Tool names that trigger automatic verification in PostToolUse hooks."
        ),
    )

    model_config = {"extra": "ignore"}


class SessionConfig(BaseModel):
    """Configuration for per-session state tracking."""

    max_age_hours: int = Field(
        default=4,
        ge=1,
        description="Maximum session age before cleanup, in hours.",
    )
    cleanup_interval_minutes: int = Field(
        default=10,
        ge=1,
        description="How often to run session cleanup, in minutes.",
    )

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class MendicantConfig(BaseModel):
    """
    Root configuration for the Mendicant Bias V5 middleware system.

    All sub-config objects are optional and default to their individual
    defaults when omitted.

    Example (YAML equivalent)
    -------------------------
    .. code-block:: yaml

        context_budget:
          default_budget: 20000
          strategies: [key_fields, truncation]
        smart_task_router:
          embedding_weight: 0.6
        verification:
          model: gpt-4o
          min_score: 0.75
    """

    semantic_tool_router: SemanticToolRouterConfig = Field(
        default_factory=SemanticToolRouterConfig
    )
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    adaptive_learning: AdaptiveLearningConfig = Field(
        default_factory=AdaptiveLearningConfig
    )
    context_budget: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    smart_task_router: SmartTaskRouterConfig = Field(
        default_factory=SmartTaskRouterConfig
    )
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)

    model_config = {"extra": "ignore"}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MendicantConfig":
        """
        Parse a ``MendicantConfig`` from a plain dict.

        Unrecognised top-level keys are silently ignored.  Sub-dicts are
        passed directly to the relevant sub-config model.

        Parameters
        ----------
        data : dict
            Mapping of section names to sub-config dicts.  Any or all
            sections may be omitted; defaults will be used for missing ones.

        Returns
        -------
        MendicantConfig

        Raises
        ------
        pydantic.ValidationError
            If any sub-config value fails validation.
        """
        return cls.model_validate(data)

    # ------------------------------------------------------------------
    # Convenience builders
    # ------------------------------------------------------------------

    def build_semantic_tool_router_middleware(self):  # type: ignore[return]
        """Instantiate SemanticToolRouterMiddleware from this config."""
        from mendicant_core.middleware.semantic_tool_router import (
            SemanticToolRouterMiddleware,
        )

        return SemanticToolRouterMiddleware(
            registry_path=self.semantic_tool_router.registry_path,
            embedding_model=self.semantic_tool_router.embedding_model,
            top_k=self.semantic_tool_router.top_k,
            similarity_threshold=self.semantic_tool_router.similarity_threshold,
            inject_as_system_hint=self.semantic_tool_router.inject_as_system_hint,
        )

    def build_verification_middleware(self):  # type: ignore[return]
        """Instantiate QualityGateMiddleware from this config."""
        from mendicant_core.middleware.verification import QualityGateMiddleware

        return QualityGateMiddleware(
            enabled=self.verification.enabled,
            model=self.verification.model,
            temperature=self.verification.temperature,
            min_score=self.verification.min_score,
            max_retries=self.verification.max_retries,
            timeout_seconds=self.verification.timeout_seconds,
            model_factory=None,
        )

    def build_adaptive_learning_middleware(self):  # type: ignore[return]
        """Instantiate AdaptiveLearningMiddleware from this config."""
        from mendicant_core.middleware.adaptive_learning import (
            AdaptiveLearningMiddleware,
        )

        return AdaptiveLearningMiddleware(
            store_path=self.adaptive_learning.store_path,
            max_patterns=self.adaptive_learning.max_patterns,
            min_success_rate=self.adaptive_learning.min_success_rate,
            recommendation_window=self.adaptive_learning.recommendation_window,
        )

    def build_context_budget_middleware(self):  # type: ignore[return]
        """Instantiate ContextBudgetMiddleware from this config."""
        from mendicant_core.middleware.context_budget import ContextBudgetMiddleware

        return ContextBudgetMiddleware(
            default_budget=self.context_budget.default_budget,
            strategies=list(self.context_budget.strategies),
            system_message_budget_fraction=self.context_budget.system_message_budget_fraction,
        )

    def build_smart_task_router_middleware(self):  # type: ignore[return]
        """Instantiate SmartTaskRouterMiddleware from this config."""
        from mendicant_core.middleware.smart_task_router import (
            SmartTaskRouterMiddleware,
        )

        return SmartTaskRouterMiddleware(
            patterns_store_path=self.smart_task_router.patterns_store_path,
            embedding_model=self.smart_task_router.embedding_model,
            embedding_weight=self.smart_task_router.embedding_weight,
            min_embedding_similarity=self.smart_task_router.min_embedding_similarity,
        )
