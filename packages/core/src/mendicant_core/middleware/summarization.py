"""
summarization.py
================
Mendicant Bias — Summarization Middleware

Reduces context when approaching token limits by summarizing older messages.
Works alongside FR4 Context Budget — this middleware operates on message
count (coarse), while Context Budget operates on token count (fine).

Strategy
--------
When ``len(messages) > max_messages``, the middleware:

1. Preserves all ``SystemMessage`` entries (they carry the system prompt).
2. Keeps the ``keep_recent`` most recent non-system messages intact.
3. Condenses older non-system messages into a single ``HumanMessage``
   summary that lists the role and a truncated preview of each message.

This is a *local* heuristic — it does not call an LLM to produce the
summary.  For LLM-powered summarisation, integrate with the Context Budget
middleware or configure the upstream DeerFlow ``SummarizationMiddleware``
(which uses a chat model for summarisation).
"""

from __future__ import annotations

import logging
from typing import override

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class SummarizationMiddleware(AgentMiddleware[AgentState]):
    """Summarise older messages when the conversation grows too long.

    Parameters
    ----------
    max_messages : int
        Total message count threshold before summarisation kicks in.
    keep_recent : int
        Number of most-recent non-system messages to keep verbatim.
    preview_chars : int
        Maximum characters to include in each message's preview inside the
        summary block.
    max_summary_entries : int
        Maximum number of older messages to list in the summary.  When there
        are more, only the last ``max_summary_entries`` are shown.
    """

    state_schema = AgentState

    def __init__(
        self,
        *,
        max_messages: int = 50,
        keep_recent: int = 10,
        preview_chars: int = 100,
        max_summary_entries: int = 20,
    ) -> None:
        super().__init__()
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.preview_chars = preview_chars
        self.max_summary_entries = max_summary_entries

    @override
    def before_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict | None:
        messages: list[BaseMessage] = list(state.get("messages", []))

        if len(messages) <= self.max_messages:
            return None

        # Separate system messages from the rest
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]

        if len(non_system) <= self.keep_recent:
            return None

        older = non_system[: -self.keep_recent]
        recent = non_system[-self.keep_recent :]

        # Build a condensed summary of the older messages
        summary_parts: list[str] = []
        # Only include the last N entries to keep the summary bounded
        entries_to_show = older[-self.max_summary_entries :]
        for msg in entries_to_show:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            role = getattr(msg, "type", "unknown")
            if len(content) > self.preview_chars:
                preview = content[: self.preview_chars] + "..."
            else:
                preview = content
            summary_parts.append(f"[{role}] {preview}")

        condensed_count = len(older)
        summary_text = (
            f"[CONTEXT SUMMARY — {condensed_count} older messages condensed]\n"
            + "\n".join(summary_parts)
        )

        summary_msg = HumanMessage(content=summary_text)

        logger.info(
            "[Summarization] Condensed %d older messages into summary (%d recent kept)",
            condensed_count,
            len(recent),
        )

        return {"messages": system_msgs + [summary_msg] + recent}
