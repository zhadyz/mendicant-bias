"""
verification.py
===============
Mendicant Bias V5 — FR2: Blind LLM Quality Gate (Aletheia-style)

A LangGraph AgentMiddleware that fires as an after_agent hook.  It runs a
two-stage, model-blind verification pass whenever the agent response contains
``write_file`` or ``str_replace`` tool calls:

  Stage 1 — Pre-analysis
      Ask a fresh LLM instance "What should correct behaviour look like for
      this task?" *without* showing it the agent's actual output.

  Stage 2 — Grading
      Show the same LLM both the pre-analysis criteria and the actual agent
      output; ask it to grade pass / fixable / fail with a confidence score.

Verdicts
--------
CORRECT  → return None  (no state change)
FIXABLE  → return {"messages": [HumanMessage("[VERIFICATION FEEDBACK]: …")]}
WRONG    → log error + return {"messages": [HumanMessage("[VERIFICATION ERROR]: …")]}

The middleware is enabled by default.  Set ``verification_enabled=False`` in
``runtime.context`` to skip the check for a given invocation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, NotRequired, override

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain.agents.middleware import AgentMiddleware
from langchain.agents import AgentState
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRIGGER_TOOL_NAMES = frozenset({"write_file", "str_replace"})

_PRE_ANALYSIS_SYSTEM = (
    "You are a code-quality analyst. "
    "You will be given a user request and must describe, concisely, "
    "what a CORRECT, complete implementation looks like — without seeing the "
    "actual agent output. Focus on: correctness, completeness, safety, and "
    "adherence to the stated requirements."
)

_GRADING_SYSTEM = (
    "You are a strict code-quality grader. "
    "You will be given (1) quality criteria and (2) the agent's actual output. "
    "Grade the output as one of:\n"
    "  CORRECT   — meets all criteria\n"
    "  FIXABLE   — has minor issues that can be corrected with feedback\n"
    "  WRONG     — fundamentally incorrect or unsafe\n\n"
    "Respond with a JSON object:\n"
    '{"verdict": "CORRECT"|"FIXABLE"|"WRONG", "confidence": 0.0-1.0, '
    '"reasoning": "...", "feedback": "..."}\n'
    "No other text."
)

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class VerificationState(AgentState):
    """Extended agent state carrying verification metadata."""

    verification_result: NotRequired[dict[str, Any] | None]
    verification_verdict: NotRequired[str | None]
    verification_feedback: NotRequired[str | None]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class VerificationMiddleware(AgentMiddleware[VerificationState]):
    """
    Blind LLM quality-gate middleware.

    Parameters
    ----------
    model_name : str
        LLM model identifier passed to ``create_chat_model()``.
    temperature : float
        Sampling temperature for grading calls (default: ``0.1``).
    fixable_threshold : float
        Minimum confidence required to emit FIXABLE feedback rather than
        treating a borderline case as CORRECT (default: ``0.6``).
    wrong_threshold : float
        If WRONG verdict confidence is below this value the middleware
        degrades the verdict to FIXABLE (default: ``0.3``).
    max_retries : int
        Number of JSON-parse retries on malformed grading responses (default: ``2``).
    model_factory : Callable[[], Any] | None
        Optional callable that returns an LLM instance.  If provided, this
        is used instead of the default langchain_openai.ChatOpenAI fallback.
    """

    state_schema = VerificationState

    def __init__(
        self,
        *,
        model_name: str = "claude-sonnet-4-20250514",
        temperature: float = 0.1,
        fixable_threshold: float = 0.6,
        wrong_threshold: float = 0.3,
        max_retries: int = 2,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__()
        self.model_name = model_name
        self.temperature = temperature
        self.fixable_threshold = fixable_threshold
        self.wrong_threshold = wrong_threshold
        self.max_retries = max_retries
        self._model_factory = model_factory

    # ------------------------------------------------------------------
    # LangGraph hooks
    # ------------------------------------------------------------------

    @override
    def after_agent(
        self, state: VerificationState, runtime: Runtime
    ) -> dict | None:
        """
        Synchronous after_agent hook.

        Returns a state delta containing a feedback HumanMessage when the
        verdict is FIXABLE or WRONG, otherwise returns ``None``.
        """
        ctx = runtime.context or {}
        if not ctx.get("verification_enabled", True):
            logger.debug("[Verification] Disabled via runtime.context; skipping.")
            return None

        thread_id: str | None = ctx.get("thread_id")
        messages: list[BaseMessage] = state.get("messages", [])

        if not self._has_trigger_tool_calls(messages):
            logger.debug(
                "[Verification] No write_file/str_replace calls found; skipping."
            )
            return None

        # Build context strings
        user_task = self._extract_user_task(messages)
        agent_output_summary = self._summarise_agent_output(messages)

        # Stage 1: blind pre-analysis
        criteria = self._run_pre_analysis(user_task)
        if not criteria:
            logger.warning("[Verification] Pre-analysis returned empty; skipping.")
            return None

        # Stage 2: grading
        result = self._run_grading(user_task, agent_output_summary, criteria)
        verdict = result.get("verdict", "CORRECT")
        confidence: float = float(result.get("confidence", 1.0))
        feedback: str = result.get("feedback", "")
        reasoning: str = result.get("reasoning", "")

        logger.info(
            "[Verification] thread=%s verdict=%s confidence=%.2f",
            thread_id,
            verdict,
            confidence,
        )

        state_delta: dict[str, Any] = {
            "verification_result": result,
            "verification_verdict": verdict,
            "verification_feedback": feedback,
        }

        if verdict == "CORRECT":
            return state_delta  # no new message needed

        if verdict == "FIXABLE" and confidence >= self.fixable_threshold:
            feedback_msg = HumanMessage(
                content=(
                    f"[VERIFICATION FEEDBACK]: The previous response has issues that "
                    f"should be corrected.\n\nReasoning: {reasoning}\n\n"
                    f"Suggested fix: {feedback}"
                )
            )
            state_delta["messages"] = [feedback_msg]
            return state_delta

        if verdict == "WRONG":
            if confidence < self.wrong_threshold:
                # Low-confidence WRONG → downgrade to FIXABLE feedback
                feedback_msg = HumanMessage(
                    content=(
                        f"[VERIFICATION FEEDBACK]: The previous response may have "
                        f"issues.\n\nReasoning: {reasoning}\n\nSuggested fix: {feedback}"
                    )
                )
                state_delta["messages"] = [feedback_msg]
                state_delta["verification_verdict"] = "FIXABLE"
                return state_delta

            logger.error(
                "[Verification] WRONG verdict (confidence=%.2f): %s",
                confidence,
                reasoning,
            )
            error_msg = HumanMessage(
                content=(
                    f"[VERIFICATION ERROR]: The previous response is fundamentally "
                    f"incorrect and cannot be used.\n\nReasoning: {reasoning}\n\n"
                    f"Required correction: {feedback}"
                )
            )
            state_delta["messages"] = [error_msg]
            return state_delta

        # Borderline FIXABLE below threshold → treat as CORRECT
        return state_delta

    @override
    async def aafter_agent(
        self, state: VerificationState, runtime: Runtime
    ) -> dict | None:
        """Async variant — delegates to synchronous implementation."""
        return self.after_agent(state, runtime)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _has_trigger_tool_calls(self, messages: list[BaseMessage]) -> bool:
        """
        Return True if any recent AIMessage contains a write_file or
        str_replace tool call.
        """
        # Check the last 10 messages for efficiency
        for msg in reversed(messages[-10:]):
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    name = (
                        tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    )
                    if name in _TRIGGER_TOOL_NAMES:
                        return True
        return False

    def _extract_tool_calls(self, messages: list[BaseMessage]) -> list[dict]:
        """Extract all tool call dicts from the most recent AIMessage."""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                result = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        result.append(tc)
                    else:
                        result.append({"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {})})
                return result
        return []

    def _extract_user_task(self, messages: list[BaseMessage]) -> str:
        """Extract the last human message content as the user task description."""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content
                if isinstance(content, str):
                    return content[:2000]  # cap for prompt length
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                    return " ".join(parts)[:2000]
        return "(no user message found)"

    def _summarise_agent_output(self, messages: list[BaseMessage]) -> str:
        """
        Build a compact text summary of the agent's tool calls and responses
        for use in the grading prompt.
        """
        parts: list[str] = []
        for msg in messages[-20:]:  # scan last 20 messages
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    parts.append(f"TOOL_CALL: {name}\nARGS: {json.dumps(args, indent=2)[:500]}")
                if msg.content:
                    parts.append(f"AI_RESPONSE: {str(msg.content)[:500]}")
            elif isinstance(msg, ToolMessage):
                parts.append(f"TOOL_RESULT ({msg.name}): {str(msg.content)[:300]}")
        return "\n\n".join(parts) if parts else "(no agent output captured)"

    def _build_pre_analysis_prompt(self, user_task: str) -> str:
        """Build the Stage-1 pre-analysis prompt."""
        return (
            f"User task:\n{user_task}\n\n"
            "Describe what a CORRECT, complete implementation of this task looks "
            "like. Be specific about expected file contents, correctness criteria, "
            "and any safety requirements. Do NOT reference any actual implementation."
        )

    def _build_grading_prompt(
        self, user_task: str, agent_output: str, criteria: str
    ) -> str:
        """Build the Stage-2 grading prompt."""
        return (
            f"QUALITY CRITERIA (from pre-analysis):\n{criteria}\n\n"
            f"USER TASK:\n{user_task}\n\n"
            f"AGENT OUTPUT:\n{agent_output}\n\n"
            "Grade the agent output against the criteria. "
            "Return only valid JSON as specified in the system prompt."
        )

    def _get_llm(self) -> Any:
        """
        Instantiate the LLM for verification.

        Uses the ``model_factory`` callable if one was provided at
        construction time.  Otherwise tries langchain_anthropic (Claude)
        first, then falls back to langchain_openai.
        """
        if self._model_factory is not None:
            return self._model_factory()

        # Try Claude (Anthropic) first — default model is now Claude
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore

            return ChatAnthropic(
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=2048,
            )
        except ImportError:
            logger.debug("[Verification] langchain-anthropic not installed, trying OpenAI")

        # Fallback: try langchain_openai
        try:
            from langchain_openai import ChatOpenAI  # type: ignore

            return ChatOpenAI(model=self.model_name, temperature=self.temperature)
        except ImportError as exc:
            raise RuntimeError(
                "Could not import langchain_anthropic or langchain_openai. "
                "Install langchain-anthropic or langchain-openai, or provide "
                "a model_factory callable."
            ) from exc

    def _run_pre_analysis(self, user_task: str) -> str:
        """Run Stage-1: blind pre-analysis. Returns criteria text."""
        from langchain_core.messages import SystemMessage

        llm = self._get_llm()
        prompt = self._build_pre_analysis_prompt(user_task)
        try:
            response = llm.invoke(
                [
                    SystemMessage(content=_PRE_ANALYSIS_SYSTEM),
                    HumanMessage(content=prompt),
                ]
            )
            return str(response.content).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("[Verification] Pre-analysis LLM call failed: %s", exc)
            return ""

    def _run_grading(
        self, user_task: str, agent_output: str, criteria: str
    ) -> dict[str, Any]:
        """
        Run Stage-2: grading.  Returns a dict with keys
        ``verdict``, ``confidence``, ``reasoning``, ``feedback``.
        """
        from langchain_core.messages import SystemMessage

        llm = self._get_llm()
        prompt = self._build_grading_prompt(user_task, agent_output, criteria)

        for attempt in range(self.max_retries + 1):
            try:
                response = llm.invoke(
                    [
                        SystemMessage(content=_GRADING_SYSTEM),
                        HumanMessage(content=prompt),
                    ]
                )
                return self._parse_verdict(str(response.content))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[Verification] Grading attempt %d/%d failed: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )

        # All retries exhausted — default to CORRECT to avoid blocking
        logger.error("[Verification] All grading retries exhausted; defaulting CORRECT.")
        return {"verdict": "CORRECT", "confidence": 0.0, "reasoning": "grading_failed", "feedback": ""}

    def _parse_verdict(self, raw: str) -> dict[str, Any]:
        """
        Parse the grading LLM response into a structured verdict dict.

        Tries strict JSON first; falls back to regex extraction.
        """
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()

        try:
            data = json.loads(cleaned)
            verdict = str(data.get("verdict", "CORRECT")).upper()
            if verdict not in {"CORRECT", "FIXABLE", "WRONG"}:
                verdict = "CORRECT"
            return {
                "verdict": verdict,
                "confidence": float(data.get("confidence", 1.0)),
                "reasoning": str(data.get("reasoning", "")),
                "feedback": str(data.get("feedback", "")),
            }
        except json.JSONDecodeError:
            pass

        # Regex fallback
        verdict_match = re.search(r'"verdict"\s*:\s*"(CORRECT|FIXABLE|WRONG)"', raw, re.IGNORECASE)
        conf_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
        reason_match = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw)
        feedback_match = re.search(r'"feedback"\s*:\s*"([^"]+)"', raw)

        return {
            "verdict": (verdict_match.group(1).upper() if verdict_match else "CORRECT"),
            "confidence": float(conf_match.group(1)) if conf_match else 1.0,
            "reasoning": reason_match.group(1) if reason_match else "",
            "feedback": feedback_match.group(1) if feedback_match else "",
        }
