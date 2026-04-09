"""
MemoryInjector — Formats memory for injection into prompts.

Prioritizes: top_of_mind > recent high-confidence facts > work context.
Respects a configurable token budget (estimated at ~4 chars per token).
"""

from __future__ import annotations

import logging
from typing import Any

from mendicant_core.memory.models import Fact, MemoryData

logger = logging.getLogger(__name__)

# Rough chars-per-token estimate for budget calculation
_CHARS_PER_TOKEN = 4


class MemoryInjector:
    """Formats memory data for prompt injection within a token budget."""

    def __init__(self, max_tokens: int = 2000) -> None:
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def format_for_injection(self, data: MemoryData) -> str:
        """Format memory as a prompt-friendly string wrapped in <memory> tags.

        Priority order:
        1. Top-of-mind context
        2. Recent high-confidence facts
        3. Work context
        4. History context

        Respects the token budget — stops adding sections once the budget
        would be exceeded.
        """
        budget_chars = self._max_tokens * _CHARS_PER_TOKEN
        sections: list[str] = []
        used = 0

        # Header
        header = "<memory>"
        sections.append(header)
        used += len(header)

        # 1. Top of mind
        if data.user.top_of_mind.summary:
            block = f"\n## Top of Mind\n{data.user.top_of_mind.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        # 2. Facts (sorted by confidence desc, then recency desc)
        if data.facts:
            facts_block = self.format_facts(data.facts, max_count=15)
            if facts_block and used + len(facts_block) < budget_chars:
                sections.append(facts_block)
                used += len(facts_block)

        # 3. Work context
        if data.user.work_context.summary:
            block = f"\n## Work Context\n{data.user.work_context.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        # 4. Personal context
        if data.user.personal_context.summary:
            block = f"\n## Personal Context\n{data.user.personal_context.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        # 5. Recent history
        if data.history.recent_months.summary:
            block = f"\n## Recent History\n{data.history.recent_months.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        # 6. Earlier / long-term (only if budget allows)
        if data.history.earlier_context.summary:
            block = f"\n## Earlier Context\n{data.history.earlier_context.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        if data.history.long_term_background.summary:
            block = f"\n## Long-term Background\n{data.history.long_term_background.summary}"
            if used + len(block) < budget_chars:
                sections.append(block)
                used += len(block)

        # Footer
        sections.append("\n</memory>")

        result = "\n".join(sections) if len(sections) > 2 else ""
        return result

    def format_facts(self, facts: list[Fact], max_count: int = 15) -> str:
        """Format top facts sorted by confidence + recency.

        Returns a formatted block of facts, or empty string if no facts.
        """
        if not facts:
            return ""

        # Sort: confidence descending, then created_at descending (recent first)
        sorted_facts = sorted(
            facts,
            key=lambda f: (-f.confidence, f.created_at or ""),
            reverse=False,  # We already negated confidence
        )
        # For created_at we want descending (most recent first) among same confidence
        # Re-sort properly: primary = -confidence, secondary = -created_at
        sorted_facts = sorted(
            facts,
            key=lambda f: (-f.confidence, ""),
        )
        # Stable sort by created_at descending within same confidence
        sorted_facts.sort(key=lambda f: f.created_at or "", reverse=True)
        sorted_facts.sort(key=lambda f: -f.confidence)

        top = sorted_facts[:max_count]

        lines = ["\n## Known Facts"]
        for fact in top:
            conf_pct = int(fact.confidence * 100)
            lines.append(f"- [{fact.category}] {fact.content} ({conf_pct}%)")

        return "\n".join(lines)

    def format_context_summary(self, data: MemoryData) -> dict[str, Any]:
        """Return a dict summary of what memory contains (for status endpoints)."""
        return {
            "top_of_mind": bool(data.user.top_of_mind.summary),
            "work_context": bool(data.user.work_context.summary),
            "personal_context": bool(data.user.personal_context.summary),
            "recent_history": bool(data.history.recent_months.summary),
            "fact_count": len(data.facts),
            "injection_budget_tokens": self._max_tokens,
        }
