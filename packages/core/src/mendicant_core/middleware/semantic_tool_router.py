"""
semantic_tool_router.py
=======================
Mendicant Bias V5 — FR1: Semantic Tool Selection

A LangGraph AgentMiddleware that fires as a before_agent hook. It embeds the
latest user message with sentence-transformers (all-MiniLM-L6-v2), compares
the embedding against a JSON tool registry using cosine similarity, groups
results by domain, and injects the top-scoring tool names into AgentState so
the agent's system prompt / tool filter can surface only the relevant tools.

Falls back to returning *all* tool names when:
  - The sentence-transformers encoder cannot be loaded.
  - No tool scores exceed the confidence threshold.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any, NotRequired, override

from langchain_core.messages import BaseMessage, HumanMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class SemanticToolRouterState(AgentState):
    """Extended agent state carrying semantic-routing metadata."""

    selected_tools: NotRequired[list[str] | None]
    tool_scores: NotRequired[dict[str, float] | None]
    router_metadata: NotRequired[dict[str, Any] | None]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SemanticToolRouterMiddleware(AgentMiddleware[SemanticToolRouterState]):
    """
    Semantic tool selection middleware.

    On every agent turn this middleware:
    1. Extracts the most recent human message from state.
    2. Embeds it with a sentence-transformer model.
    3. Scores each tool in the registry via cosine similarity.
    4. Groups tools by domain and selects the top-K per domain that exceed
       the confidence threshold.
    5. Returns a state delta so downstream nodes know which tools to expose.

    Parameters
    ----------
    registry_path : str
        Path to the JSON tool registry (default: ``.mendicant/tool_registry.json``).
    model_name : str
        Sentence-transformers model name for encoding (default: ``all-MiniLM-L6-v2``).
    confidence_threshold : float
        Minimum cosine similarity score to include a tool (default: ``0.3``).
    max_tools_per_domain : int
        Maximum number of tools to select from each domain (default: ``5``).
    fallback_all_on_error : bool
        Return all registered tool names when encoding fails (default: ``True``).
    """

    state_schema = SemanticToolRouterState

    def __init__(
        self,
        *,
        registry_path: str = ".mendicant/tool_registry.json",
        model_name: str = "all-MiniLM-L6-v2",
        confidence_threshold: float = 0.3,
        max_tools_per_domain: int = 5,
        fallback_all_on_error: bool = True,
    ) -> None:
        super().__init__()
        self.registry_path = registry_path
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.max_tools_per_domain = max_tools_per_domain
        self.fallback_all_on_error = fallback_all_on_error

        self._registry: dict[str, Any] | None = None
        self._encoder: Any | None = None  # SentenceTransformer instance or None
        self._encoder_loaded: bool = False

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def before_agent(
        self, state: SemanticToolRouterState, runtime: Runtime
    ) -> dict | None:
        """
        Synchronous before_agent hook.

        Embeds the latest user message and returns a state delta with
        ``selected_tools``, ``tool_scores``, and ``router_metadata``.
        """
        thread_id: str | None = (runtime.context or {}).get("thread_id")
        query = self._extract_latest_human_message(state)

        if not query:
            logger.debug("[SemanticToolRouter] No human message found; skipping.")
            return None

        registry = self._load_registry()
        if not registry:
            logger.warning(
                "[SemanticToolRouter] Empty or missing registry; skipping routing."
            )
            return None

        all_tool_names = [t["name"] for t in registry.get("tools", [])]

        # Attempt encoding
        try:
            query_embedding = self._encode_query(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SemanticToolRouter] Encoder failed (%s); falling back to all tools.",
                exc,
            )
            if self.fallback_all_on_error:
                return self._build_result(
                    selected=all_tool_names,
                    scores={n: 0.0 for n in all_tool_names},
                    metadata={
                        "fallback_reason": "encoder_error",
                        "thread_id": thread_id,
                    },
                )
            return None

        # Score tools
        tools = registry.get("tools", [])
        scored = self._select_tools(query_embedding, tools)

        if not scored:
            logger.info(
                "[SemanticToolRouter] No tools above threshold %.2f; falling back.",
                self.confidence_threshold,
            )
            if self.fallback_all_on_error:
                return self._build_result(
                    selected=all_tool_names,
                    scores={n: 0.0 for n in all_tool_names},
                    metadata={
                        "fallback_reason": "no_tools_above_threshold",
                        "threshold": self.confidence_threshold,
                        "thread_id": thread_id,
                    },
                )
            return None

        selected_names = [t["name"] for t in scored]
        scores_map = {t["name"]: t["_score"] for t in scored}

        return self._build_result(
            selected=selected_names,
            scores=scores_map,
            metadata={
                "query_length": len(query),
                "total_tools_in_registry": len(tools),
                "tools_above_threshold": len(scored),
                "threshold": self.confidence_threshold,
                "thread_id": thread_id,
                "model": self.model_name,
            },
        )

    @override
    async def abefore_agent(
        self, state: SemanticToolRouterState, runtime: Runtime
    ) -> dict | None:
        """Async variant — delegates to synchronous implementation."""
        return self.before_agent(state, runtime)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_latest_human_message(self, state: SemanticToolRouterState) -> str:
        """Return the content of the most recent HumanMessage in state.messages."""
        messages: list[BaseMessage] = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    # multimodal: extract text parts
                    parts = [
                        p["text"] for p in content if isinstance(p, dict) and "text" in p
                    ]
                    return " ".join(parts).strip()
        return ""

    def _load_registry(self) -> dict[str, Any]:
        """Load (and cache) the tool registry JSON from disk."""
        if self._registry is not None:
            return self._registry

        path = Path(self.registry_path)
        if not path.exists():
            logger.warning(
                "[SemanticToolRouter] Registry file not found: %s", path
            )
            self._registry = {}
            return self._registry

        try:
            with path.open("r", encoding="utf-8") as fh:
                self._registry = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[SemanticToolRouter] Failed to load registry: %s", exc
            )
            self._registry = {}

        return self._registry or {}

    def _load_encoder(self) -> Any:
        """Lazily load the SentenceTransformer encoder."""
        if self._encoder_loaded:
            return self._encoder
        self._encoder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._encoder = SentenceTransformer(self.model_name)
            logger.debug(
                "[SemanticToolRouter] Loaded encoder: %s", self.model_name
            )
        except ImportError:
            logger.warning(
                "[SemanticToolRouter] sentence-transformers not installed; "
                "semantic routing disabled."
            )
            self._encoder = None
        return self._encoder

    def _encode_query(self, query: str) -> list[float]:
        """
        Encode *query* into a unit-normalised embedding vector.

        Raises
        ------
        RuntimeError
            If the encoder cannot be loaded.
        """
        encoder = self._load_encoder()
        if encoder is None:
            raise RuntimeError("Encoder not available")
        embedding = encoder.encode(query, convert_to_numpy=True)
        return embedding.tolist()

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """
        Compute cosine similarity between two vectors.

        Returns a value in [-1, 1].  Returns 0.0 when either vector is
        the zero vector.
        """
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _select_tools(
        self, query_embedding: list[float], tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Score and select tools from the registry.

        Groups by domain, takes the top ``max_tools_per_domain`` per domain,
        and only includes tools whose cosine similarity exceeds
        ``confidence_threshold``.

        Returns
        -------
        list[dict]
            Tool records (each augmented with a ``_score`` key), sorted
            descending by score.
        """
        scored_tools: list[dict[str, Any]] = []

        for tool in tools:
            tool_embedding: list[float] | None = tool.get("embedding")
            if not tool_embedding:
                # Use description as fallback text embedding if encoder available
                description = tool.get("description", "")
                try:
                    tool_embedding = self._encode_query(description)
                except Exception:  # noqa: BLE001
                    continue

            score = self._cosine_similarity(query_embedding, tool_embedding)
            if score >= self.confidence_threshold:
                scored_tools.append({**tool, "_score": score})

        # Group by domain and cap per-domain
        domain_buckets: dict[str, list[dict]] = {}
        for tool in scored_tools:
            domain = tool.get("domain", "general")
            domain_buckets.setdefault(domain, []).append(tool)

        result: list[dict[str, Any]] = []
        for domain_tools in domain_buckets.values():
            domain_tools.sort(key=lambda t: t["_score"], reverse=True)
            result.extend(domain_tools[: self.max_tools_per_domain])

        result.sort(key=lambda t: t["_score"], reverse=True)
        return result

    @staticmethod
    def _build_result(
        selected: list[str],
        scores: dict[str, float],
        metadata: dict[str, Any],
    ) -> dict:
        """Package the selection result as a LangGraph state delta."""
        return {
            "selected_tools": selected,
            "tool_scores": scores,
            "router_metadata": metadata,
        }
