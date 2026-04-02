"""
Mendicant Bias V5 — Context Optimizer (Evolved FR4)

Semantic value ranking for context management. Complements Claude Code's
native compaction (which only does age-based deletion) by adding:

1. Semantic relevance scoring (embedding similarity to current query)
2. Recency weighting (newer = more important)
3. Role-based weighting (system > user > tool > assistant for context)
4. Priority-based compression (compress lowest-priority first)
5. Result summarization (extract key facts before deletion)

Claude Code compacts by deleting the oldest tool results wholesale.
This module instead *ranks every message by importance* and compresses the
lowest-value messages first, preserving semantically relevant context
regardless of age.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token counting — tiktoken when available, word-split fallback
# ---------------------------------------------------------------------------

_ENC = None

try:
    import tiktoken as _tiktoken

    _ENC = _tiktoken.get_encoding("cl100k_base")
except ImportError:
    _tiktoken = None  # type: ignore[assignment]


def _count_tokens(text: str) -> int:
    """Count tokens via tiktoken, or fall back to word-count heuristic."""
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text.split()))


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors without numpy."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Role weight constants
# ---------------------------------------------------------------------------

_ROLE_WEIGHTS: dict[str, float] = {
    "system": 1.0,
    "user": 0.8,
    "tool": 0.5,
    "assistant": 0.3,
}

# ---------------------------------------------------------------------------
# Compression strategies (FR4-derived)
# ---------------------------------------------------------------------------

_TRUNCATION_PREVIEW = 300
_SUMMARY_EXCERPT = 200


def _strategy_key_fields(content: str, token_count: int) -> str | None:
    """Extract top-level keys and first values from JSON content."""
    if len(content) <= _TRUNCATION_PREVIEW:
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(data, dict):
        keys = list(data.keys())[:10]
        preview_items = []
        for k in keys[:5]:
            val = data[k]
            val_str = str(val)[:80]
            preview_items.append(f"  {k}: {val_str}")
        summary = (
            f"[JSON COMPRESSED — {token_count} tokens, {len(keys)} keys]\n"
            + "\n".join(preview_items)
        )
        if len(keys) > 5:
            summary += f"\n  ... and {len(keys) - 5} more keys"
        return summary

    if isinstance(data, list):
        count = len(data)
        samples = [str(item)[:80] for item in data[:3]]
        summary = (
            f"[LIST COMPRESSED — {token_count} tokens, {count} items]\n"
            f"Samples: {samples}"
        )
        return summary

    return None


def _strategy_statistical_summary(content: str, token_count: int) -> str | None:
    """Replace with excerpt + stats."""
    if token_count <= 100:
        return None
    excerpt = content[:_SUMMARY_EXCERPT]
    return (
        f"[COMPRESSED — {token_count} tokens → summary]\n"
        f"Length: {len(content)} chars\n"
        f"Excerpt: {excerpt}..."
    )


def _strategy_truncation(content: str, token_count: int) -> str | None:
    """Hard truncation to preview length."""
    if len(content) <= _TRUNCATION_PREVIEW:
        return None
    return content[:_TRUNCATION_PREVIEW] + f"... [truncated from {token_count} tokens]"


# ---------------------------------------------------------------------------
# Summarization heuristics
# ---------------------------------------------------------------------------


def _detect_content_type(content: str) -> str:
    """Detect content type: json, list, code, or text."""
    stripped = content.strip()

    # JSON detection
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, TypeError):
            pass

    # List detection: lines starting with - or * or numbered
    lines = stripped.split("\n")
    list_lines = sum(
        1 for line in lines if re.match(r"^\s*[-*]\s|^\s*\d+[.)]\s", line)
    )
    if list_lines > len(lines) * 0.5 and list_lines >= 3:
        return "list"

    # Code detection: function/class defs, braces, indentation patterns
    code_signals = sum(
        1
        for line in lines
        if re.match(r"^\s*(def |class |function |const |let |var |import |from |#include)", line)
        or re.match(r"^\s*[{}()];?\s*$", line)
    )
    if code_signals > len(lines) * 0.15 and code_signals >= 2:
        return "code"

    return "text"


# ---------------------------------------------------------------------------
# ContextOptimizer
# ---------------------------------------------------------------------------


class ContextOptimizer:
    """
    Semantic-value context optimizer for Mendicant Bias.

    Ranks messages by a weighted combination of semantic relevance, recency,
    and role importance, then compresses or summarizes the lowest-priority
    messages until the context fits within the token budget.

    Parameters
    ----------
    semantic_weight : float
        Weight for semantic similarity score (default: 0.6).
    recency_weight : float
        Weight for recency score (default: 0.3).
    role_weight : float
        Weight for role-based score (default: 0.1).
    embedding_model : str
        sentence-transformers model name for semantic scoring.
    """

    def __init__(
        self,
        semantic_weight: float = 0.6,
        recency_weight: float = 0.3,
        role_weight: float = 0.1,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.semantic_weight = semantic_weight
        self.recency_weight = recency_weight
        self.role_weight = role_weight
        self.embedding_model = embedding_model
        self._encoder: Any | None = None
        self._encoder_loaded = False

    # ------------------------------------------------------------------
    # Lazy encoder loading
    # ------------------------------------------------------------------

    def _load_encoder(self) -> Any | None:
        """Load the sentence-transformer encoder lazily. Returns None on failure."""
        if self._encoder_loaded:
            return self._encoder
        self._encoder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer

            self._encoder = SentenceTransformer(self.embedding_model)
            logger.info("[ContextOptimizer] Loaded encoder: %s", self.embedding_model)
        except ImportError:
            logger.debug(
                "[ContextOptimizer] sentence-transformers not installed; "
                "semantic scoring disabled"
            )
            self._encoder = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ContextOptimizer] Failed to load encoder: %s", exc)
            self._encoder = None
        return self._encoder

    def _encode(self, text: str) -> list[float] | None:
        """Encode text to an embedding vector, or None if unavailable."""
        encoder = self._load_encoder()
        if encoder is None:
            return None
        try:
            vec = encoder.encode(text, show_progress_bar=False)
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _semantic_score(
        self, content: str, query_embedding: list[float] | None
    ) -> float:
        """Cosine similarity between content embedding and query embedding."""
        if query_embedding is None:
            return 0.0
        content_emb = self._encode(content)
        if content_emb is None:
            return 0.0
        return max(0.0, _cosine_similarity(content_emb, query_embedding))

    @staticmethod
    def _recency_score(index: int, total: int) -> float:
        """Score from 0.0 (oldest, index 0) to 1.0 (newest, last index)."""
        if total <= 1:
            return 1.0
        return index / (total - 1)

    @staticmethod
    def _role_score(role: str) -> float:
        """Role-based importance weight."""
        return _ROLE_WEIGHTS.get(role, 0.3)

    def _combined_score(
        self,
        semantic: float,
        recency: float,
        role: float,
    ) -> float:
        """Weighted combination of all score dimensions."""
        return (
            self.semantic_weight * semantic
            + self.recency_weight * recency
            + self.role_weight * role
        )

    # ------------------------------------------------------------------
    # Public API: rank_messages
    # ------------------------------------------------------------------

    def rank_messages(
        self,
        messages: list[dict[str, Any]],
        current_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Score each message by priority.

        Returns messages with added ``_priority`` and ``_scores`` keys::

            {
                "role": "tool",
                "content": "...",
                "_priority": 0.45,
                "_scores": {
                    "semantic": 0.3,
                    "recency": 0.6,
                    "role": 0.5,
                    "combined": 0.45
                }
            }
        """
        total = len(messages)
        query_embedding: list[float] | None = None
        if current_query:
            query_embedding = self._encode(current_query)

        ranked: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            sem = self._semantic_score(content, query_embedding)
            rec = self._recency_score(idx, total)
            rol = self._role_score(role)
            combined = self._combined_score(sem, rec, rol)

            # System messages always get priority 1.0
            if role == "system":
                combined = 1.0

            entry = dict(msg)  # shallow copy
            entry["_priority"] = round(combined, 4)
            entry["_scores"] = {
                "semantic": round(sem, 4),
                "recency": round(rec, 4),
                "role": round(rol, 4),
                "combined": round(combined, 4),
            }
            ranked.append(entry)

        return ranked

    # ------------------------------------------------------------------
    # Public API: summarize_result
    # ------------------------------------------------------------------

    def summarize_result(self, content: str, max_tokens: int = 100) -> str:
        """
        Extract key facts from a tool result before deletion.

        Uses heuristic extraction (not LLM) with content-type-aware
        strategies:

        - **JSON**: extract top-level keys and first values
        - **Lists**: count + first 3 items
        - **Code**: function signatures + line count
        - **Text**: first 2 sentences + word count
        """
        content_type = _detect_content_type(content)

        if content_type == "json":
            return self._summarize_json(content, max_tokens)
        elif content_type == "list":
            return self._summarize_list(content, max_tokens)
        elif content_type == "code":
            return self._summarize_code(content, max_tokens)
        else:
            return self._summarize_text(content, max_tokens)

    def _summarize_json(self, content: str, max_tokens: int) -> str:
        """Extract top-level keys and first values from JSON."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return self._summarize_text(content, max_tokens)

        if isinstance(data, dict):
            keys = list(data.keys())
            items = []
            for k in keys[:5]:
                val_repr = repr(data[k])
                if len(val_repr) > 60:
                    val_repr = val_repr[:60] + "..."
                items.append(f"{k}: {val_repr}")
            summary = f"JSON object with {len(keys)} keys: {', '.join(items)}"
            return summary[:max_tokens * 4]  # approximate char limit

        if isinstance(data, list):
            return self._summarize_json_list(data, max_tokens)

        return self._summarize_text(content, max_tokens)

    def _summarize_json_list(self, data: list, max_tokens: int) -> str:
        """Summarize a JSON list."""
        count = len(data)
        samples = []
        for item in data[:3]:
            s = repr(item)
            if len(s) > 60:
                s = s[:60] + "..."
            samples.append(s)
        return f"JSON array with {count} items. First 3: {', '.join(samples)}"

    def _summarize_list(self, content: str, max_tokens: int) -> str:
        """Summarize a bulleted/numbered list."""
        lines = content.strip().split("\n")
        list_items = [
            line.strip()
            for line in lines
            if re.match(r"^\s*[-*]\s|^\s*\d+[.)]\s", line)
        ]
        count = len(list_items)
        samples = [item[:80] for item in list_items[:3]]
        summary = f"List with {count} items. First 3: " + "; ".join(samples)
        return summary[:max_tokens * 4]

    def _summarize_code(self, content: str, max_tokens: int) -> str:
        """Extract function/class signatures + line count."""
        lines = content.strip().split("\n")
        signatures = []
        for line in lines:
            stripped = line.strip()
            if re.match(
                r"^(def |class |function |const |let |var |export |async )",
                stripped,
            ):
                sig = stripped[:100]
                signatures.append(sig)

        sig_text = "; ".join(signatures[:5]) if signatures else "no signatures found"
        summary = f"Code block ({len(lines)} lines). Signatures: {sig_text}"
        return summary[:max_tokens * 4]

    def _summarize_text(self, content: str, max_tokens: int) -> str:
        """First 2 sentences + word count."""
        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", content.strip())
        first_two = " ".join(sentences[:2])
        word_count = len(content.split())
        summary = f"{first_two} [{word_count} words total]"
        # Rough token-to-char: 1 token ~= 4 chars
        return summary[: max_tokens * 4]

    # ------------------------------------------------------------------
    # Public API: optimize
    # ------------------------------------------------------------------

    def optimize(
        self,
        messages: list[dict[str, Any]],
        budget_tokens: int = 30000,
        current_query: str | None = None,
    ) -> dict[str, Any]:
        """
        Optimize messages to fit within budget using semantic ranking.

        Ranks all messages, then compresses the lowest-priority ones first
        using FR4-derived strategies (key_fields, statistical_summary,
        truncation).

        Returns
        -------
        dict with keys:
            optimized_messages : list[dict]
                The optimized message list.
            manifest : dict
                Detailed record of what happened:
                - original_tokens, optimized_tokens, tokens_saved
                - actions: per-message action log
                - strategy_applied: overall strategy name
        """
        if not messages:
            return {
                "optimized_messages": [],
                "manifest": {
                    "original_tokens": 0,
                    "optimized_tokens": 0,
                    "tokens_saved": 0,
                    "actions": [],
                    "strategy_applied": "none",
                },
            }

        # Step 1: Rank all messages
        ranked = self.rank_messages(messages, current_query=current_query)

        # Step 2: Count tokens per message
        per_msg_tokens = [_count_tokens(m.get("content", "")) + 4 for m in ranked]
        original_total = sum(per_msg_tokens)

        # Step 3: If already under budget, return as-is
        actions: list[dict[str, Any]] = []
        if original_total <= budget_tokens:
            for idx, msg in enumerate(ranked):
                actions.append({
                    "index": idx,
                    "action": "kept",
                    "reason": f"under budget (priority {msg['_priority']})",
                })
            # Strip internal scoring keys from output
            clean = self._strip_scores(ranked)
            return {
                "optimized_messages": clean,
                "manifest": {
                    "original_tokens": original_total,
                    "optimized_tokens": original_total,
                    "tokens_saved": 0,
                    "actions": actions,
                    "strategy_applied": "none",
                },
            }

        # Step 4: Build priority-sorted index (lowest priority first)
        priority_order = sorted(
            range(len(ranked)),
            key=lambda i: ranked[i]["_priority"],
        )

        # Step 5: Compress lowest-priority messages first
        optimized = [dict(m) for m in ranked]  # working copy
        current_tokens = list(per_msg_tokens)
        strategy_applied = "semantic_ranking"

        # Strategies in priority order
        strategies = [
            ("key_fields", _strategy_key_fields),
            ("statistical_summary", _strategy_statistical_summary),
            ("truncation", _strategy_truncation),
        ]

        for idx in priority_order:
            if sum(current_tokens) <= budget_tokens:
                break

            msg = optimized[idx]
            role = msg.get("role", "user")
            content = msg.get("content", "")
            priority = msg.get("_priority", 0)

            # Never compress system messages
            if role == "system":
                actions.append({
                    "index": idx,
                    "action": "kept",
                    "reason": "system message",
                })
                continue

            # Try each strategy in order
            compressed = False
            for strat_name, strat_fn in strategies:
                result = strat_fn(content, current_tokens[idx])
                if result is not None:
                    # Generate a summary before replacing
                    summary = self.summarize_result(content, max_tokens=100)
                    optimized[idx] = {
                        "role": role,
                        "content": result,
                    }
                    new_tokens = _count_tokens(result) + 4
                    actions.append({
                        "index": idx,
                        "action": "compressed",
                        "reason": f"low relevance ({priority})",
                        "strategy": strat_name,
                        "summary": summary[:200],
                    })
                    current_tokens[idx] = new_tokens
                    compressed = True
                    break

            if not compressed:
                actions.append({
                    "index": idx,
                    "action": "kept",
                    "reason": f"already compact (priority {priority})",
                })

        # Fill in actions for messages we never visited
        acted_indices = {a["index"] for a in actions}
        for idx in range(len(optimized)):
            if idx not in acted_indices:
                actions.append({
                    "index": idx,
                    "action": "kept",
                    "reason": f"above budget threshold (priority {ranked[idx]['_priority']})",
                })

        # Sort actions by index for readability
        actions.sort(key=lambda a: a["index"])

        optimized_total = sum(current_tokens)
        clean = self._strip_scores(optimized)

        return {
            "optimized_messages": clean,
            "manifest": {
                "original_tokens": original_total,
                "optimized_tokens": optimized_total,
                "tokens_saved": max(0, original_total - optimized_total),
                "actions": actions,
                "strategy_applied": strategy_applied,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_scores(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove internal _priority and _scores keys from messages."""
        clean = []
        for msg in messages:
            entry = {k: v for k, v in msg.items() if not k.startswith("_")}
            clean.append(entry)
        return clean
