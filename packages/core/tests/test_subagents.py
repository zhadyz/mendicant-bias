"""
Tests for mendicant_core.subagents -- Subagent execution system.

Covers:
- SubagentStatus lifecycle and is_terminal property
- SubagentResult creation and defaults
- SubagentConfig defaults
- SubagentExecutor submit, get_result, wait
- Parallel submission and wait_all
- Agent factory integration (mock agent)
- Timeout handling
- Cleanup (single and bulk)
- Thread safety under concurrent submission
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from mendicant_core.subagents import (
    SubagentConfig,
    SubagentExecutor,
    SubagentResult,
    SubagentStatus,
)


# ---------------------------------------------------------------------------
# SubagentStatus
# ---------------------------------------------------------------------------


class TestSubagentStatus:
    def test_values(self):
        assert SubagentStatus.PENDING.value == "pending"
        assert SubagentStatus.RUNNING.value == "running"
        assert SubagentStatus.COMPLETED.value == "completed"
        assert SubagentStatus.FAILED.value == "failed"
        assert SubagentStatus.TIMED_OUT.value == "timed_out"

    def test_is_terminal(self):
        assert not SubagentStatus.PENDING.is_terminal
        assert not SubagentStatus.RUNNING.is_terminal
        assert SubagentStatus.COMPLETED.is_terminal
        assert SubagentStatus.FAILED.is_terminal
        assert SubagentStatus.TIMED_OUT.is_terminal


# ---------------------------------------------------------------------------
# SubagentResult
# ---------------------------------------------------------------------------


class TestSubagentResult:
    def test_defaults(self):
        r = SubagentResult(task_id="t1", trace_id="tr1", agent_name="test")
        assert r.status == SubagentStatus.PENDING
        assert r.result is None
        assert r.error is None
        assert r.started_at is None
        assert r.completed_at is None
        assert r.messages == []
        assert r.duration_seconds == 0.0

    def test_fields(self):
        r = SubagentResult(
            task_id="t2",
            trace_id="tr2",
            agent_name="writer",
            status=SubagentStatus.COMPLETED,
            result="Done",
            duration_seconds=1.5,
        )
        assert r.agent_name == "writer"
        assert r.result == "Done"
        assert r.duration_seconds == 1.5


# ---------------------------------------------------------------------------
# SubagentConfig
# ---------------------------------------------------------------------------


class TestSubagentConfig:
    def test_defaults(self):
        cfg = SubagentConfig(name="helper")
        assert cfg.name == "helper"
        assert cfg.system_prompt == ""
        assert cfg.tools is None
        assert cfg.disallowed_tools is None
        assert cfg.model == "inherit"
        assert cfg.max_turns == 10
        assert cfg.timeout_seconds == 300.0

    def test_custom(self):
        cfg = SubagentConfig(
            name="coder",
            tools=["bash", "write_file"],
            disallowed_tools=["task"],
            model="claude-sonnet-4-20250514",
            max_turns=5,
            timeout_seconds=120.0,
        )
        assert cfg.tools == ["bash", "write_file"]
        assert cfg.disallowed_tools == ["task"]
        assert cfg.model == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Mock agent factory
# ---------------------------------------------------------------------------


class _MockMessage:
    """Minimal message-like object for testing."""

    def __init__(self, content: str, msg_type: str = "ai"):
        self.content = content
        self.type = msg_type

    def model_dump(self):
        return {"type": self.type, "content": self.content}


class _MockAgent:
    """Minimal agent stub that returns a fixed response."""

    def __init__(self, response: str = "mock result", delay: float = 0.0):
        self._response = response
        self._delay = delay

    def invoke(self, state):
        if self._delay:
            time.sleep(self._delay)
        return {
            "messages": [
                _MockMessage("echo input", "human"),
                _MockMessage(self._response, "ai"),
            ],
        }


def _mock_factory(response: str = "mock result", delay: float = 0.0):
    """Return an agent_factory callable for testing."""

    def factory(config: SubagentConfig):
        return _MockAgent(response=response, delay=delay)

    return factory


class _FailingAgent:
    """Agent that always raises."""

    def invoke(self, state):
        raise RuntimeError("Agent blew up")


def _failing_factory(config: SubagentConfig):
    return _FailingAgent()


# ---------------------------------------------------------------------------
# SubagentExecutor: basic submit + wait
# ---------------------------------------------------------------------------


class TestSubagentExecutorBasic:
    def test_submit_returns_task_id(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("Do something", SubagentConfig(name="worker"))
            assert tid.startswith("task_")
            assert len(tid) > 5
        finally:
            executor.shutdown()

    def test_submit_without_factory(self):
        """Without a factory, result stores the task text."""
        executor = SubagentExecutor()
        try:
            tid = executor.submit("Summarize X", SubagentConfig(name="summarizer"))
            result = executor.wait(tid, timeout=5)
            assert result is not None
            assert result.status == SubagentStatus.COMPLETED
            assert "Task queued" in result.result
            assert "Summarize X" in result.result
            assert result.agent_name == "summarizer"
        finally:
            executor.shutdown()

    def test_submit_with_factory(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit(
                "Write tests",
                SubagentConfig(name="tester"),
                agent_factory=_mock_factory("tests written"),
            )
            result = executor.wait(tid, timeout=5)
            assert result is not None
            assert result.status == SubagentStatus.COMPLETED
            assert result.result == "tests written"
            assert len(result.messages) == 2  # human + ai
        finally:
            executor.shutdown()

    def test_submit_with_failing_factory(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit(
                "Do something",
                SubagentConfig(name="breaker"),
                agent_factory=_failing_factory,
            )
            result = executor.wait(tid, timeout=5)
            assert result is not None
            assert result.status == SubagentStatus.FAILED
            assert "blew up" in result.error
        finally:
            executor.shutdown()


# ---------------------------------------------------------------------------
# SubagentExecutor: get_result
# ---------------------------------------------------------------------------


class TestSubagentExecutorGetResult:
    def test_get_result_exists(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("task", SubagentConfig(name="w"))
            # May be PENDING or already COMPLETED -- just check it exists.
            result = executor.get_result(tid)
            assert result is not None
            assert result.task_id == tid
        finally:
            executor.shutdown()

    def test_get_result_unknown(self):
        executor = SubagentExecutor()
        try:
            assert executor.get_result("nonexistent") is None
        finally:
            executor.shutdown()

    def test_get_all_results(self):
        executor = SubagentExecutor()
        try:
            t1 = executor.submit("a", SubagentConfig(name="w1"))
            t2 = executor.submit("b", SubagentConfig(name="w2"))
            all_results = executor.get_all_results()
            assert t1 in all_results
            assert t2 in all_results
        finally:
            executor.shutdown()


# ---------------------------------------------------------------------------
# SubagentExecutor: wait + timeout
# ---------------------------------------------------------------------------


class TestSubagentExecutorWait:
    def test_wait_returns_on_completion(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("fast", SubagentConfig(name="w"))
            result = executor.wait(tid, timeout=5)
            assert result is not None
            assert result.status.is_terminal
        finally:
            executor.shutdown()

    def test_wait_unknown_task(self):
        executor = SubagentExecutor()
        try:
            result = executor.wait("bogus", timeout=0.5)
            assert result is None
        finally:
            executor.shutdown()

    def test_wait_timeout_marks_timed_out(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit(
                "slow",
                SubagentConfig(name="sleeper"),
                agent_factory=_mock_factory(delay=10.0),
            )
            result = executor.wait(tid, timeout=0.5)
            assert result is not None
            assert result.status == SubagentStatus.TIMED_OUT
            assert "Timed out" in (result.error or "")
        finally:
            executor.shutdown()

    def test_result_has_timing(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("task", SubagentConfig(name="w"))
            result = executor.wait(tid, timeout=5)
            assert result is not None
            assert result.started_at is not None
            assert result.completed_at is not None
            assert result.duration_seconds >= 0
        finally:
            executor.shutdown()


# ---------------------------------------------------------------------------
# SubagentExecutor: parallel
# ---------------------------------------------------------------------------


class TestSubagentExecutorParallel:
    def test_submit_parallel(self):
        executor = SubagentExecutor(max_workers=3)
        try:
            tasks = [
                ("Task A", SubagentConfig(name="a")),
                ("Task B", SubagentConfig(name="b")),
                ("Task C", SubagentConfig(name="c")),
            ]
            tids = executor.submit_parallel(tasks)
            assert len(tids) == 3
            assert all(t.startswith("task_") for t in tids)
        finally:
            executor.shutdown()

    def test_submit_parallel_shared_trace(self):
        executor = SubagentExecutor()
        try:
            tasks = [
                ("X", SubagentConfig(name="x")),
                ("Y", SubagentConfig(name="y")),
            ]
            tids = executor.submit_parallel(tasks, trace_id="shared_trace")
            results = executor.wait_all(tids, timeout=5)
            for r in results:
                assert r is not None
                assert r.trace_id == "shared_trace"
        finally:
            executor.shutdown()

    def test_wait_all(self):
        executor = SubagentExecutor(max_workers=3)
        try:
            tasks = [
                ("A", SubagentConfig(name="a")),
                ("B", SubagentConfig(name="b")),
            ]
            tids = executor.submit_parallel(tasks)
            results = executor.wait_all(tids, timeout=10)
            assert len(results) == 2
            assert all(r is not None and r.status.is_terminal for r in results)
        finally:
            executor.shutdown()

    def test_wait_all_with_factory(self):
        executor = SubagentExecutor(max_workers=3)
        try:
            factory = _mock_factory("done")
            tasks = [
                ("A", SubagentConfig(name="a")),
                ("B", SubagentConfig(name="b")),
                ("C", SubagentConfig(name="c")),
            ]
            tids = executor.submit_parallel(tasks, agent_factory=factory)
            results = executor.wait_all(tids, timeout=10)
            assert all(r.result == "done" for r in results)
        finally:
            executor.shutdown()


# ---------------------------------------------------------------------------
# SubagentExecutor: cleanup
# ---------------------------------------------------------------------------


class TestSubagentExecutorCleanup:
    def test_cleanup_terminal(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("x", SubagentConfig(name="w"))
            executor.wait(tid, timeout=5)
            assert executor.cleanup(tid) is True
            assert executor.get_result(tid) is None
        finally:
            executor.shutdown()

    def test_cleanup_unknown(self):
        executor = SubagentExecutor()
        try:
            assert executor.cleanup("nope") is False
        finally:
            executor.shutdown()

    def test_cleanup_non_terminal(self):
        """Should not remove a still-running task."""
        executor = SubagentExecutor()
        try:
            tid = executor.submit(
                "slow",
                SubagentConfig(name="sleeper"),
                agent_factory=_mock_factory(delay=10.0),
            )
            # Give it a moment to start running.
            time.sleep(0.2)
            assert executor.cleanup(tid) is False
        finally:
            executor.shutdown()

    def test_cleanup_all_terminal(self):
        executor = SubagentExecutor()
        try:
            t1 = executor.submit("a", SubagentConfig(name="w1"))
            t2 = executor.submit("b", SubagentConfig(name="w2"))
            executor.wait_all([t1, t2], timeout=5)
            removed = executor.cleanup_all_terminal()
            assert removed == 2
            assert executor.get_all_results() == {}
        finally:
            executor.shutdown()

    def test_pending_count(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit(
                "slow",
                SubagentConfig(name="s"),
                agent_factory=_mock_factory(delay=5.0),
            )
            time.sleep(0.2)
            assert executor.pending_count() >= 1
        finally:
            executor.shutdown()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestSubagentExecutorThreadSafety:
    def test_concurrent_submit(self):
        executor = SubagentExecutor(max_workers=5)
        errors: list[Exception] = []
        task_ids: list[str] = []
        lock = threading.Lock()

        def worker(i: int):
            try:
                tid = executor.submit(f"Task {i}", SubagentConfig(name=f"w{i}"))
                with lock:
                    task_ids.append(tid)
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(task_ids) == 20
            # All unique
            assert len(set(task_ids)) == 20
        finally:
            executor.shutdown()

    def test_concurrent_get_result(self):
        executor = SubagentExecutor()
        try:
            tid = executor.submit("x", SubagentConfig(name="w"))
            executor.wait(tid, timeout=5)

            errors: list[Exception] = []
            results: list[SubagentResult | None] = []
            lock = threading.Lock()

            def reader():
                try:
                    r = executor.get_result(tid)
                    with lock:
                        results.append(r)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=reader) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(results) == 20
            assert all(r is not None for r in results)
        finally:
            executor.shutdown()
