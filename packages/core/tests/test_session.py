"""
Tests for mendicant_core.session — SessionState and SessionStateManager.

Covers:
- SessionState creation and defaults
- Tool call logging
- Verification result tracking + pass rate
- Pending context consume/set lifecycle
- Cleanup of expired sessions
- Thread safety under concurrent access
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from mendicant_core.session import SessionState, SessionStateManager


# ---------------------------------------------------------------------------
# SessionState basic tests
# ---------------------------------------------------------------------------


class TestSessionState:
    def test_creation_defaults(self):
        state = SessionState(session_id="test-1")
        assert state.session_id == "test-1"
        assert state.task_classification is None
        assert state.verification_history == []
        assert state.memory_injected is False
        assert state.tool_call_log == []
        assert state.pending_context is None
        assert isinstance(state.created_at, datetime)
        assert isinstance(state.last_activity, datetime)

    def test_log_tool_call(self):
        state = SessionState(session_id="test-2")
        state.log_tool_call(
            tool_name="Write",
            tool_input={"file_path": "/tmp/test.py", "content": "print('hi')"},
        )
        assert len(state.tool_call_log) == 1
        entry = state.tool_call_log[0]
        assert entry["tool_name"] == "Write"
        assert entry["tool_input"]["file_path"] == "/tmp/test.py"
        assert entry["tool_output"] is None
        assert "timestamp" in entry

    def test_log_tool_call_truncates_output(self):
        state = SessionState(session_id="test-3")
        long_output = "x" * 1000
        state.log_tool_call(tool_name="Bash", tool_output=long_output)
        assert len(state.tool_call_log[0]["tool_output"]) == 500

    def test_add_verification_result(self):
        state = SessionState(session_id="test-4")
        state.add_verification_result(
            {"verdict": "CORRECT", "confidence": 0.95, "reasoning": "Looks good"}
        )
        state.add_verification_result(
            {"verdict": "INCORRECT", "confidence": 0.8, "reasoning": "Bug found"}
        )
        assert len(state.verification_history) == 2
        assert state.verification_history[0]["verdict"] == "CORRECT"
        assert state.verification_history[1]["verdict"] == "INCORRECT"
        assert "timestamp" in state.verification_history[0]

    def test_verification_pass_rate_none_when_empty(self):
        state = SessionState(session_id="test-5")
        assert state.verification_pass_rate is None

    def test_verification_pass_rate_calculation(self):
        state = SessionState(session_id="test-6")
        state.add_verification_result({"verdict": "CORRECT"})
        state.add_verification_result({"verdict": "PASS"})
        state.add_verification_result({"verdict": "INCORRECT"})
        # 2 out of 3 passed
        assert state.verification_pass_rate == pytest.approx(2 / 3)

    def test_verification_pass_rate_all_pass(self):
        state = SessionState(session_id="test-7")
        state.add_verification_result({"verdict": "CORRECT"})
        state.add_verification_result({"verdict": "PASS"})
        assert state.verification_pass_rate == 1.0

    def test_verification_pass_rate_all_fail(self):
        state = SessionState(session_id="test-8")
        state.add_verification_result({"verdict": "INCORRECT"})
        state.add_verification_result({"verdict": "FAIL"})
        assert state.verification_pass_rate == 0.0

    def test_age_seconds(self):
        state = SessionState(session_id="test-9")
        # Age should be small since just created
        assert state.age_seconds >= 0
        assert state.age_seconds < 5  # Should be nearly instant


# ---------------------------------------------------------------------------
# SessionStateManager tests
# ---------------------------------------------------------------------------


class TestSessionStateManager:
    def test_get_or_create_new(self):
        mgr = SessionStateManager()
        session = mgr.get_or_create("s1")
        assert session.session_id == "s1"
        assert mgr.active_count() == 1

    def test_get_or_create_existing(self):
        mgr = SessionStateManager()
        s1 = mgr.get_or_create("s1")
        s1.task_classification = {"task_type": "CODE_GENERATION"}
        s2 = mgr.get_or_create("s1")
        assert s2 is s1
        assert s2.task_classification == {"task_type": "CODE_GENERATION"}
        assert mgr.active_count() == 1

    def test_get_missing(self):
        mgr = SessionStateManager()
        assert mgr.get("nonexistent") is None

    def test_get_existing(self):
        mgr = SessionStateManager()
        mgr.get_or_create("s1")
        assert mgr.get("s1") is not None
        assert mgr.get("s1").session_id == "s1"

    def test_pending_context_lifecycle(self):
        mgr = SessionStateManager()
        mgr.get_or_create("s1")

        # Initially no pending context
        assert mgr.consume_pending_context("s1") is None

        # Set some context
        mgr.set_pending_context("s1", "Verification failed: missing error handling")

        # Consume it
        ctx = mgr.consume_pending_context("s1")
        assert ctx == "Verification failed: missing error handling"

        # Second consume should return None (already consumed)
        assert mgr.consume_pending_context("s1") is None

    def test_pending_context_unknown_session(self):
        mgr = SessionStateManager()
        # Should not raise, just log a warning
        mgr.set_pending_context("nonexistent", "test")
        assert mgr.consume_pending_context("nonexistent") is None

    def test_cleanup_expired(self):
        mgr = SessionStateManager()
        s1 = mgr.get_or_create("old-session")
        s2 = mgr.get_or_create("new-session")

        # Backdate old-session to 5 hours ago
        s1.last_activity = datetime.now(tz=timezone.utc) - timedelta(hours=5)

        removed = mgr.cleanup_expired(max_age_hours=4)
        assert removed == 1
        assert mgr.active_count() == 1
        assert mgr.get("old-session") is None
        assert mgr.get("new-session") is not None

    def test_cleanup_expired_none_expired(self):
        mgr = SessionStateManager()
        mgr.get_or_create("s1")
        mgr.get_or_create("s2")
        removed = mgr.cleanup_expired(max_age_hours=4)
        assert removed == 0
        assert mgr.active_count() == 2

    def test_cleanup_expired_all_expired(self):
        mgr = SessionStateManager()
        s1 = mgr.get_or_create("s1")
        s2 = mgr.get_or_create("s2")
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=10)
        s1.last_activity = old_time
        s2.last_activity = old_time
        removed = mgr.cleanup_expired(max_age_hours=4)
        assert removed == 2
        assert mgr.active_count() == 0

    def test_get_all_stats(self):
        mgr = SessionStateManager()
        s1 = mgr.get_or_create("s1")
        s1.log_tool_call(tool_name="Write")
        s1.log_tool_call(tool_name="Edit")
        s1.add_verification_result({"verdict": "CORRECT"})

        s2 = mgr.get_or_create("s2")
        s2.log_tool_call(tool_name="Bash")

        stats = mgr.get_all_stats()
        assert stats["active_sessions"] == 2
        assert stats["total_tool_calls"] == 3
        assert stats["total_verifications"] == 1

    def test_multiple_sessions(self):
        mgr = SessionStateManager()
        for i in range(10):
            mgr.get_or_create(f"session-{i}")
        assert mgr.active_count() == 10


# ---------------------------------------------------------------------------
# Thread safety tests
# ---------------------------------------------------------------------------


class TestSessionStateManagerThreadSafety:
    def test_concurrent_get_or_create(self):
        """Multiple threads creating/accessing the same session concurrently."""
        mgr = SessionStateManager()
        results: list[SessionState] = []
        errors: list[Exception] = []

        def worker():
            try:
                session = mgr.get_or_create("shared-session")
                results.append(session)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors: {errors}"
        assert len(results) == 20
        # All threads should get the same session object
        assert all(r is results[0] for r in results)
        assert mgr.active_count() == 1

    def test_concurrent_different_sessions(self):
        """Multiple threads creating different sessions concurrently."""
        mgr = SessionStateManager()
        errors: list[Exception] = []

        def worker(session_id: str):
            try:
                session = mgr.get_or_create(session_id)
                session.log_tool_call(tool_name="Bash")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"session-{i}",))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors: {errors}"
        assert mgr.active_count() == 50

    def test_concurrent_pending_context(self):
        """Concurrent set/consume of pending context."""
        mgr = SessionStateManager()
        mgr.get_or_create("ctx-session")
        consumed: list[str | None] = []
        errors: list[Exception] = []

        def setter():
            try:
                for i in range(100):
                    mgr.set_pending_context("ctx-session", f"context-{i}")
            except Exception as exc:
                errors.append(exc)

        def consumer():
            try:
                for _ in range(100):
                    result = mgr.consume_pending_context("ctx-session")
                    if result is not None:
                        consumed.append(result)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=setter)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"
        # We should have consumed some contexts (exact count depends on timing)
        # The important thing is no crashes or data corruption

    def test_concurrent_cleanup(self):
        """Cleanup running concurrently with get_or_create."""
        mgr = SessionStateManager()
        errors: list[Exception] = []

        def creator():
            try:
                for i in range(50):
                    mgr.get_or_create(f"c-{i}")
            except Exception as exc:
                errors.append(exc)

        def cleaner():
            try:
                for _ in range(20):
                    mgr.cleanup_expired(max_age_hours=0)  # Aggressive cleanup
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=creator)
        t2 = threading.Thread(target=cleaner)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"
