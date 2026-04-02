"""
MemoryUpdater — LLM-based fact extraction with heuristic fallback.

If a model_factory is provided, uses an LLM to extract structured facts
from conversation text. Otherwise, falls back to simple pattern matching.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from mendicant_core.memory.models import MemoryData
from mendicant_core.memory.store import MemoryStore, _normalize_content

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristic fact extraction (no LLM required)
# ---------------------------------------------------------------------------

# Pattern categories with their regex patterns and category labels
_HEURISTIC_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    # Preferences
    (
        "preference",
        "preference",
        re.compile(
            r"(?:i\s+(?:prefer|like|enjoy|love|favor|choose|always use|tend to use))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "preference",
        "preference",
        re.compile(
            r"(?:my\s+(?:preferred|favorite|go-to))\s+(?:\w+\s+)?(?:is|are)\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    # Knowledge / work
    (
        "knowledge",
        "knowledge",
        re.compile(
            r"(?:i\s+(?:work\s+(?:on|with|at|in)|use|build|develop|maintain|manage))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "knowledge",
        "knowledge",
        re.compile(
            r"(?:i(?:'m|\s+am)\s+(?:a|an|the)\s+)(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "knowledge",
        "knowledge",
        re.compile(
            r"(?:my\s+(?:project|codebase|system|app|application|stack)\s+(?:is|uses))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    # Goals
    (
        "goal",
        "goal",
        re.compile(
            r"(?:i\s+(?:want\s+to|need\s+to|'m\s+trying\s+to|plan\s+to|intend\s+to|aim\s+to|hope\s+to))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "goal",
        "goal",
        re.compile(
            r"(?:my\s+goal\s+is\s+(?:to\s+)?)\s*(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    # Behavior patterns
    (
        "behavior",
        "behavior",
        re.compile(
            r"(?:i\s+(?:usually|always|never|often|sometimes|typically))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    # Context
    (
        "context",
        "context",
        re.compile(
            r"(?:i(?:'m|\s+am)\s+(?:currently|now|today))\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "context",
        "context",
        re.compile(
            r"(?:(?:right\s+now|currently|at\s+the\s+moment),?\s+i)\s+(.+?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
    ),
]


def extract_facts_heuristic(text: str) -> list[dict[str, Any]]:
    """Extract facts using simple pattern matching.

    Looks for:
    - Preferences: "I prefer...", "I like...", "my favorite..."
    - Knowledge: "I work on...", "I use...", "I'm a..."
    - Goals: "I want to...", "I'm trying to...", "I plan to..."
    - Behavior: "I usually...", "I always...", "I never..."
    - Context: "I'm currently...", "right now I..."

    Returns a list of dicts with keys: content, category, confidence.
    """
    results: list[dict[str, Any]] = []
    seen_normalized: set[str] = set()

    for category, _label, pattern in _HEURISTIC_PATTERNS:
        for match in pattern.finditer(text):
            # Use the full match as the fact content (more context than just the group)
            full_match = match.group(0).strip()
            if len(full_match) < 5:
                continue

            normalized = _normalize_content(full_match)
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)

            results.append({
                "content": full_match,
                "category": category,
                "confidence": 0.6,  # Heuristic extractions get moderate confidence
            })

    return results


# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a memory extraction system. Analyze the following conversation and \
extract factual information about the user that would be useful to remember \
for future conversations.

Extract facts in these categories:
- preference: User preferences, likes, dislikes, style choices
- knowledge: What the user knows, works on, their expertise, tools they use
- context: Current situation, ongoing projects, recent events
- behavior: Patterns, habits, typical workflows
- goal: Things they want to achieve, plans, aspirations

For each fact, provide:
- content: A concise statement of the fact (one sentence)
- category: One of the categories above
- confidence: 0.0-1.0 how confident you are this is a real, stable fact

Respond with a JSON array of objects. Only extract clear, explicit facts — \
do not infer or speculate. If no facts can be extracted, return [].

Existing memory (avoid duplicates):
{existing_facts}

Conversation to analyze:
{conversation}

Respond ONLY with a JSON array:"""


class MemoryUpdater:
    """LLM-based fact extraction with heuristic fallback.

    Parameters
    ----------
    model_factory
        Optional callable that returns an LLM instance with a method
        ``invoke(prompt: str) -> response`` where ``response.content``
        is a string. If None, uses heuristic extraction only.
    max_facts
        Maximum number of facts to retain in the store.
    confidence_threshold
        Minimum confidence for a fact to be stored.
    """

    def __init__(
        self,
        model_factory: Callable[[], Any] | None = None,
        max_facts: int = 100,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._model_factory = model_factory
        self._max_facts = max_facts
        self._confidence_threshold = confidence_threshold

    def extract_facts(
        self,
        conversation: str,
        existing_memory: MemoryData,
    ) -> list[dict[str, Any]]:
        """Extract facts from conversation text.

        Uses LLM if model_factory is available, otherwise falls back to
        heuristic pattern matching.

        Returns a list of dicts with keys: content, category, confidence.
        """
        if self._model_factory is not None:
            return self._extract_facts_llm(conversation, existing_memory)
        return self._extract_facts_heuristic(conversation, existing_memory)

    def _extract_facts_heuristic(
        self,
        conversation: str,
        existing_memory: MemoryData,
    ) -> list[dict[str, Any]]:
        """Heuristic extraction with deduplication against existing memory."""
        raw = extract_facts_heuristic(conversation)

        # Filter by confidence threshold
        raw = [f for f in raw if f["confidence"] >= self._confidence_threshold]

        # Deduplicate against existing facts
        existing_normalized = {
            _normalize_content(f.content) for f in existing_memory.facts
        }
        filtered = [
            f
            for f in raw
            if _normalize_content(f["content"]) not in existing_normalized
        ]

        return filtered

    def _extract_facts_llm(
        self,
        conversation: str,
        existing_memory: MemoryData,
    ) -> list[dict[str, Any]]:
        """LLM-based extraction."""
        try:
            model = self._model_factory()  # type: ignore[misc]

            existing_facts_str = "\n".join(
                f"- [{f.category}] {f.content}" for f in existing_memory.facts[:30]
            ) or "(none)"

            prompt = _EXTRACTION_PROMPT.format(
                existing_facts=existing_facts_str,
                conversation=conversation[:4000],  # Limit input size
            )

            response = model.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # Parse JSON from LLM response
            # Try to find JSON array in the response
            json_match = re.search(r"\[.*\]", content, re.DOTALL)
            if json_match:
                facts_raw = json.loads(json_match.group())
            else:
                logger.warning("[Memory] LLM response did not contain valid JSON array")
                # Fall back to heuristic
                return self._extract_facts_heuristic(conversation, existing_memory)

            # Validate and filter
            valid_categories = {"preference", "knowledge", "context", "behavior", "goal"}
            results: list[dict[str, Any]] = []
            for f in facts_raw:
                if not isinstance(f, dict):
                    continue
                content_str = f.get("content", "")
                category = f.get("category", "knowledge")
                confidence = float(f.get("confidence", 0.7))

                if not content_str or len(content_str) < 5:
                    continue
                if category not in valid_categories:
                    category = "knowledge"
                if confidence < self._confidence_threshold:
                    continue

                results.append({
                    "content": content_str,
                    "category": category,
                    "confidence": confidence,
                })

            # Deduplicate against existing memory
            existing_normalized = {
                _normalize_content(ef.content) for ef in existing_memory.facts
            }
            results = [
                r
                for r in results
                if _normalize_content(r["content"]) not in existing_normalized
            ]

            return results

        except Exception as exc:
            logger.warning("[Memory] LLM extraction failed: %s — falling back to heuristic", exc)
            return self._extract_facts_heuristic(conversation, existing_memory)

    def update_memory(
        self,
        store: MemoryStore,
        conversation: str,
        thread_id: str = "unknown",
    ) -> bool:
        """Full update cycle: extract facts, deduplicate, update store.

        Returns True if new facts were added.
        """
        data = store.load()
        new_facts = self.extract_facts(conversation, data)

        if not new_facts:
            return False

        added = 0
        for fact_dict in new_facts:
            # Enforce max_facts limit
            if len(data.facts) >= self._max_facts:
                # Remove the oldest lowest-confidence fact to make room
                if data.facts:
                    data.facts.sort(key=lambda f: (f.confidence, f.created_at))
                    data.facts.pop(0)

            store.add_fact(
                content=fact_dict["content"],
                category=fact_dict["category"],
                confidence=fact_dict["confidence"],
                source=thread_id,
            )
            added += 1

        if added > 0:
            logger.info("[Memory] Added %d new facts from thread %s", added, thread_id)
            return True

        return False
