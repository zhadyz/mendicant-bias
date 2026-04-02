"""
mendicant_gateway.hooks
=======================
Mendicant Bias V5 — Claude Code HTTP Hook Endpoints (Phase 3)

FastAPI router providing three POST endpoints that match Claude Code's hook
protocol.  When configured via settings.json, CC calls these hooks at:

  - SessionStart  -> POST /hooks/session-start
  - PreToolUse    -> POST /hooks/pre-tool-use
  - PostToolUse   -> POST /hooks/post-tool-use

Each endpoint receives the CC hook input JSON, processes it through the
Mendicant middleware stack, and returns the CC-expected response format:

    {
      "continue": true,
      "hookSpecificOutput": {
        "hookEventName": "<event>",
        "additionalContext": "..."
      }
    }

Integration Points
------------------
- SessionStateManager: Tracks per-session classification, tool log, pending context
- MemoryStore + MemoryInjector: Loads memory on session start
- SmartTaskRouterMiddleware: Classifies tasks via keyword heuristics
- PatternStore: Records tool usage patterns

References
----------
Claude Code hooks specification for HTTP hooks:
  https://docs.anthropic.com/en/docs/claude-code/hooks
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

hooks_router = APIRouter(prefix="/hooks", tags=["hooks"])

# ---------------------------------------------------------------------------
# Session state — singleton, initialised on first import
# ---------------------------------------------------------------------------

from mendicant_core.session import SessionStateManager

_session_mgr = SessionStateManager()

# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------


class HookInput(BaseModel):
    """Generic CC hook input — fields vary by event type."""

    hook_event_name: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | Any | None = None
    tool_output: str | Any | None = None

    # CC may send additional fields; allow them
    model_config = {"extra": "allow"}


class HookSpecificOutput(BaseModel):
    """The hookSpecificOutput portion of the CC response."""

    hookEventName: str
    additionalContext: str | None = None


class HookResponse(BaseModel):
    """Top-level response format expected by Claude Code."""

    # continue=True means "allow the action to proceed"
    # continue=False would block the tool call (used for safety gates)
    model_config = {"populate_by_name": True}

    # "continue" is a Python keyword, so we use an alias
    should_continue: bool = Field(default=True, alias="continue")
    hookSpecificOutput: HookSpecificOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_session_id(body: HookInput) -> str:
    """Extract session_id from the hook input, or generate a fallback."""
    if body.session_id:
        return body.session_id
    # Try to find it in extra fields
    extra = getattr(body, "__pydantic_extra__", {}) or {}
    sid = extra.get("session_id") or extra.get("sessionId")
    if sid:
        return str(sid)
    return f"anonymous-{uuid.uuid4().hex[:8]}"


def _load_memory_context() -> str:
    """Load memory from MemoryStore and format for injection."""
    try:
        from mendicant_core.memory import MemoryInjector, MemoryStore

        store_path = os.environ.get(
            "MENDICANT_MEMORY_PATH", ".mendicant/memory.json"
        )
        store = MemoryStore(storage_path=store_path)
        data = store.load()
        injector = MemoryInjector(max_tokens=2000)
        return injector.format_for_injection(data)
    except Exception as exc:
        logger.warning("[Hooks] Failed to load memory: %s", exc)
        return ""


def _classify_task_text(text: str) -> dict[str, Any]:
    """Run keyword-based task classification on the given text."""
    try:
        from mendicant_core.middleware.smart_task_router import (
            SmartTaskRouterMiddleware,
            _FLAGS,
        )

        router = SmartTaskRouterMiddleware()
        task_type, confidence = router._classify_keywords(text)
        flags = _FLAGS.get(task_type, _FLAGS.get("SIMPLE", {}))
        return {
            "task_type": task_type,
            "confidence": confidence,
            **flags,
        }
    except Exception as exc:
        logger.warning("[Hooks] Task classification failed: %s", exc)
        return {
            "task_type": "SIMPLE",
            "confidence": 0.0,
            "verification_enabled": False,
            "subagent_enabled": False,
            "thinking_enabled": False,
        }


def _extract_text_from_tool_input(tool_input: Any) -> str:
    """Extract classifiable text from a CC tool_input payload."""
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, dict):
        # Combine common text fields
        parts = []
        for key in ("command", "content", "query", "prompt", "message", "file_path", "description"):
            val = tool_input.get(key)
            if val and isinstance(val, str):
                parts.append(val)
        return " ".join(parts)
    return ""


def _should_auto_verify(
    tool_name: str | None,
    classification: dict[str, Any] | None,
    auto_verify_tools: list[str] | None = None,
    auto_verify_types: list[str] | None = None,
) -> bool:
    """Determine whether automatic verification should run for this tool call."""
    if auto_verify_tools is None:
        auto_verify_tools = ["Write", "Edit", "FileWrite", "FileEdit"]
    if auto_verify_types is None:
        auto_verify_types = ["CODE_GENERATION", "CRITICAL_CODE"]

    if not tool_name or tool_name not in auto_verify_tools:
        return False
    if not classification:
        return False
    return classification.get("task_type", "SIMPLE") in auto_verify_types


def _run_verification(task_text: str, output_text: str) -> dict[str, Any]:
    """Run synchronous verification and return result dict."""
    try:
        from mendicant_core import MendicantConfig

        config = MendicantConfig()
        if not config.verification.enabled:
            return {"verdict": "SKIPPED", "confidence": 0.0, "reasoning": "Verification disabled"}

        verifier = config.build_verification_middleware()
        criteria = verifier._run_pre_analysis(task_text)
        if not criteria:
            return {
                "verdict": "SKIPPED",
                "confidence": 0.0,
                "reasoning": "Pre-analysis returned empty",
            }
        result = verifier._run_grading(task_text, output_text, criteria)
        return result
    except Exception as exc:
        logger.error("[Hooks] Verification failed: %s", exc)
        return {"verdict": "ERROR", "confidence": 0.0, "reasoning": str(exc)}


# ---------------------------------------------------------------------------
# POST /hooks/session-start
# ---------------------------------------------------------------------------


@hooks_router.post("/session-start")
async def hook_session_start(body: HookInput) -> dict[str, Any]:
    """
    Handle CC SessionStart hook.

    Creates session state, loads memory via MemoryStore + MemoryInjector,
    and returns initial context for the session.
    """
    session_id = _resolve_session_id(body)
    session = _session_mgr.get_or_create(session_id)

    logger.info("[Hooks] SessionStart for session=%s", session_id)

    # Load and inject memory if not already done
    context_parts: list[str] = []
    if not session.memory_injected:
        memory_text = _load_memory_context()
        if memory_text:
            context_parts.append(memory_text)
        session.memory_injected = True

    context_parts.append(
        "\n[Mendicant Bias active. "
        "Task classification and auto-verification enabled.]"
    )

    additional_context = "\n".join(context_parts)

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        },
    }


# ---------------------------------------------------------------------------
# POST /hooks/pre-tool-use
# ---------------------------------------------------------------------------


@hooks_router.post("/pre-tool-use")
async def hook_pre_tool_use(body: HookInput) -> dict[str, Any]:
    """
    Handle CC PreToolUse hook.

    - Ensures session exists
    - Classifies task if not yet classified for this session
    - Logs the tool call
    - Returns any pending verification context from a previous turn
    """
    session_id = _resolve_session_id(body)
    session = _session_mgr.get_or_create(session_id)

    tool_name = body.tool_name or "unknown"
    tool_input = body.tool_input

    logger.info(
        "[Hooks] PreToolUse session=%s tool=%s",
        session_id,
        tool_name,
    )

    # Classify task if not yet done for this session
    if session.task_classification is None:
        text = _extract_text_from_tool_input(tool_input)
        if text:
            session.task_classification = _classify_task_text(text)
            logger.info(
                "[Hooks] Classified session=%s as %s (conf=%.2f)",
                session_id,
                session.task_classification.get("task_type"),
                session.task_classification.get("confidence", 0.0),
            )

    # Log the tool call (output not yet available at pre-tool stage)
    session.log_tool_call(
        tool_name=tool_name,
        tool_input=tool_input if isinstance(tool_input, dict) else {"raw": str(tool_input)},
    )

    # Consume any pending context from previous PostToolUse verification
    context_parts: list[str] = []
    pending = _session_mgr.consume_pending_context(session_id)
    if pending:
        context_parts.append(pending)

    # Add classification info on first tool use
    if session.task_classification and len(session.tool_call_log) <= 1:
        cls = session.task_classification
        context_parts.append(
            f"[Task classified as {cls.get('task_type', 'UNKNOWN')} "
            f"(confidence: {cls.get('confidence', 0):.0%}). "
            f"Verification: {'on' if cls.get('verification_enabled') else 'off'}. "
            f"Thinking: {'on' if cls.get('thinking_enabled') else 'off'}.]"
        )

    additional_context = "\n".join(context_parts) if context_parts else None

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": additional_context,
        },
    }


# ---------------------------------------------------------------------------
# POST /hooks/post-tool-use
# ---------------------------------------------------------------------------


@hooks_router.post("/post-tool-use")
async def hook_post_tool_use(body: HookInput) -> dict[str, Any]:
    """
    Handle CC PostToolUse hook.

    - Updates tool call log with output
    - If tool is Write/Edit and task is CODE_GENERATION/CRITICAL_CODE:
      - Runs verification synchronously
      - Stores result in session.verification_history
      - Sets pending_context with verification feedback
    - Records tool usage for pattern store
    """
    session_id = _resolve_session_id(body)
    session = _session_mgr.get_or_create(session_id)

    tool_name = body.tool_name or "unknown"
    tool_input = body.tool_input
    tool_output = body.tool_output

    logger.info(
        "[Hooks] PostToolUse session=%s tool=%s",
        session_id,
        tool_name,
    )

    # Update the most recent tool call log entry with output
    output_str = str(tool_output)[:2000] if tool_output else ""
    if session.tool_call_log:
        last_entry = session.tool_call_log[-1]
        if last_entry.get("tool_name") == tool_name and last_entry.get("tool_output") is None:
            last_entry["tool_output"] = output_str[:500]

    context_parts: list[str] = []

    # Check if we should run auto-verification
    if _should_auto_verify(tool_name, session.task_classification):
        logger.info(
            "[Hooks] Running auto-verification for tool=%s session=%s",
            tool_name,
            session_id,
        )

        # Build a task description from the tool input
        task_text = _extract_text_from_tool_input(tool_input)
        if not task_text:
            task_text = f"Code written/edited via {tool_name}"

        verification_result = _run_verification(task_text, output_str)
        session.add_verification_result(
            {
                "tool_name": tool_name,
                "verdict": verification_result.get("verdict", "UNKNOWN"),
                "confidence": verification_result.get("confidence", 0.0),
                "reasoning": verification_result.get("reasoning", ""),
            }
        )

        verdict = verification_result.get("verdict", "UNKNOWN")
        confidence = verification_result.get("confidence", 0.0)
        reasoning = verification_result.get("reasoning", "")
        feedback = verification_result.get("feedback", "")

        # Build feedback context for the next turn
        if verdict in ("INCORRECT", "FAIL", "ERROR"):
            verification_msg = (
                f"[Mendicant Verification FAILED for {tool_name}]\n"
                f"Verdict: {verdict} (confidence: {confidence:.0%})\n"
                f"Reasoning: {reasoning}\n"
            )
            if feedback:
                verification_msg += f"Feedback: {feedback}\n"
            verification_msg += (
                "Please review and correct the issues identified above."
            )
            # Set as pending context for the next PreToolUse hook
            _session_mgr.set_pending_context(session_id, verification_msg)
            context_parts.append(verification_msg)
        elif verdict == "SKIPPED":
            logger.debug(
                "[Hooks] Verification skipped for session=%s: %s",
                session_id,
                reasoning,
            )
        else:
            # CORRECT / PASS
            pass_msg = (
                f"[Mendicant Verification PASSED for {tool_name}] "
                f"(confidence: {confidence:.0%})"
            )
            context_parts.append(pass_msg)

        # Log pass rate
        pass_rate = session.verification_pass_rate
        if pass_rate is not None:
            logger.info(
                "[Hooks] Session %s verification pass rate: %.0f%% (%d checks)",
                session_id,
                pass_rate * 100,
                len(session.verification_history),
            )

    # Record tool usage for adaptive learning patterns
    try:
        _record_tool_pattern(session, tool_name, tool_input, output_str)
    except Exception as exc:
        logger.debug("[Hooks] Pattern recording failed: %s", exc)

    additional_context = "\n".join(context_parts) if context_parts else None

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        },
    }


# ---------------------------------------------------------------------------
# Pattern recording
# ---------------------------------------------------------------------------


def _record_tool_pattern(
    session: Any,
    tool_name: str,
    tool_input: Any,
    output_preview: str,
) -> None:
    """Record tool usage in the pattern store for adaptive learning."""
    try:
        from mendicant_core.patterns import PatternStore

        store_path = os.environ.get(
            "MENDICANT_PATTERNS_PATH",
            ".mendicant/orchestration_patterns.json",
        )
        store = PatternStore(store_path=store_path)
        task_type = (
            session.task_classification.get("task_type", "UNKNOWN")
            if session.task_classification
            else "UNKNOWN"
        )
        store.record(
            {
                "session_id": session.session_id,
                "tool_name": tool_name,
                "task_type": task_type,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "success": True,  # Refined later by verification
            }
        )
    except Exception as exc:
        # Pattern recording is best-effort; never block the hook response
        logger.debug("[Hooks] Could not record pattern: %s", exc)


# ---------------------------------------------------------------------------
# Status endpoint for session manager
# ---------------------------------------------------------------------------


@hooks_router.get("/status")
async def hooks_status() -> dict[str, Any]:
    """Return current hook system status and session stats."""
    return {
        "hooks_active": True,
        "sessions": _session_mgr.get_all_stats(),
    }
