"""
smart_task_router.py
====================
Mendicant Bias V5 — FR5: Smart Task Router

Classifies tasks into five categories using a three-stage approach:

  1. Embedding similarity to labeled reference examples (primary — accurate)
  2. Keyword heuristics with negative pattern suppression (fast fallback)
  3. Optional historical pattern matching from adaptive learning store

Based on the classification, three runtime flags are set:

    verification_enabled : bool
    subagent_enabled     : bool
    thinking_enabled     : bool
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
# Task types
# ---------------------------------------------------------------------------

SIMPLE = "SIMPLE"
RESEARCH = "RESEARCH"
CODE_GENERATION = "CODE_GENERATION"
CRITICAL_CODE = "CRITICAL_CODE"
MULTI_MODAL = "MULTI_MODAL"

_ALL_TYPES = [SIMPLE, RESEARCH, CODE_GENERATION, CRITICAL_CODE, MULTI_MODAL]

# ---------------------------------------------------------------------------
# Labeled reference examples for embedding-based classification
# ---------------------------------------------------------------------------

_REFERENCE_EXAMPLES: dict[str, list[str]] = {
    SIMPLE: [
        "what is the capital of France",
        "hello",
        "thanks",
        "what does this error mean",
        "how many lines in this file",
        "what version of python am I using",
        "show me the git log",
        "list the files in src",
        "what is the current branch",
        "read the README",
        "how old is this project",
        "who wrote this function",
        "what does ECONNREFUSED mean",
        "tell me about this codebase",
        "check the git status",
    ],
    CODE_GENERATION: [
        "fix the bug in the login form",
        "add dark mode to the app",
        "write a function that validates emails",
        "refactor the user service to use dependency injection",
        "add rate limiting to the API endpoints",
        "write a migration script for postgres",
        "optimize the database queries they are slow",
        "generate a README for this project",
        "add error handling to the payment flow",
        "create a utility function for date formatting",
        "fix the broken tests in the auth module",
        "add pagination to the user list endpoint",
        "write unit tests for the cart service",
        "add a loading spinner to the dashboard",
        "update the API to return proper error codes",
        "add dark mode to the app",
        "add a search feature to the sidebar",
        "add caching to the API responses",
        "fix the broken CSS on mobile",
        "add a logout button to the header",
        "update the dependencies to latest versions",
    ],
    CRITICAL_CODE: [
        "build a REST API with JWT authentication",
        "set up github actions for CI/CD",
        "review this PR for security issues",
        "create a websocket server with authentication",
        "add CSRF protection to all forms",
        "set up SSL certificates for the domain",
        "write the terraform config for AWS deployment",
        "configure kubernetes secrets management",
        "add input sanitization to prevent SQL injection",
        "implement OAuth2 login with Google",
        "set up the production deployment pipeline",
        "add encryption for user data at rest",
        "configure the firewall rules",
        "write the docker-compose for production",
        "add rate limiting and DDoS protection",
        "deploy the app to production",
        "push this to staging",
        "release version 2.0",
    ],
    RESEARCH: [
        "research quantum error correction techniques",
        "compare React and Vue for our use case",
        "analyze the performance bottlenecks in our app",
        "summarize the latest AI research papers",
        "investigate why the memory usage is growing",
        "study the competition and their features",
        "deep dive into the authentication architecture",
        "explain the trade-offs between SQL and NoSQL",
        "review the codebase architecture and suggest improvements",
        "analyze our API response times over the last month",
        "investigate the root cause of the production outage",
        "compare different caching strategies for our workload",
        "survey best practices for microservice communication",
        "examine the test coverage and identify gaps",
        "evaluate whether we should migrate to TypeScript",
    ],
    MULTI_MODAL: [
        "design a responsive landing page with animations",
        "create a dashboard UI with charts and graphs",
        "build a drag and drop file upload component",
        "design the settings page layout",
        "create a mobile-responsive navigation bar",
        "add a profile picture upload with cropping",
        "design a notification toast component",
        "build an interactive data visualization",
        "create a responsive email template",
        "design the onboarding flow with illustrations",
        "add a color picker to the theme settings",
        "create a responsive grid layout for the gallery",
        "design a modal dialog with form validation",
        "build a chart showing user growth metrics",
        "create an animated loading skeleton",
    ],
}

# ---------------------------------------------------------------------------
# Keyword heuristics (fast path + fallback when no embeddings)
# ---------------------------------------------------------------------------

_KEYWORDS: dict[str, list[str]] = {
    CRITICAL_CODE: [
        "sql injection", "authentication", "authoris", "authoriz", "csrf", "xss",
        "encryption", "decrypt", "private key", "secret", "password", "oauth",
        "jwt", "tls", "firewall", "iam role", "infra", "terraform", "kubernetes",
        "k8s", "production deploy", "ci/cd", "pipeline", "dockerfile",
        "docker-compose", "cloud run", "aws lambda", "security review",
        "ssl cert", "secrets management",
    ],
    MULTI_MODAL: [
        "design a", "responsive", "landing page", "ui component",
        "ux design", "layout", "css animation", "figma", "mockup", "wireframe",
        "dashboard ui", "drag and drop", "color picker", "data visualization",
        "photo", "screenshot", "video", "audio", "ocr",
    ],
    CODE_GENERATION: [
        "fix the", "fix this", "fix bug", "add a ", "add the ",
        "write a function", "write a class", "write a test", "write code",
        "create a function", "create a class", "create a module",
        "create an api", "create a server", "implement",
        "refactor", "optimize", "add error handling",
        "add pagination", "unit test", "migration script",
        "generate a readme", "update the api",
    ],
    RESEARCH: [
        "research", "analyse", "analyze", "compare", "investigate",
        "summarize", "summarise", "survey", "evaluate whether",
        "deep dive", "study", "examine", "review the architecture",
        "root cause", "bottleneck",
    ],
}

_NEGATIVE_PATTERNS: dict[str, list[str]] = {
    CODE_GENERATION: [
        "what is a", "what is the", "what are", "who is",
        "how many", "how much", "capital of", "explain",
        "tell me about", "do you know", "define ",
    ],
    CRITICAL_CODE: [
        "what is authentication", "what is oauth", "what is jwt",
        "explain encryption", "explain tls", "explain how",
        "how does oauth work", "what does", "tell me about",
    ],
}

_PRIORITY = [CRITICAL_CODE, MULTI_MODAL, CODE_GENERATION, RESEARCH, SIMPLE]

# ---------------------------------------------------------------------------
# Runtime flags
# ---------------------------------------------------------------------------

_FLAGS: dict[str, dict[str, bool]] = {
    SIMPLE: {"verification_enabled": False, "subagent_enabled": False, "thinking_enabled": False},
    RESEARCH: {"verification_enabled": True, "subagent_enabled": True, "thinking_enabled": True},
    CODE_GENERATION: {"verification_enabled": True, "subagent_enabled": False, "thinking_enabled": True},
    CRITICAL_CODE: {"verification_enabled": True, "subagent_enabled": False, "thinking_enabled": True},
    MULTI_MODAL: {"verification_enabled": False, "subagent_enabled": True, "thinking_enabled": False},
}

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class SmartTaskRouterState(AgentState):
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
    Three-stage task classifier:
      1. Embedding similarity to labeled examples (primary)
      2. Keyword heuristics (fast fallback)
      3. Historical pattern matching (optional boost)
    """

    state_schema = SmartTaskRouterState

    def __init__(
        self,
        *,
        patterns_store_path: str | None = ".mendicant/orchestration_patterns.json",
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_weight: float = 0.6,
        min_embedding_similarity: float = 0.45,
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
        self._ref_embeddings: dict[str, list[list[float]]] | None = None

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def before_agent(
        self, state: SmartTaskRouterState, runtime: Runtime
    ) -> dict | None:
        ctx = runtime.context or {}
        messages: list[BaseMessage] = state.get("messages", [])
        task_text = self._extract_task_text(messages)

        if not task_text:
            return None

        # Stage 1 — embedding classification against labeled examples
        emb_type, emb_conf = self._classify_by_examples(task_text)

        # Stage 2 — keyword heuristics (fast, always available)
        kw_type, kw_conf = self._classify_keywords(task_text)

        # Stage 3 — optional historical pattern matching
        hist_type: str | None = None
        hist_sim: float = 0.0
        try:
            hist_type, hist_sim = self._classify_from_history(task_text)
        except Exception:
            pass

        # Blend all signals
        final_type = self._blend_all(
            emb_type, emb_conf, kw_type, kw_conf, hist_type, hist_sim
        )
        flags = _FLAGS[final_type]

        logger.info(
            "[SmartRouter] task_type=%s emb=%s(%.2f) kw=%s(%.2f) hist=%s(%.2f)",
            final_type, emb_type, emb_conf, kw_type, kw_conf, hist_type, hist_sim,
        )

        return {
            "task_type": final_type,
            "verification_enabled": flags["verification_enabled"],
            "subagent_enabled": flags["subagent_enabled"],
            "thinking_enabled": flags["thinking_enabled"],
            "routing_metadata": {
                "task_text_preview": task_text[:120],
                "embedding_classification": emb_type,
                "embedding_confidence": round(emb_conf, 3),
                "keyword_classification": kw_type,
                "keyword_confidence": round(kw_conf, 3),
                "history_classification": hist_type,
                "history_similarity": round(hist_sim, 3),
                "final_type": final_type,
            },
        }

    @override
    async def abefore_agent(
        self, state: SmartTaskRouterState, runtime: Runtime
    ) -> dict | None:
        return self.before_agent(state, runtime)

    # ------------------------------------------------------------------
    # Stage 1: Embedding classification against labeled examples
    # ------------------------------------------------------------------

    def _classify_by_examples(self, text: str) -> tuple[str | None, float]:
        """
        Embed the query and compare against pre-labeled reference examples.
        Returns (task_type, confidence). Returns (None, 0.0) if encoder
        unavailable.
        """
        encoder = self._load_encoder()
        if encoder is None:
            return None, 0.0

        # Lazy-build reference embeddings
        if self._ref_embeddings is None:
            self._ref_embeddings = {}
            for task_type, examples in _REFERENCE_EXAMPLES.items():
                vecs = encoder.encode(examples, convert_to_numpy=True).tolist()
                self._ref_embeddings[task_type] = vecs
            logger.debug("[SmartRouter] Built reference embeddings for %d types", len(self._ref_embeddings))

        query_vec = encoder.encode(text, convert_to_numpy=True).tolist()

        # Find the best match across all types
        best_type: str | None = None
        best_sim = 0.0
        type_scores: dict[str, float] = {}

        for task_type, ref_vecs in self._ref_embeddings.items():
            # Average of top-3 similarities (more robust than single best)
            sims = sorted(
                [_cosine_similarity(query_vec, rv) for rv in ref_vecs],
                reverse=True,
            )
            avg_top3 = sum(sims[:3]) / min(3, len(sims)) if sims else 0.0
            type_scores[task_type] = avg_top3

            if avg_top3 > best_sim:
                best_sim = avg_top3
                best_type = task_type

        if best_sim < self.min_embedding_similarity:
            return None, best_sim

        # Confidence = how much the best type stands out from the runner-up
        sorted_scores = sorted(type_scores.values(), reverse=True)
        if len(sorted_scores) >= 2:
            margin = sorted_scores[0] - sorted_scores[1]
            # Scale margin [0, 0.2] -> confidence [0.6, 0.95]
            confidence = min(0.95, 0.6 + margin * 1.75)
        else:
            confidence = best_sim

        return best_type, confidence

    # ------------------------------------------------------------------
    # Stage 2: Keyword heuristics (fast fallback)
    # ------------------------------------------------------------------

    def _classify_keywords(self, text: str) -> tuple[str, float]:
        """Keyword matching with negative pattern suppression."""
        lower = text.lower()
        scores: dict[str, int] = {t: 0 for t in _ALL_TYPES}

        for task_type, kws in _KEYWORDS.items():
            for kw in kws:
                if kw in lower:
                    scores[task_type] += 1

        # Suppress false positives
        for task_type, neg_patterns in _NEGATIVE_PATTERNS.items():
            if scores.get(task_type, 0) > 0:
                for neg in neg_patterns:
                    if neg in lower:
                        scores[task_type] = 0
                        break

        best_type = SIMPLE
        best_score = 0
        for t in _PRIORITY:
            if scores[t] > best_score:
                best_score = scores[t]
                best_type = t

        if best_score == 0:
            confidence = 0.3
        elif best_score == 1:
            confidence = 0.5
        elif best_score == 2:
            confidence = 0.65
        else:
            confidence = 0.8

        return best_type, confidence

    # ------------------------------------------------------------------
    # Stage 3: Historical pattern matching
    # ------------------------------------------------------------------

    def _classify_from_history(self, text: str) -> tuple[str | None, float]:
        """Match against historical patterns from adaptive learning store."""
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
            p_emb = p.get("embedding")
            if not p_emb:
                continue
            sim = _cosine_similarity(query_vec, p_emb)
            if sim > best_sim:
                best_sim = sim
                best_type = p.get("task_type")

        if best_sim < self.min_embedding_similarity:
            return None, best_sim
        return best_type, best_sim

    # ------------------------------------------------------------------
    # Blending
    # ------------------------------------------------------------------

    def _blend_all(
        self,
        emb_type: str | None, emb_conf: float,
        kw_type: str, kw_conf: float,
        hist_type: str | None, hist_sim: float,
    ) -> str:
        """Combine all three classification signals."""
        # If embedding classifier has a clear answer, trust it
        if emb_type is not None and emb_conf >= 0.6:
            # If keywords agree, even better
            if kw_type == emb_type:
                return emb_type
            # If keywords disagree but embedding is strong, trust embedding
            if emb_conf >= 0.7:
                return emb_type
            # Weak embedding + keyword disagreement: weight them
            emb_w = self.embedding_weight * emb_conf
            kw_w = (1.0 - self.embedding_weight) * kw_conf
            if emb_w >= kw_w:
                return emb_type

        # Historical patterns can boost a weak signal
        if hist_type is not None and hist_sim >= 0.7:
            if emb_type == hist_type or kw_type == hist_type:
                return hist_type

        # If embedding had a result (even weak), prefer it over SIMPLE keyword
        if emb_type is not None and emb_conf >= self.min_embedding_similarity:
            if kw_type == SIMPLE:
                return emb_type

        # Fall back to keywords
        return kw_type

    # ------------------------------------------------------------------
    # Encoder / pattern loading
    # ------------------------------------------------------------------

    def _load_encoder(self) -> Any:
        if self._encoder_loaded:
            return self._encoder
        self._encoder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer

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
        except Exception:
            return []

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
