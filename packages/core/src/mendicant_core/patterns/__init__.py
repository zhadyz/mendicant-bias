"""
mendicant_core.patterns
=======================
Reusable pattern store extracted from adaptive_learning's storage logic.

Provides a standalone ``PatternStore`` class for recording, searching, and
managing JSON-backed pattern records with cosine-similarity lookup and
atomic file writes.

Usage
-----
>>> from mendicant_core.patterns import PatternStore
>>> store = PatternStore(".mendicant/patterns.json", max_records=500)
>>> store.append({"task_type": "CODE", "embedding": [...], "outcome": "success"})
>>> results = store.search(query_embedding=[0.1, 0.2, ...], top_n=3)
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility
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


# ---------------------------------------------------------------------------
# PatternStore
# ---------------------------------------------------------------------------


class PatternStore:
    """
    JSON-backed pattern store with LRU eviction and cosine-similarity search.

    Designed as a reusable building block for any middleware or component that
    needs to persist and query embedding-annotated records.

    Parameters
    ----------
    store_path : str | Path
        Path to the JSON file used for persistence.
    max_records : int
        Maximum number of records to retain.  When exceeded, the oldest
        entries are evicted (LRU).
    """

    def __init__(
        self,
        store_path: str | Path,
        max_records: int = 1000,
    ) -> None:
        self.store_path = Path(store_path)
        self.max_records = max_records

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def load(self) -> list[dict[str, Any]]:
        """
        Load all patterns from the JSON store.

        Returns
        -------
        list[dict]
            The stored pattern records, or an empty list if the file does
            not exist or contains invalid data.
        """
        if not self.store_path.exists():
            return []
        try:
            with self.store_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001
            logger.error("[PatternStore] Failed to load patterns: %s", exc)
            return []

    def append(self, pattern: dict[str, Any]) -> None:
        """
        Append *pattern* to the store with LRU eviction and atomic write.

        If the store exceeds ``max_records`` after insertion, the oldest
        entries are evicted to bring the count back within the limit.

        Parameters
        ----------
        pattern : dict
            Arbitrary pattern record.  Should contain an ``"embedding"`` key
            (list[float]) for similarity search to work.
        """
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        patterns = self.load()
        patterns.append(pattern)

        # LRU eviction — keep the most recent max_records entries
        if len(patterns) > self.max_records:
            patterns = patterns[-self.max_records :]

        self._atomic_write(patterns)

    def search(
        self,
        query_embedding: list[float],
        top_n: int = 5,
        min_similarity: float = 0.3,
        task_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search stored patterns by cosine similarity against *query_embedding*.

        Parameters
        ----------
        query_embedding : list[float]
            The embedding vector for the query.
        top_n : int
            Maximum number of results to return.
        min_similarity : float
            Minimum cosine similarity threshold.  Patterns scoring below this
            are excluded.
        task_type : str | None
            Optional filter.  If provided, only patterns whose ``"task_type"``
            matches are considered.

        Returns
        -------
        list[dict]
            Matching patterns, each augmented with a ``"_similarity"`` key.
            Sorted descending by similarity score.
        """
        patterns = self.load()
        if not patterns:
            return []

        # Optional task_type filter
        if task_type is not None:
            patterns = [p for p in patterns if p.get("task_type") == task_type]

        scored: list[dict[str, Any]] = []
        for p in patterns:
            p_emb: list[float] | None = p.get("embedding")
            if not p_emb:
                continue
            sim = _cosine_similarity(query_embedding, p_emb)
            if sim >= min_similarity:
                scored.append(dict(p, _similarity=sim))

        scored.sort(key=lambda x: x["_similarity"], reverse=True)
        return scored[:top_n]

    def get_stats(self) -> dict[str, Any]:
        """
        Return summary statistics about the pattern store.

        Returns
        -------
        dict
            Keys: ``total``, ``task_types``, ``outcomes``,
            ``avg_duration_seconds``, ``has_embeddings``.
        """
        patterns = self.load()
        if not patterns:
            return {"total": 0}

        type_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        total_duration = 0.0
        embedded_count = 0

        for p in patterns:
            tt = p.get("task_type", "unknown")
            oc = p.get("outcome", "unknown")
            type_counts[tt] = type_counts.get(tt, 0) + 1
            outcome_counts[oc] = outcome_counts.get(oc, 0) + 1
            total_duration += p.get("duration_seconds", 0.0)
            if p.get("embedding"):
                embedded_count += 1

        return {
            "total": len(patterns),
            "task_types": type_counts,
            "outcomes": outcome_counts,
            "avg_duration_seconds": round(total_duration / len(patterns), 3),
            "has_embeddings": embedded_count,
        }

    def clear(self) -> None:
        """Clear the pattern store, writing an empty list to disk."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write([])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _atomic_write(self, patterns: list[dict[str, Any]]) -> None:
        """Write patterns to disk via a temp file + os.replace (atomic on POSIX)."""
        dir_ = self.store_path.parent
        dir_.mkdir(parents=True, exist_ok=True)
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(dir_), suffix=".json.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(patterns, fh, indent=2, default=str)
            os.replace(tmp_path, str(self.store_path))
        except Exception as exc:  # noqa: BLE001
            logger.error("[PatternStore] Atomic write failed: %s", exc)
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
