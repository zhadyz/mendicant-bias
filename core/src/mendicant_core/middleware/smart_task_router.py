"""
smart_task_router.py
====================
Mendicant Bias V5 — FR5: Smart Task Router

A LangGraph AgentMiddleware that fires as a before_agent hook.  It inspects
the latest HumanMessage and classifies the task into one of five categories:

    SIMPLE           — short factual queries, greetings, lookups
    RESEARCH         — open-ended investigative or analytical tasks
    CODE_GENERATION  — write / generate new code artefacts
    CRITICAL_CODE    — security-sensitive, infrastructure, or production code
    MULTI_MODAL      — tasks involving image/audio/video processing

Classification uses a two-stage approach:
  1. Fast keyword heuristics (no ML deps required).
  2. Optional embedding similarity to historical patterns from the adaptive-
     learning store (``AdaptiveLearningMiddleware``), if sentence-transformers
     is available and patterns exist.

Based on the classification, three runtime flags are set in state:

    verification_enabled : bool   — run the QualityGateMiddleware?
    subagent_enabled     : bool   — spawn sub-agents for parallel work?
    thinking_enabled     : bool   — allow extended chain-of-thought?

These flags are read by downstream middleware and the Mendicant Bias planner.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, NotRequired, override

from langchain_core.messages import BaseMessage, HumanMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task type enum (strings so they survive JSON serialisation)
# ---------------------------------------------------------------------------

SIMPLE = "SIMPLE"
RESEARCH = "RESEARCH"
CODE_GENERATION = "CODE_GENERATION"
CRITICAL_CODE = "CRITICAL_CODE"
MULTI_MODAL = "MULTI_MODAL"

_ALL_TYPES = [SIMPLE, RESEARCH, CODE_GENERATION, CRITICAL_CODE, MULTI_MODAL]

# ---------------------------------------------------------------------------
# Keyword dictionaries for heuristic classification
# ---------------------------------------------------------------------------

_KEYWORDS: dict[str, list[str]] = {
    CRITICAL_CODE: [
        "sql injection", "authentication", "authoris", "authoriz", "csrf", "xss",
        "encryption", "decrypt", "private key", "secret", "password", "oauth",
        "jwt", "tls", "firewall", "iam role", "infra", "terraform", "kubernetes",
        "k8s", "production", "deploy", "ci/cd", "pipeline", "dockerfile",
        "docker-compose", "cloud run", "aws lambda", "security",
    ],
    MULTI_MODAL: [
        "image", "photo", "picture", "screenshot", "diagram", "chart", "graph",
        "video", "audio", "speech", "transcribe", "vision", "ocr", "pdf",
        "document", "sketch", "draw", "render", "visualis", "visualiz",
    ],
    CODE_GENERATION: [
        "write a", "generate", "create a", "implement", "code", "function",
        "class", "module", "script", "program", "api", "endpoint", "schema",
        "boilerplate", "template", "stub", "scaffold", "refactor", "unit test",
        "pytest", "unittest", "fixture",
    ],
    RESEARCH: [
        "research", "analyse", "analyze", "explain", "compare", "contrast",
        "summarise", "summarize", "survey", "overview", "what is", "how does",
        "why does", "when did", "literature", "study", "investigate",
        "history of", "evolution of", "trends in",
    ],
    # SIMPLE is the default fallback — no keyword list needed
}

# Priority order for heuristic matching (most specific first)
_PRIORITY = [CRITICAL_CODE, MULTI_MODAL, CODE_GENERATION, RESEARCH, SIMPLE]

# ---------------------------------------------------------------------------
# Runtime flags per task type
# ---------------------------------------------------------------------------

_FLAGS: dict[str, dict[str, bool]] = {
    SIMPLE: {
        "verification_enabled": False,
        "subagent_enabled": False,
        "thinking_enabled": False,
    },
    RESEARCH: {
        "verification_enabled": True,
        "subagent_enabled": True,
        "thinking_enabled": True,
    },
    CODE_GENERATION: {
        "verification_enabled": True,
        "subagent_enabled": False,
        "thinking_enabled": True,
    },
    CRITICAL_CODE: {
        "verification_enabled": True,
        "subagent_enabled": False,
        "thinking_enabled": True,
    },
    MULTI_MODAL: {
        "verification_enabled": False,
        "subagent_enabled": True,
        "thinking_enabled": False,
    },
}

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class SmartTaskRouterState(AgentState):
    """Extended agent state carrying task-routing metadata."""

    task_type: NotRequired[str | None]
    verification_enabled: NotRequired[bool | None]
    subagent_enabled: NotRequired[bool | None]
    thinking_enabled: NotRequired[bool | None]
    routing_metadata: NotRequired[dict[str, Any] | None]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SmartTaskRouterMiddleware(AgentMiddleware[SmartTaskRouterState]):
    """
    Task classifier and runtime flag setter.

    Parameters
    ----------
    patterns_store_path : str
        Path to adaptive-learning pattern store for embedding-assisted
        classification.  Pass ``None`` to disable.
    embedding_model : str
        Sentence-transformer model name (default: ``all-MiniLM-L6-v2``).
    embedding_weight : float
        Weight (0–1) given to embedding similarity vs. keyword heuristic
        confidence when both are available.  Default: 0.5.
    min_embedding_similarity : float
        Minimum cosine similarity for an embedding suggestion to be
        considered.  Default: 0.55.
    """

    state_schema = SmartTaskRouterState

    def __init__(
        self,
        *,
        patterns_store_path: str | None = ".mendicant/orchestration_patterns.json",
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_weight: float = 0.5,
        min_embedding_similarity: float = 0.55,
    ) -> None:
        super().__init__()
        self.patterns_store_path = (
            Path(patterns_store_path) if patterns_store_path else None
        )
        self.embedding_model = embedding_model
        self.embedding_weight = embedding_weight
        self.min_embedding_similarity = min_embedding_similarity

        self._encoder: Any | None = None
        self._encoder_loaded: bool = False

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def before_agent(
        self, state: SmartTaskRouterState, runtime: Runtime
    ) -> dict | None:
        """Classify task and set runtime flags."""
        ctx = runtime.context or {}
        messages: list[BaseMessage] = state.get("messages", [])
        task_text = self._extract_task_text(messages)

        if not task_text:
            return None

        # Stage 1 — keyword heuristic
        keyword_type, keyword_conf = self._classify_keywords(task_text)

        # Stage 2 — optional embedding similarity
        embedding_type: str | None = None
        embedding_sim: float = 0.0
        try:
            embedding_type, embedding_sim = self._classify_embedding(task_text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SmartRouter] Embedding classification failed: %s", exc)

        # Blend
        final_type = self._blend(
            keyword_type, keyword_conf, embedding_type, embedding_sim
        )
        flags = _FLAGS[final_type]

        logger.info(
            "[SmartRouter] task_type=%s kw=%s(%.2f) emb=%s(%.2f)",
            final_type,
            keyword_type,
            keyword_conf,
            embedding_type,
            embedding_sim,
        )

        return {
            "task_type": final_type,
            "verification_enabled": flags["verification_enabled"],
            "subagent_enabled": flags["subagent_enabled"],
            "thinking_enabled": flags["thinking_enabled"],
            "routing_metadata": {
                "task_text_preview": task_text[:120],
                "keyword_classification": keyword_type,
                "keyword_confidence": round(keyword_conf, 3),
                "embedding_classification": embedding_type,
                "embedding_similarity": round(embedding_sim, 3),
                "final_type": final_type,
            },
        }

    @override
    async def abefore_agent(
        self, state: SmartTaskRouterState, runtime: Runtime
    ) -> dict | None:
        """Async variant — delegates to synchronous implementation."""
        return self.before_agent(state, runtime)

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def _classify_keywords(self, text: str) -> tuple[str, float]:
        """
        Return (task_type, confidence) from keyword matching.

        Confidence is 1.0 when multiple keywords match, scaled down for
        single-match cases.
        """
        lower = text.lower()
        scores: dict[str, int] = {t: 0 for t in _ALL_TYPES}

        for task_type, kws in _KEYWORDS.items():
            for kw in kws:
                if kw in lower:
                    scores[task_type] += 1

        # Apply priority ordering for tie-breaking
        best_type = SIMPLE
        best_score = 0
        for t in _PRIORITY:
            if scores[t] > best_score:
                best_score = scores[t]
                best_type = t

        # Confidence: 0.6 for 1 match, 0.8 for 2, 0.95 for 3+
        if best_score == 0:
            confidence = 0.5  # pure default
        elif best_score == 1:
            confidence = 0.6
        elif best_score == 2:
            confidence = 0.8
        else:
            confidence = 0.95

        return best_type, confidence

    def _classify_embedding(self, text: str) -> tuple[str | None, float]:
        """
        Return (task_type, similarity) by finding the nearest historical
        pattern and returning its type.  Returns (None, 0.0) if not available.
        """
        if self.patterns_store_path is None or not self.patterns_store_path.exists():
            return None, 0.0

        encoder = self._load_encoder()
        if encoder is None:
            return None, 0.0

        query_vec = encoder.encode(text, convert_to_numpy=True).tolist()
        patterns = self._load_patterns()
        if not patterns:
            return None, 0.0

        best_type: str | None = None
        best_sim = 0.0
        for p in patterns:
            p_emb: list[float] | None = p.get("embedding")
            if not p_emb:
                continue
            sim = _cosine_similarity(query_vec, p_emb)
            if sim > best_sim:
                best_sim = sim
                best_type = p.get("task_type")

        if best_sim < self.min_embedding_similarity:
            return None, best_sim
        return best_type, best_sim

    def _blend(
        self,
        kw_type: str,
        kw_conf: float,
        emb_type: str | None,
        emb_sim: float,
    ) -> str:
        """
        Combine keyword and embedding classifications.

        If embedding is not available or below threshold, use keyword result.
        Otherwise weight the two signals.
        """
        if emb_type is None or emb_sim < self.min_embedding_similarity:
            return kw_type

        if kw_type == emb_type:
            return kw_type

        # Different signals: choose based on weighted confidence
        kw_weight = (1.0 - self.embedding_weight) * kw_conf
        emb_weight = self.embedding_weight * emb_sim

        if kw_weight >= emb_weight:
            return kw_type
        return emb_type

    # ------------------------------------------------------------------
    # Encoder / pattern loading
    # ------------------------------------------------------------------

    def _load_encoder(self) -> Any:
        if self._encoder_loaded:
            return self._encoder
        self._encoder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._encoder = SentenceTransformer(self.embedding_model)
        except ImportError:
            self._encoder = None
        return self._encoder

    def _load_patterns(self) -> list[dict]:
        if self.patterns_store_path is None:
            return []
        try:
            with self.patterns_store_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_task_text(messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        p.get("text", "") for p in content if isinstance(p, dict)
                    ]
                    return " ".join(parts).strip()
        return ""


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
