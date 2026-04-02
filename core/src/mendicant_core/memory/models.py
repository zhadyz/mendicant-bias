"""
Memory data models — DeerFlow-compatible schema using dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ContextSection:
    """A single section of contextual memory (summary + timestamp)."""

    summary: str = ""
    updated_at: str = ""


@dataclass
class UserContext:
    """User-specific context buckets."""

    work_context: ContextSection = field(default_factory=ContextSection)
    personal_context: ContextSection = field(default_factory=ContextSection)
    top_of_mind: ContextSection = field(default_factory=ContextSection)


@dataclass
class HistoryContext:
    """Temporal history buckets."""

    recent_months: ContextSection = field(default_factory=ContextSection)
    earlier_context: ContextSection = field(default_factory=ContextSection)
    long_term_background: ContextSection = field(default_factory=ContextSection)


@dataclass
class Fact:
    """A single extracted fact with metadata."""

    id: str = ""
    content: str = ""
    category: str = "knowledge"  # preference, knowledge, context, behavior, goal
    confidence: float = 0.8
    created_at: str = ""
    source: str = "unknown"  # thread_id or "unknown"

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"fact_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(tz=timezone.utc).isoformat()


@dataclass
class MemoryData:
    """Root memory container — DeerFlow-compatible schema."""

    version: str = "1.0"
    last_updated: str = ""
    user: UserContext = field(default_factory=UserContext)
    history: HistoryContext = field(default_factory=HistoryContext)
    facts: list[Fact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON storage."""

        def _section(s: ContextSection) -> dict[str, str]:
            return {"summary": s.summary, "updated_at": s.updated_at}

        return {
            "version": self.version,
            "last_updated": self.last_updated,
            "user": {
                "work_context": _section(self.user.work_context),
                "personal_context": _section(self.user.personal_context),
                "top_of_mind": _section(self.user.top_of_mind),
            },
            "history": {
                "recent_months": _section(self.history.recent_months),
                "earlier_context": _section(self.history.earlier_context),
                "long_term_background": _section(self.history.long_term_background),
            },
            "facts": [
                {
                    "id": f.id,
                    "content": f.content,
                    "category": f.category,
                    "confidence": f.confidence,
                    "created_at": f.created_at,
                    "source": f.source,
                }
                for f in self.facts
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryData:
        """Deserialize from a plain dict."""

        def _parse_section(d: dict[str, Any] | None) -> ContextSection:
            if d is None:
                return ContextSection()
            return ContextSection(
                summary=d.get("summary", ""),
                updated_at=d.get("updated_at", ""),
            )

        user_data = data.get("user", {})
        history_data = data.get("history", {})

        user = UserContext(
            work_context=_parse_section(user_data.get("work_context")),
            personal_context=_parse_section(user_data.get("personal_context")),
            top_of_mind=_parse_section(user_data.get("top_of_mind")),
        )

        history = HistoryContext(
            recent_months=_parse_section(history_data.get("recent_months")),
            earlier_context=_parse_section(history_data.get("earlier_context")),
            long_term_background=_parse_section(history_data.get("long_term_background")),
        )

        facts_raw = data.get("facts", [])
        facts = [
            Fact(
                id=f.get("id", ""),
                content=f.get("content", ""),
                category=f.get("category", "knowledge"),
                confidence=f.get("confidence", 0.8),
                created_at=f.get("created_at", ""),
                source=f.get("source", "unknown"),
            )
            for f in facts_raw
        ]

        return cls(
            version=data.get("version", "1.0"),
            last_updated=data.get("last_updated", ""),
            user=user,
            history=history,
            facts=facts,
        )
