"""
guardrails.py
=============
Mendicant Bias — Guardrail Middleware

Pre-tool-call authorization.  Evaluates each tool call against a pluggable
``GuardrailProvider`` before execution.  Denied calls receive an error
ToolMessage so the agent can adapt; allowed calls pass through to the real
tool handler.

Provider model
--------------
Any class implementing ``evaluate(GuardrailRequest) -> GuardrailDecision``
(and optionally ``aevaluate`` for async) satisfies the protocol.  Two
built-in providers ship with Mendicant:

* ``AllowlistProvider`` — allow only named tools (empty allowlist = allow all)
* ``DenylistProvider``  — deny named tools, allow everything else

Fail-closed behaviour
---------------------
When ``fail_closed=True`` (default), provider exceptions cause the tool call
to be blocked.  When ``fail_closed=False``, provider exceptions are logged
and the tool call proceeds.

Based on DeerFlow's GuardrailMiddleware, adapted for Mendicant Bias.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, override, runtime_checkable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GuardrailRequest:
    """Context passed to the provider for each tool call."""

    tool_name: str
    tool_input: dict[str, Any]
    agent_id: str | None = None
    thread_id: str | None = None
    timestamp: str = ""


@dataclass
class GuardrailReason:
    """Structured reason for an allow/deny decision."""

    code: str
    message: str = ""


@dataclass
class GuardrailDecision:
    """Provider's allow/deny verdict."""

    allow: bool
    reasons: list[GuardrailReason] = field(default_factory=list)
    policy_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class GuardrailProvider(Protocol):
    """Contract for pluggable tool-call authorization.

    Any class with ``evaluate`` (and optionally ``aevaluate``) satisfies this.
    """

    name: str

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision: ...

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision: ...


# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------


class AllowlistProvider:
    """Allow only tools whose names appear in the allowlist.

    If ``allowed_tools`` is empty/None, all tools are allowed.
    If ``denied_tools`` is provided, those tools are additionally blocked.
    """

    name = "allowlist"

    def __init__(
        self,
        *,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
    ) -> None:
        self._allowed = set(allowed_tools) if allowed_tools else None
        self._denied = set(denied_tools) if denied_tools else set()

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if self._allowed is not None and request.tool_name not in self._allowed:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="mendicant.tool_not_allowed",
                        message=f"tool '{request.tool_name}' not in allowlist",
                    )
                ],
            )
        if request.tool_name in self._denied:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="mendicant.tool_denied",
                        message=f"tool '{request.tool_name}' is denied",
                    )
                ],
            )
        return GuardrailDecision(
            allow=True,
            reasons=[GuardrailReason(code="mendicant.allowed")],
        )

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        return self.evaluate(request)


class DenylistProvider:
    """Deny tools whose names appear in the denylist; allow everything else."""

    name = "denylist"

    def __init__(self, denied_tools: list[str]) -> None:
        self._denied = set(denied_tools)

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if request.tool_name in self._denied:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="mendicant.tool_denied",
                        message=f"tool '{request.tool_name}' is denied",
                    )
                ],
            )
        return GuardrailDecision(
            allow=True,
            reasons=[GuardrailReason(code="mendicant.allowed")],
        )

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        return self.evaluate(request)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class GuardrailMiddleware(AgentMiddleware[AgentState]):
    """Evaluate tool calls against a GuardrailProvider before execution.

    Denied calls return an error ToolMessage so the agent can adapt.
    If the provider raises, behaviour depends on ``fail_closed``:
      - True  (default): block the call
      - False:           allow it through with a warning
    """

    def __init__(
        self,
        provider: GuardrailProvider | AllowlistProvider | DenylistProvider,
        *,
        fail_closed: bool = True,
        passport: str | None = None,
    ) -> None:
        self.provider = provider
        self.fail_closed = fail_closed
        self.passport = passport

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_request(self, request: ToolCallRequest) -> GuardrailRequest:
        return GuardrailRequest(
            tool_name=str(request.tool_call.get("name", "")),
            tool_input=request.tool_call.get("args", {}),
            agent_id=self.passport,
        )

    def _build_denied_message(
        self, request: ToolCallRequest, decision: GuardrailDecision
    ) -> ToolMessage:
        tool_name = str(request.tool_call.get("name", "unknown_tool"))
        tool_call_id = str(request.tool_call.get("id", "missing_id"))
        reason_text = (
            decision.reasons[0].message
            if decision.reasons
            else "blocked by guardrail policy"
        )
        reason_code = (
            decision.reasons[0].code if decision.reasons else "mendicant.denied"
        )
        return ToolMessage(
            content=(
                f"Guardrail denied: tool '{tool_name}' was blocked ({reason_code}). "
                f"Reason: {reason_text}. Choose an alternative approach."
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _fail_closed_decision(self) -> GuardrailDecision:
        return GuardrailDecision(
            allow=False,
            reasons=[
                GuardrailReason(
                    code="mendicant.evaluator_error",
                    message="guardrail provider error (fail-closed)",
                )
            ],
        )

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        gr = self._build_request(request)
        try:
            decision = self.provider.evaluate(gr)
        except Exception:
            logger.exception("Guardrail provider error (sync)")
            if self.fail_closed:
                decision = self._fail_closed_decision()
            else:
                return handler(request)
        if not decision.allow:
            logger.warning(
                "Guardrail denied: tool=%s code=%s",
                gr.tool_name,
                decision.reasons[0].code if decision.reasons else "unknown",
            )
            return self._build_denied_message(request, decision)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        gr = self._build_request(request)
        try:
            decision = await self.provider.aevaluate(gr)
        except Exception:
            logger.exception("Guardrail provider error (async)")
            if self.fail_closed:
                decision = self._fail_closed_decision()
            else:
                return await handler(request)
        if not decision.allow:
            logger.warning(
                "Guardrail denied: tool=%s code=%s",
                gr.tool_name,
                decision.reasons[0].code if decision.reasons else "unknown",
            )
            return self._build_denied_message(request, decision)
        return await handler(request)
