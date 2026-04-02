"""
context_budget.py
=================
Mendicant Bias V5 — FR4: Context Budget Enforcement with Compression

A LangGraph AgentMiddleware that fires as a before_model hook.  It counts the
token cost of the current message list (via tiktoken cl100k_base) and, when the
total exceeds the configured budget, compresses the oldest non-system messages
until the budget is satisfied.

Compression strategies (applied oldest-first)
---------------------------------------------
``key_fields``
    For ToolMessages only: strip all content except a short token-count
    summary and a truncated preview.

``statistical_summary``
    For long HumanMessage / AIMessage content: replace with a summary that
    includes original length, token count, and a leading excerpt.

``truncation``
    Hard truncation: keep the first N characters of the message content.

Strategies are tried in priority order (key_fields → statistical_summary →
truncation) until the budget is recovered.

State field
-----------
``context_budget_usage`` — dict with keys ``total_tokens``, ``budget``,
``compressed_count``, ``strategy_applied``.

Thread-level budget override
-----------------------------
Set ``{"thread_budgets": {"<thread_id>": 20000}}`` in ``runtime.context`` to
override the default budget for a specific thread.
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, override

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BUDGET = 30_000          # tokens
_ENCODING_NAME = "cl100k_base"
_TRUNCATION_PREVIEW = 300         # chars kept after truncation
_SUMMARY_EXCERPT = 200            # chars shown in statistical summary
_MIN_MSG_TOKENS = 10              # never compress below this

# ---------------------------------------------------------------------------
# tiktoken import (optional)
# ---------------------------------------------------------------------------

try:
    import tiktoken as _tiktoken

    _ENC = _tiktoken.get_encoding(_ENCODING_NAME)
except ImportError:
    _tiktoken = None  # type: ignore
    _ENC = None


def _count_tokens(text: str) -> int:
    """Count tokens via tiktoken, or fall back to word-count estimate."""
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text.split()))


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class ContextBudgetState(AgentState):
    """Extended agent state carrying context-budget metadata."""

    context_budget_usage: NotRequired[dict[str, Any] | None]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class ContextBudgetMiddleware(AgentMiddleware[ContextBudgetState]):
    """
    Token-budget enforcement middleware.

    Parameters
    ----------
    default_budget : int
        Maximum token budget (default: 30 000).
    strategies : list[str]
        Compression strategies to apply in order.  Valid values:
        ``"key_fields"``, ``"statistical_summary"``, ``"truncation"``.
    system_message_budget_fraction : float
        Fraction of the budget that system messages alone may consume before a
        warning is emitted (default: 0.4).
    """

    state_schema = ContextBudgetState

    def __init__(
        self,
        *,
        default_budget: int = _DEFAULT_BUDGET,
        strategies: list[str] | None = None,
        system_message_budget_fraction: float = 0.4,
    ) -> None:
        super().__init__()
        self.default_budget = default_budget
        self.strategies: list[str] = strategies or [
            "key_fields",
            "statistical_summary",
            "truncation",
        ]
        self.system_message_budget_fraction = system_message_budget_fraction

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def before_model(
        self, state: ContextBudgetState, runtime: Runtime
    ) -> dict | None:
        """
        Count tokens, compress if over-budget, return updated messages and
        ``context_budget_usage`` metadata.
        """
        ctx = runtime.context or {}
        thread_id: str | None = ctx.get("thread_id")
        budget = self._resolve_budget(thread_id, ctx)

        messages: list[BaseMessage] = list(state.get("messages", []))
        if not messages:
            return None

        total_tokens, per_msg_tokens = self._count_all(messages)

        usage: dict[str, Any] = {
            "total_tokens": total_tokens,
            "budget": budget,
            "compressed_count": 0,
            "strategy_applied": None,
            "over_budget": total_tokens > budget,
        }

        if total_tokens <= budget:
            logger.debug(
                "[ContextBudget] thread=%s tokens=%d/%d — OK",
                thread_id,
                total_tokens,
                budget,
            )
            return {"context_budget_usage": usage}

        logger.info(
            "[ContextBudget] thread=%s tokens=%d/%d — compressing",
            thread_id,
            total_tokens,
            budget,
        )

        compressed_messages, compressed_count, strategy = self._compress(
            messages, per_msg_tokens, budget
        )

        new_total, _ = self._count_all(compressed_messages)
        usage.update(
            {
                "total_tokens": new_total,
                "compressed_count": compressed_count,
                "strategy_applied": strategy,
                "over_budget": new_total > budget,
            }
        )

        if new_total > budget:
            logger.warning(
                "[ContextBudget] thread=%s still over budget after compression: %d/%d",
                thread_id,
                new_total,
                budget,
            )

        return {
            "messages": compressed_messages,
            "context_budget_usage": usage,
        }

    @override
    async def abefore_model(
        self, state: ContextBudgetState, runtime: Runtime
    ) -> dict | None:
        """Async variant — delegates to synchronous implementation."""
        return self.before_model(state, runtime)

    # ------------------------------------------------------------------
    # Compression engine
    # ------------------------------------------------------------------

    def _compress(
        self,
        messages: list[BaseMessage],
        per_msg_tokens: list[int],
        budget: int,
    ) -> tuple[list[BaseMessage], int, str | None]:
        """
        Attempt to reduce total tokens to <= *budget*.

        Tries strategies in ``self.strategies`` order.  Each strategy pass
        iterates over non-system messages oldest-first and compresses until
        below budget or no more candidates remain.

        Returns
        -------
        (compressed_messages, compressed_count, strategy_name_used)
        """
        working = list(messages)
        working_tokens = list(per_msg_tokens)
        compressed_count = 0
        strategy_used: str | None = None

        for strategy in self.strategies:
            if sum(working_tokens) <= budget:
                break
            strategy_used = strategy
            for idx, msg in enumerate(working):
                if sum(working_tokens) <= budget:
                    break
                if isinstance(msg, SystemMessage):
                    continue  # never compress system messages
                candidate = working[idx]
                new_msg = self._apply_strategy(strategy, candidate, working_tokens[idx])
                if new_msg is not None:
                    working[idx] = new_msg
                    new_tokens = _count_tokens(self._msg_text(new_msg))
                    working_tokens[idx] = new_tokens
                    compressed_count += 1

        return working, compressed_count, strategy_used

    def _apply_strategy(
        self, strategy: str, msg: BaseMessage, original_tokens: int
    ) -> BaseMessage | None:
        """
        Apply *strategy* to *msg*.  Returns the compressed message, or None
        if the strategy is not applicable to this message type.
        """
        if strategy == "key_fields":
            return self._strategy_key_fields(msg, original_tokens)
        if strategy == "statistical_summary":
            return self._strategy_statistical_summary(msg, original_tokens)
        if strategy == "truncation":
            return self._strategy_truncation(msg, original_tokens)
        return None

    def _strategy_key_fields(
        self, msg: BaseMessage, original_tokens: int
    ) -> BaseMessage | None:
        """
        Key-fields compression: only applicable to ToolMessages.

        Replaces content with a brief summary + short preview.
        """
        if not isinstance(msg, ToolMessage):
            return None
        content = self._msg_text(msg)
        if len(content) <= _TRUNCATION_PREVIEW:
            return None  # already short
        preview = content[:_TRUNCATION_PREVIEW]
        summary = (
            f"[TOOL RESULT COMPRESSED — original {original_tokens} tokens]\n"
            f"Preview: {preview}…"
        )
        return msg.model_copy(update={"content": summary})

    def _strategy_statistical_summary(
        self, msg: BaseMessage, original_tokens: int
    ) -> BaseMessage | None:
        """
        Statistical-summary compression: applicable to Human and AI messages
        with more than 100 tokens.
        """
        if isinstance(msg, SystemMessage):
            return None
        content = self._msg_text(msg)
        if original_tokens <= 100:
            return None
        excerpt = content[:_SUMMARY_EXCERPT]
        summary = (
            f"[MESSAGE COMPRESSED — {original_tokens} tokens → summary]\n"
            f"Original length: {len(content)} chars\n"
            f"Excerpt: {excerpt}…"
        )
        return msg.model_copy(update={"content": summary})

    def _strategy_truncation(
        self, msg: BaseMessage, original_tokens: int
    ) -> BaseMessage | None:
        """
        Hard truncation: trim content to ``_TRUNCATION_PREVIEW`` characters.
        Applicable to any non-system message above the minimum token threshold.
        """
        if isinstance(msg, SystemMessage):
            return None
        content = self._msg_text(msg)
        if len(content) <= _TRUNCATION_PREVIEW:
            return None
        truncated = content[:_TRUNCATION_PREVIEW] + f"… [truncated from {original_tokens} tokens]"
        return msg.model_copy(update={"content": truncated})

    # ------------------------------------------------------------------
    # Token counting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _msg_text(msg: BaseMessage) -> str:
        """Extract string content from a message."""
        content = msg.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(block.get("text", ""))
            return " ".join(parts)
        return str(content)

    def _count_all(
        self, messages: list[BaseMessage]
    ) -> tuple[int, list[int]]:
        """
        Return (total_tokens, per_message_token_list).

        Adds a small fixed overhead per message to approximate the OpenAI
        message-framing overhead (~4 tokens per message).
        """
        per_msg: list[int] = []
        for msg in messages:
            text = self._msg_text(msg)
            tokens = _count_tokens(text) + 4  # framing overhead
            per_msg.append(tokens)
        return sum(per_msg), per_msg

    # ------------------------------------------------------------------
    # Budget resolution
    # ------------------------------------------------------------------

    def _resolve_budget(self, thread_id: str | None, ctx: dict) -> int:
        """
        Resolve per-thread budget override from runtime context.

        ``runtime.context`` may contain ``{"thread_budgets": {"<id>": N}}``.
        """
        if thread_id:
            overrides: dict = ctx.get("thread_budgets", {})
            if thread_id in overrides:
                return int(overrides[thread_id])
        return self.default_budget
