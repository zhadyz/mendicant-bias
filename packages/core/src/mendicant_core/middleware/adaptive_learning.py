"""
adaptive_learning.py
====================
Mendicant Bias V5 — FR3: Pattern Recorder & Strategy Recommender

A LangGraph AgentMiddleware that fires as an after_agent hook.  Each time the
agent completes, this middleware records a pattern entry to a JSON store at
``.mendicant/orchestration_patterns.json``.

Pattern entries capture:
  - Task text embedding (all-MiniLM-L6-v2 if available, else None)
  - Task type (SIMPLE / RESEARCH / CODE_GENERATION / CRITICAL_CODE / MULTI_MODAL)
  - Strategy tags (list of strings)
  - Tools used
  - Wall-clock duration (seconds)
  - Outcome and verification verdict
  - ISO 8601 timestamp

The ``recommend_strategy()`` method accepts a query string, embeds it, and
returns the top-N most similar historical patterns for use by the planner.

Implementation notes
--------------------
- Atomic file writes via temp-file + os.replace to avoid corruption.
- Records are pruned to at most 1000 entries (oldest first).
- Embedding failures are silently tolerated; entries are stored without an
  embedding and are excluded from similarity search.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NotRequired, override

from langchain_core.messages import BaseMessage, HumanMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_STORE_PATH = ".mendicant/orchestration_patterns.json"
_MAX_RECORDS = 1000
_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_TOP_N = 5
_MIN_SIMILARITY = 0.3

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class AdaptiveLearningState(AgentState):
    """Extended agent state carrying adaptive-learning metadata."""

    task_start_time: NotRequired[float | None]
    task_type: NotRequired[str | None]
    strategy_tags: NotRequired[list[str] | None]
    learning_metadata: NotRequired[dict[str, Any] | None]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class AdaptiveLearningMiddleware(AgentMiddleware[AdaptiveLearningState]):
    """
    Pattern recorder and strategy recommender middleware.

    Parameters
    ----------
    store_path : str
        Path to the JSON pattern store (default: ``.mendicant/orchestration_patterns.json``).
    model_name : str
        Sentence-transformer model for embedding generation.
    max_records : int
        Maximum number of patterns to retain (default: 1000).
    top_n : int
        Default number of recommendations returned by ``recommend_strategy``.
    min_similarity : float
        Minimum cosine similarity for a pattern to appear in recommendations.
    """

    state_schema = AdaptiveLearningState

    def __init__(
        self,
        *,
        store_path: str = _DEFAULT_STORE_PATH,
        model_name: str = _DEFAULT_MODEL,
        max_records: int = _MAX_RECORDS,
        top_n: int = _DEFAULT_TOP_N,
        min_similarity: float = _MIN_SIMILARITY,
    ) -> None:
        super().__init__()
        self.store_path = Path(store_path)
        self.model_name = model_name
        self.max_records = max_records
        self.top_n = top_n
        self.min_similarity = min_similarity

        self._encoder: Any | None = None
        self._encoder_loaded: bool = False

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def after_agent(
        self, state: AdaptiveLearningState, runtime: Runtime
    ) -> dict | None:
        """
        Record the completed task pattern.

        Returns a state delta with ``learning_metadata`` populated.
        """
        ctx = runtime.context or {}
        thread_id: str | None = ctx.get("thread_id")

        messages: list[BaseMessage] = state.get("messages", [])
        task_text = self._extract_task_text(messages)
        task_type: str = state.get("task_type") or ctx.get("task_type", "SIMPLE")
        strategy_tags: list[str] = state.get("strategy_tags") or ctx.get("strategy_tags", [])
        tools_used: list[str] = self._extract_tools_used(messages)
        duration: float = self._compute_duration(state)
        outcome: str = ctx.get("task_outcome", "success")
        verdict: str | None = state.get("verification_verdict")

        # Generate embedding (best-effort)
        embedding: list[float] | None = None
        try:
            embedding = self._encode(task_text) if task_text else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AdaptiveLearning] Embedding failed: %s", exc)

        pattern: dict[str, Any] = {
            "id": self._make_id(),
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "thread_id": thread_id,
            "task_text": task_text[:500] if task_text else "",
            "embedding": embedding,
            "task_type": task_type,
            "strategy_tags": strategy_tags,
            "tools_used": tools_used,
            "duration_seconds": round(duration, 3),
            "outcome": outcome,
            "verification_verdict": verdict,
        }

        self._append_pattern(pattern)

        return {
            "learning_metadata": {
                "pattern_id": pattern["id"],
                "recorded": True,
                "task_type": task_type,
                "duration": duration,
            }
        }

    @override
    async def aafter_agent(
        self, state: AdaptiveLearningState, runtime: Runtime
    ) -> dict | None:
        """Async variant — delegates to synchronous implementation."""
        return self.after_agent(state, runtime)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend_strategy(
        self, query: str, *, top_n: int | None = None, task_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Return the top-N historically similar patterns for *query*.

        Parameters
        ----------
        query : str
            Natural-language description of the current task.
        top_n : int | None
            Number of results to return.  Defaults to ``self.top_n``.
        task_type : str | None
            Optional filter; if supplied only patterns with this task_type
            are considered.

        Returns
        -------
        list[dict]
            Each dict contains the full pattern record plus a ``_similarity``
            key with the cosine score.  Sorted descending by similarity.
        """
        n = top_n or self.top_n
        patterns = self._load_patterns()
        if not patterns:
            return []

        # Filter by task_type
        if task_type:
            patterns = [p for p in patterns if p.get("task_type") == task_type]

        # Embed query
        try:
            query_embedding = self._encode(query)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AdaptiveLearning] Query embedding failed: %s", exc)
            # Fall back: return most recent patterns
            return [dict(p, _similarity=0.0) for p in reversed(patterns[-n:])]

        # Score
        scored: list[dict[str, Any]] = []
        for p in patterns:
            p_emb: list[float] | None = p.get("embedding")
            if not p_emb:
                continue
            sim = _cosine_similarity(query_embedding, p_emb)
            if sim >= self.min_similarity:
                scored.append(dict(p, _similarity=sim))

        scored.sort(key=lambda x: x["_similarity"], reverse=True)
        return scored[:n]

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics about the pattern store."""
        patterns = self._load_patterns()
        if not patterns:
            return {"total": 0}

        type_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        total_duration = 0.0
        for p in patterns:
            tt = p.get("task_type", "unknown")
            oc = p.get("outcome", "unknown")
            type_counts[tt] = type_counts.get(tt, 0) + 1
            outcome_counts[oc] = outcome_counts.get(oc, 0) + 1
            total_duration += p.get("duration_seconds", 0.0)

        return {
            "total": len(patterns),
            "task_types": type_counts,
            "outcomes": outcome_counts,
            "avg_duration_seconds": round(total_duration / len(patterns), 3),
        }

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _load_patterns(self) -> list[dict[str, Any]]:
        """Load all patterns from the JSON store.  Returns [] on any error."""
        if not self.store_path.exists():
            return []
        try:
            with self.store_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001
            logger.error("[AdaptiveLearning] Failed to load patterns: %s", exc)
            return []

    def _append_pattern(self, pattern: dict[str, Any]) -> None:
        """
        Append *pattern* to the store atomically.

        Prunes to ``max_records`` entries if needed.
        """
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        patterns = self._load_patterns()
        patterns.append(pattern)

        # Prune oldest records
        if len(patterns) > self.max_records:
            patterns = patterns[-self.max_records :]

        self._atomic_write(patterns)

    def _atomic_write(self, patterns: list[dict[str, Any]]) -> None:
        """Write patterns to disk via a temp file + os.replace (atomic on POSIX)."""
        dir_ = self.store_path.parent
        dir_.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".json.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(patterns, fh, indent=2, default=str)
            os.replace(tmp_path, str(self.store_path))
        except Exception as exc:  # noqa: BLE001
            logger.error("[AdaptiveLearning] Atomic write failed: %s", exc)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    def _load_encoder(self) -> Any:
        """Lazily load the SentenceTransformer."""
        if self._encoder_loaded:
            return self._encoder
        self._encoder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._encoder = SentenceTransformer(self.model_name)
        except ImportError:
            logger.debug(
                "[AdaptiveLearning] sentence-transformers not installed; "
                "embeddings disabled."
            )
            self._encoder = None
        return self._encoder

    def _encode(self, text: str) -> list[float]:
        """Encode *text* into a float list.  Raises RuntimeError if unavailable."""
        encoder = self._load_encoder()
        if encoder is None:
            raise RuntimeError("sentence-transformers not available")
        return encoder.encode(text, convert_to_numpy=True).tolist()

    # ------------------------------------------------------------------
    # Extraction helpers
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

    @staticmethod
    def _extract_tools_used(messages: list[BaseMessage]) -> list[str]:
        """Collect unique tool names from all AIMessage tool_calls."""
        from langchain_core.messages import AIMessage

        seen: set[str] = set()
        tools: list[str] = []
        for msg in messages:
            if isinstance(msg, AIMessage):
                for tc in getattr(msg, "tool_calls", None) or []:
                    name = (
                        tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    )
                    if name and name not in seen:
                        seen.add(name)
                        tools.append(name)
        return tools

    @staticmethod
    def _compute_duration(state: AdaptiveLearningState) -> float:
        """Compute wall-clock duration using state.task_start_time."""
        start: float | None = state.get("task_start_time")
        if start is None:
            return 0.0
        return max(0.0, time.monotonic() - start)

    @staticmethod
    def _make_id() -> str:
        """Generate a simple time-based ID."""
        return f"pat_{int(time.time() * 1000)}"


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two equal-length float lists."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
