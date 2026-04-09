"""
mendicant_gateway.brain_routes
==============================
REST endpoints for the Brain Dashboard's initial data load and state queries.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

brain_router = APIRouter(prefix="/api/brain", tags=["brain"])


def _get_mendicant_dir() -> Path:
    """Resolve the .mendicant data directory."""
    candidates = [
        Path.cwd() / ".mendicant",
        Path.home() / ".mendicant",
    ]
    for p in candidates:
        if p.exists():
            return p
    return Path.cwd() / ".mendicant"


# ---------------------------------------------------------------------------
# GET /api/brain/state — Current session state snapshot
# ---------------------------------------------------------------------------


@brain_router.get("/state")
async def brain_state() -> dict[str, Any]:
    """Returns current hook system state for the dashboard."""
    try:
        from mendicant_core.session import SessionStateManager

        manager = SessionStateManager()

        session_summaries = []
        for sid, state in list(manager._sessions.items()):
                session_summaries.append({
                    "session_id": sid,
                    "task_classification": getattr(state, "task_classification", None),
                    "tool_count": len(getattr(state, "tool_call_log", [])),
                    "verification_count": len(getattr(state, "verification_history", [])),
                    "memory_injected": getattr(state, "memory_injected", False),
                    "created_at": getattr(state, "created_at", None),
                    "last_activity": getattr(state, "last_activity", None),
                })

        return {
            "sessions": session_summaries,
            "session_count": len(session_summaries),
            "timestamp": time.time(),
        }
    except Exception as exc:
        logger.debug("[Brain] Failed to load session state: %s", exc)
        return {"sessions": [], "session_count": 0, "timestamp": time.time()}


# ---------------------------------------------------------------------------
# GET /api/brain/mahoraga — Adaptation rules + stats
# ---------------------------------------------------------------------------


@brain_router.get("/mahoraga")
async def brain_mahoraga() -> dict[str, Any]:
    """Returns Mahoraga adaptation engine stats and active rules."""
    try:
        from mendicant_core.mahoraga import MahoragaEngine

        mendicant_dir = _get_mendicant_dir()
        engine = MahoragaEngine(store_path=str(mendicant_dir / "mahoraga.json"))

        stats = engine.get_stats()

        # Get all active rules serialized
        rules = []
        for rule in engine.rules:
            if rule.active:
                rules.append({
                    "id": rule.id,
                    "category": rule.category,
                    "trigger": rule.trigger,
                    "action": rule.action,
                    "confidence": rule.confidence,
                    "source": rule.source,
                    "apply_count": rule.apply_count,
                    "success_count": rule.success_count,
                    "failure_count": rule.failure_count,
                    "tags": rule.tags,
                    "last_applied": rule.last_applied,
                })

        return {"stats": stats, "rules": rules}
    except Exception as exc:
        logger.debug("[Brain] Failed to load Mahoraga state: %s", exc)
        return {
            "stats": {
                "total_rules": 0, "active_rules": 0,
                "by_category": {}, "success_rate": 0.0,
            },
            "rules": [],
        }


# ---------------------------------------------------------------------------
# GET /api/brain/sessions — Detailed session list
# ---------------------------------------------------------------------------


@brain_router.get("/sessions")
async def brain_sessions() -> dict[str, Any]:
    """Returns detailed session list from SessionStateManager."""
    try:
        from mendicant_core.session import SessionStateManager

        manager = SessionStateManager()

        details = []
        for sid, state in list(manager._sessions.items()):
                tool_log = getattr(state, "tool_call_log", [])
                verifications = getattr(state, "verification_history", [])
                created = getattr(state, "created_at", None)

                age = None
                if created:
                    try:
                        age = time.time() - created
                    except (TypeError, ValueError):
                        pass

                details.append({
                    "session_id": sid,
                    "task_classification": getattr(state, "task_classification", None),
                    "tool_count": len(tool_log),
                    "verification_count": len(verifications),
                    "age_seconds": age,
                    "tools_used": list({
                        entry.get("tool_name", "unknown")
                        for entry in tool_log
                        if isinstance(entry, dict)
                    }),
                })

        return {"sessions": details, "count": len(details)}
    except Exception as exc:
        logger.debug("[Brain] Failed to load sessions: %s", exc)
        return {"sessions": [], "count": 0}
