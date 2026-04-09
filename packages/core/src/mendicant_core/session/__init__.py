"""
Mendicant Bias V5 — Session State Manager

Tracks per-session state across Claude Code turns:
- Task classification (cached, not re-classified every turn)
- Verification history (accumulated pass/fail)
- Tool call log (for pattern recording)
- Pending verification results (injected as context on next turn)
- Memory injection status
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import threading

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """State for a single Claude Code session."""

    session_id: str
    task_classification: dict[str, Any] | None = None
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    memory_injected: bool = False
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    pending_context: str | None = None  # Injected as additionalContext on next hook
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def log_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
    ) -> None:
        """Append a tool call record to the session log."""
        self.tool_call_log.append(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_output[:500] if tool_output else None,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        )
        self.last_activity = datetime.now(tz=timezone.utc)

    def add_verification_result(self, result: dict[str, Any]) -> None:
        """Append a verification result to the session history."""
        self.verification_history.append(
            {
                **result,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        )
        self.last_activity = datetime.now(tz=timezone.utc)

    @property
    def verification_pass_rate(self) -> float | None:
        """Return the fraction of verifications that passed, or None if no history."""
        if not self.verification_history:
            return None
        passed = sum(
            1
            for v in self.verification_history
            if v.get("verdict", "").upper() in ("CORRECT", "PASS")
        )
        return passed / len(self.verification_history)

    @property
    def age_seconds(self) -> float:
        """Return the age of this session in seconds."""
        now = datetime.now(tz=timezone.utc)
        return (now - self.created_at).total_seconds()


class SessionStateManager:
    """Thread-safe in-memory session state store."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> SessionState:
        """Get existing session or create a new one. Updates last_activity."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState(session_id=session_id)
                logger.info("[Session] Created new session: %s", session_id)
            session = self._sessions[session_id]
            session.last_activity = datetime.now(tz=timezone.utc)
            return session

    def get(self, session_id: str) -> SessionState | None:
        """Get a session by ID, or None if it doesn't exist."""
        with self._lock:
            return self._sessions.get(session_id)

    def consume_pending_context(self, session_id: str) -> str | None:
        """Get and clear pending context for injection into the next hook response."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.pending_context:
                ctx = session.pending_context
                session.pending_context = None
                return ctx
            return None

    def set_pending_context(self, session_id: str, context: str) -> None:
        """Set pending context to be injected on the next hook call."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.pending_context = context
            else:
                logger.warning(
                    "[Session] set_pending_context called for unknown session: %s",
                    session_id,
                )

    def cleanup_expired(self, max_age_hours: int = 4) -> int:
        """Remove sessions older than max_age_hours. Returns count removed."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
        removed = 0
        with self._lock:
            expired_ids = [
                sid
                for sid, session in self._sessions.items()
                if session.last_activity < cutoff
            ]
            for sid in expired_ids:
                del self._sessions[sid]
                removed += 1
        if removed:
            logger.info("[Session] Cleaned up %d expired sessions", removed)
        return removed

    def active_count(self) -> int:
        """Return the number of active sessions."""
        with self._lock:
            return len(self._sessions)

    def get_all_stats(self) -> dict[str, Any]:
        """Return aggregate statistics for all sessions."""
        with self._lock:
            total = len(self._sessions)
            total_tool_calls = sum(
                len(s.tool_call_log) for s in self._sessions.values()
            )
            total_verifications = sum(
                len(s.verification_history) for s in self._sessions.values()
            )
            return {
                "active_sessions": total,
                "total_tool_calls": total_tool_calls,
                "total_verifications": total_verifications,
            }
