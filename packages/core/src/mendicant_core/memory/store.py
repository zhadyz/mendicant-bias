"""
MemoryStore — Persistent storage and retrieval for conversational memory.

Uses atomic file I/O (temp file + os.replace) to prevent corruption.
Default storage at .mendicant/memory.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mendicant_core.memory.models import Fact, MemoryData

logger = logging.getLogger(__name__)


def _normalize_content(text: str) -> str:
    """Normalize whitespace for deduplication comparison."""
    return re.sub(r"\s+", " ", text.strip()).lower()


def _stem(word: str) -> str:
    """Naive English suffix stripping for fuzzy search.

    Strips common suffixes to match related word forms. Not a real stemmer
    — just enough to catch test/testing/tests, commit/committing,
    deploy/deployment, etc.
    """
    w = word.lower()
    # Longest suffixes first to avoid partial matches
    for suffix in (
        "itting", "tting", "ting",          # committing, setting, testing
        "ment", "ness", "tion", "sion",     # deployment, weakness, action
        "ling", "ning", "ring",             # handling, running, entering
        "ing",                               # running, testing
        "ies", "ied",                        # entries, tried
        "est", "ers",                        # fastest, workers
        "ed", "ly", "er",                   # worked, quickly, worker
        "es", "ts", "s",                    # matches, tests, runs
    ):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


class MemoryStore:
    """Handles storage and retrieval of memory data with atomic file I/O."""

    def __init__(self, storage_path: str | Path = ".mendicant/memory.json") -> None:
        self._path = Path(storage_path)
        self._data: MemoryData | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> MemoryData:
        """Load memory from JSON file, creating default if absent."""
        if self._data is not None:
            return self._data

        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = MemoryData.from_dict(raw)
                logger.debug("[Memory] Loaded %d facts from %s", len(self._data.facts), self._path)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("[Memory] Corrupt memory file %s: %s — starting fresh", self._path, exc)
                self._data = MemoryData()
        else:
            self._data = MemoryData()

        return self._data

    def save(self, data: MemoryData | None = None) -> None:
        """Atomic write: temp file + os.replace to prevent corruption."""
        if data is not None:
            self._data = data

        if self._data is None:
            return

        self._data.last_updated = datetime.now(tz=timezone.utc).isoformat()

        # Ensure parent directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(self._data.to_dict(), indent=2, ensure_ascii=False)

        # Atomic write via temp file in the same directory
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".memory_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, str(self._path))
            logger.debug("[Memory] Saved %d facts to %s", len(self._data.facts), self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_facts(
        self,
        category: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """Filter facts by category and minimum confidence."""
        data = self.load()
        results = data.facts

        if category is not None:
            results = [f for f in results if f.category == category]

        if min_confidence > 0.0:
            results = [f for f in results if f.confidence >= min_confidence]

        return results

    def add_fact(
        self,
        content: str,
        category: str = "knowledge",
        confidence: float = 0.8,
        source: str = "unknown",
    ) -> Fact:
        """Add a new fact, deduplicating by whitespace-normalized content.

        If a fact with the same normalized content already exists, update its
        confidence to the maximum of the old and new values and return it.
        """
        data = self.load()

        normalized = _normalize_content(content)

        # Deduplicate
        for existing in data.facts:
            if _normalize_content(existing.content) == normalized:
                existing.confidence = max(existing.confidence, confidence)
                existing.source = source
                self.save()
                logger.debug("[Memory] Deduplicated fact: %s", existing.id)
                return existing

        fact = Fact(
            content=content.strip(),
            category=category,
            confidence=confidence,
            source=source,
        )
        data.facts.append(fact)
        self.save()
        logger.debug("[Memory] Added fact %s: %.60s", fact.id, fact.content)
        return fact

    def remove_fact(self, fact_id: str) -> bool:
        """Remove a fact by ID. Returns True if found and removed."""
        data = self.load()
        original_len = len(data.facts)
        data.facts = [f for f in data.facts if f.id != fact_id]

        if len(data.facts) < original_len:
            self.save()
            logger.debug("[Memory] Removed fact %s", fact_id)
            return True
        return False

    def clear(self) -> None:
        """Clear all memory (facts and context sections)."""
        self._data = MemoryData()
        self.save()
        logger.info("[Memory] Cleared all memory")

    def get_stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        data = self.load()

        category_counts: dict[str, int] = {}
        for fact in data.facts:
            category_counts[fact.category] = category_counts.get(fact.category, 0) + 1

        avg_confidence = 0.0
        if data.facts:
            avg_confidence = sum(f.confidence for f in data.facts) / len(data.facts)

        return {
            "total_facts": len(data.facts),
            "categories": category_counts,
            "average_confidence": round(avg_confidence, 3),
            "last_updated": data.last_updated,
            "storage_path": str(self._path),
            "has_user_context": bool(
                data.user.work_context.summary
                or data.user.personal_context.summary
                or data.user.top_of_mind.summary
            ),
            "has_history_context": bool(
                data.history.recent_months.summary
                or data.history.earlier_context.summary
                or data.history.long_term_background.summary
            ),
        }

    def search_facts(self, query: str, limit: int = 10) -> list[Fact]:
        """Fuzzy search over facts with keyword overlap + substring matching.

        Search strategy (scored and combined):
        1. Exact word matches (highest weight)
        2. Substring/partial matches (medium weight)
        3. Stemmed overlap — strips common suffixes for broader matching

        Returns facts sorted by combined relevance score, then confidence.
        """
        data = self.load()
        if not query.strip():
            return sorted(data.facts, key=lambda f: -f.confidence)[:limit]

        query_lower = query.lower()
        query_words = set(query_lower.split())
        query_stems = {_stem(w) for w in query_words if len(w) > 3}

        scored: list[tuple[float, float, Fact]] = []
        for fact in data.facts:
            fact_lower = fact.content.lower()
            fact_words = set(fact_lower.split())
            fact_stems = {_stem(w) for w in fact_words if len(w) > 3}

            # Exact word matches (weight 3)
            exact = len(query_words & fact_words)

            # Substring matches — query word appears inside any fact word or vice versa (weight 2)
            substr = 0
            for qw in query_words:
                if len(qw) >= 3 and any(qw in fw or fw in qw for fw in fact_words):
                    substr += 1

            # Stem overlap — catches test/testing, commit/committing, etc. (weight 1)
            stem_matches = len(query_stems & fact_stems) if query_stems else 0

            score = (exact * 3.0) + (substr * 2.0) + (stem_matches * 1.0)

            if score > 0:
                scored.append((score, fact.confidence, fact))

        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [t[2] for t in scored[:limit]]
