"""
Mendicant Bias V5 -- Subagent Execution System
===============================================

Manages parallel specialist agent execution with managed thread pools.
Adapted from DeerFlow's dual-pool executor pattern and reimplemented
for Mendicant's middleware-rich architecture.

Components:
    - **SubagentStatus** -- Lifecycle enum (PENDING -> RUNNING -> terminal).
    - **SubagentResult** -- Dataclass carrying task output, timing, and messages.
    - **SubagentConfig** -- Per-agent configuration (tools, model, timeout).
    - **SubagentExecutor** -- Thread-pool manager that accepts tasks via
      :meth:`submit` / :meth:`submit_parallel` and exposes
      :meth:`wait` / :meth:`wait_all` for synchronous collection.

Key design choices:
    - *agent_factory* is an optional callable so that tests (and
      non-LangGraph callers) can inject lightweight stubs.
    - Timeout enforcement is per-task, checked in ``wait()``'s polling
      loop, not via ``Future.result(timeout=...)`` -- this avoids
      hard-killing threads that may hold sandbox resources.
    - Results are stored in an in-memory dict keyed by ``task_id``.
      Call :meth:`cleanup` to remove terminal results.
    - Trace IDs propagate from parent to child so that distributed
      logging can correlate a lead-agent turn with its subagent work.

Usage::

    executor = SubagentExecutor(max_workers=3)

    tid = executor.submit(
        "Summarize the uploaded PDF",
        SubagentConfig(name="summarizer"),
    )
    result = executor.wait(tid, timeout=120)
    print(result.status, result.result)

    executor.shutdown()

Parallel::

    tids = executor.submit_parallel([
        ("Research X", SubagentConfig(name="researcher")),
        ("Write tests for Y", SubagentConfig(name="test-writer")),
    ])
    results = executor.wait_all(tids, timeout=300)
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class SubagentStatus(Enum):
    """Lifecycle status of a subagent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in (
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.TIMED_OUT,
        )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SubagentResult:
    """Result of a single subagent execution.

    Attributes:
        task_id:  Unique identifier for this execution.
        trace_id: Distributed-tracing ID (links parent and subagent logs).
        agent_name: Name of the subagent config that produced this result.
        status:   Current lifecycle status.
        result:   Final text result (if completed).
        error:    Error message (if failed / timed out).
        started_at:   UTC datetime when execution started.
        completed_at: UTC datetime when execution finished.
        messages: Raw message dicts captured during execution (for replay).
        duration_seconds: Wall-clock seconds from start to completion.
    """

    task_id: str
    trace_id: str
    agent_name: str
    status: SubagentStatus = SubagentStatus.PENDING
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class SubagentConfig:
    """Configuration for a specialist subagent.

    Attributes:
        name: Human-readable agent name (used in logs and result).
        system_prompt: Optional system prompt override.
        tools: Optional allowlist of tool names.  ``None`` = all tools.
        disallowed_tools: Optional denylist (applied after allowlist).
        model: Model name, or ``"inherit"`` to use the parent's model.
        max_turns: Maximum LangGraph recursion limit.
        timeout_seconds: Per-task hard timeout.
    """

    name: str
    system_prompt: str = ""
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 10
    timeout_seconds: float = 300.0  # 5 min default


# ---------------------------------------------------------------------------
# Type alias for the factory callable
# ---------------------------------------------------------------------------

AgentFactory = Callable[[SubagentConfig], Any]
"""Callable that receives a SubagentConfig and returns an invocable agent.

The returned object must support ``agent.invoke({"messages": [...]})``
and return a dict with a ``"messages"`` key (standard LangGraph protocol).
"""


# ---------------------------------------------------------------------------
# SubagentExecutor
# ---------------------------------------------------------------------------


class SubagentExecutor:
    """Execute subagents in a managed thread pool.

    Parameters:
        max_workers:     Maximum concurrent subagent threads.
        default_timeout: Fallback timeout used by :meth:`wait` when no
                         explicit value is passed.
    """

    def __init__(
        self,
        max_workers: int = 3,
        default_timeout: float = 300.0,
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mendicant-subagent",
        )
        self._default_timeout = default_timeout
        self._tasks: dict[str, SubagentResult] = {}
        self._lock = threading.Lock()

    # -- Submission --------------------------------------------------------

    def submit(
        self,
        task: str,
        config: SubagentConfig,
        agent_factory: AgentFactory | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Submit a subagent task.  Returns *task_id* immediately.

        If *agent_factory* is ``None`` the task text is stored verbatim
        as the result (useful for testing or deferred execution).
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        trace_id = trace_id or f"trace_{uuid.uuid4().hex[:8]}"

        result = SubagentResult(
            task_id=task_id,
            trace_id=trace_id,
            agent_name=config.name,
        )

        with self._lock:
            self._tasks[task_id] = result

        logger.info(
            "[trace=%s] Submitted subagent task %s (%s)",
            trace_id,
            task_id,
            config.name,
        )
        self._pool.submit(self._execute, task, config, result, agent_factory)
        return task_id

    def submit_parallel(
        self,
        tasks: list[tuple[str, SubagentConfig]],
        agent_factory: AgentFactory | None = None,
        trace_id: str | None = None,
    ) -> list[str]:
        """Submit multiple subagent tasks sharing the same *trace_id*.

        Returns a list of task IDs in the same order as *tasks*.
        """
        trace_id = trace_id or f"trace_{uuid.uuid4().hex[:8]}"
        task_ids: list[str] = []
        for task_text, config in tasks:
            tid = self.submit(task_text, config, agent_factory, trace_id)
            task_ids.append(tid)
        return task_ids

    # -- Execution (runs inside thread pool) --------------------------------

    def _execute(
        self,
        task: str,
        config: SubagentConfig,
        result: SubagentResult,
        agent_factory: AgentFactory | None,
    ) -> None:
        """Execute a subagent task.  Updates *result* in place."""
        result.status = SubagentStatus.RUNNING
        result.started_at = datetime.now(tz=timezone.utc)
        start_mono = time.monotonic()

        try:
            if agent_factory is not None:
                agent = agent_factory(config)
                # Standard LangGraph invoke protocol.
                try:
                    from langchain_core.messages import HumanMessage

                    input_messages = [HumanMessage(content=task)]
                except ImportError:
                    # Fallback if langchain_core is not installed.
                    input_messages = [{"role": "user", "content": task}]

                agent_output = agent.invoke({"messages": input_messages})

                # Extract last AI message content from the result.
                messages = agent_output.get("messages", [])
                for msg in reversed(messages):
                    content = getattr(msg, "content", None)
                    msg_type = getattr(msg, "type", None)
                    if content and msg_type == "ai":
                        result.result = (
                            content if isinstance(content, str) else str(content)
                        )
                        break
                else:
                    # No AI message found -- use raw output.
                    if messages:
                        last = messages[-1]
                        raw = getattr(last, "content", str(last))
                        result.result = raw if isinstance(raw, str) else str(raw)
                    else:
                        result.result = "No response generated"

                # Capture serialisable message dicts.
                for msg in messages:
                    if hasattr(msg, "model_dump"):
                        result.messages.append(msg.model_dump())
                    elif isinstance(msg, dict):
                        result.messages.append(msg)
            else:
                # No factory -- store task for deferred / manual execution.
                result.result = (
                    f"[Subagent {config.name}] Task queued: {task[:200]}"
                )

            result.status = SubagentStatus.COMPLETED
            logger.info(
                "[trace=%s] Subagent %s completed (task %s)",
                result.trace_id,
                config.name,
                result.task_id,
            )

        except Exception as e:
            result.status = SubagentStatus.FAILED
            result.error = str(e)
            logger.error(
                "[trace=%s] Subagent %s failed (task %s): %s",
                result.trace_id,
                config.name,
                result.task_id,
                e,
            )
        finally:
            result.completed_at = datetime.now(tz=timezone.utc)
            result.duration_seconds = time.monotonic() - start_mono

    # -- Result retrieval --------------------------------------------------

    def get_result(self, task_id: str) -> SubagentResult | None:
        """Return the current result for *task_id*, or ``None``."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_all_results(self) -> dict[str, SubagentResult]:
        """Snapshot of all tracked results (terminal and in-flight)."""
        with self._lock:
            return dict(self._tasks)

    # -- Blocking waiters --------------------------------------------------

    def wait(
        self,
        task_id: str,
        timeout: float | None = None,
        poll_interval: float = 0.1,
    ) -> SubagentResult | None:
        """Block until *task_id* reaches a terminal status.

        Returns the result, or marks it ``TIMED_OUT`` if the deadline
        expires.  Returns ``None`` only if the task_id is unknown.
        """
        deadline = time.monotonic() + (timeout or self._default_timeout)
        while time.monotonic() < deadline:
            result = self.get_result(task_id)
            if result is None:
                return None
            if result.status.is_terminal:
                return result
            time.sleep(poll_interval)

        # Timeout reached.
        result = self.get_result(task_id)
        if result and not result.status.is_terminal:
            result.status = SubagentStatus.TIMED_OUT
            result.error = f"Timed out after {timeout or self._default_timeout:.1f}s"
            result.completed_at = datetime.now(tz=timezone.utc)
            logger.warning(
                "[trace=%s] Subagent %s timed out (task %s)",
                result.trace_id,
                result.agent_name,
                result.task_id,
            )
        return result

    def wait_all(
        self,
        task_ids: list[str],
        timeout: float | None = None,
    ) -> list[SubagentResult | None]:
        """Wait for every task in *task_ids* to complete.

        The total wall-clock time is bounded by *timeout*; individual
        tasks get whatever time remains.
        """
        deadline = time.monotonic() + (timeout or self._default_timeout)
        results: list[SubagentResult | None] = []
        for tid in task_ids:
            remaining = max(0.1, deadline - time.monotonic())
            result = self.wait(tid, timeout=remaining)
            results.append(result)
        return results

    # -- Cleanup -----------------------------------------------------------

    def cleanup(self, task_id: str) -> bool:
        """Remove a terminal result.  Returns ``True`` if removed."""
        with self._lock:
            result = self._tasks.get(task_id)
            if result is None:
                return False
            if result.status.is_terminal:
                del self._tasks[task_id]
                return True
            return False

    def cleanup_all_terminal(self) -> int:
        """Remove all terminal results.  Returns count removed."""
        with self._lock:
            to_remove = [
                tid
                for tid, r in self._tasks.items()
                if r.status.is_terminal
            ]
            for tid in to_remove:
                del self._tasks[tid]
            return len(to_remove)

    # -- Lifecycle ---------------------------------------------------------

    def pending_count(self) -> int:
        """Number of non-terminal tasks."""
        with self._lock:
            return sum(
                1 for r in self._tasks.values() if not r.status.is_terminal
            )

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the thread pool.

        By default does *not* wait for running tasks, since they may be
        blocked on LLM calls.
        """
        self._pool.shutdown(wait=wait)
        logger.info("SubagentExecutor shut down (wait=%s)", wait)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SubagentStatus",
    "SubagentResult",
    "SubagentConfig",
    "SubagentExecutor",
    "AgentFactory",
]
