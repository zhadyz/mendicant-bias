"""
mendicant_runtime.subagents
============================
Subagent executor for the Mendicant Bias runtime.

Modeled after DeerFlow's SubagentExecutor — runs named agents in parallel
using a thread pool, with configurable concurrency and timeout.

Usage::

    executor = SubagentExecutor(max_workers=3)
    future_id = executor.submit("Research quantum computing", config, factory)
    # ... poll or await ...
    result = executor.get_result(future_id)
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class SubagentConfig:
    """Configuration for a subagent invocation."""

    name: str
    system_prompt: str = ""
    model: str = "inherit"
    tools: list[str] | None = None
    max_turns: int = 10
    timeout_seconds: float = 900.0  # 15 minutes, matching DeerFlow


@dataclass
class SubagentTask:
    """Tracks a submitted subagent task."""

    task_id: str
    agent_name: str
    prompt: str
    status: SubagentStatus = SubagentStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    future: Future | None = field(default=None, repr=False)


class SubagentExecutor:
    """
    Manages concurrent subagent execution with a bounded thread pool.

    Parameters
    ----------
    max_workers : int
        Maximum number of subagents that can run in parallel.
        Defaults to 3, matching DeerFlow's ``MAX_CONCURRENT_SUBAGENTS``.
    """

    def __init__(self, max_workers: int = 3) -> None:
        self._max_workers = max_workers
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mendicant-subagent",
        )
        self._tasks: dict[str, SubagentTask] = {}
        self._lock = threading.Lock()

        logger.info(
            "[SubagentExecutor] Initialized — max_workers=%d",
            max_workers,
        )

    def submit(
        self,
        prompt: str,
        config: SubagentConfig,
        agent_factory: Callable[[SubagentConfig], Any],
    ) -> str:
        """
        Submit a task for subagent execution.

        Parameters
        ----------
        prompt : str
            The task description / prompt for the subagent.
        config : SubagentConfig
            Subagent configuration (name, model, tools, etc.).
        agent_factory : callable
            Factory function that creates a compiled agent from config.

        Returns
        -------
        str
            Task ID for polling the result.
        """
        task_id = f"subagent_{uuid.uuid4().hex[:10]}"
        task = SubagentTask(
            task_id=task_id,
            agent_name=config.name,
            prompt=prompt,
        )

        with self._lock:
            self._tasks[task_id] = task

        future = self._pool.submit(
            self._run_subagent, task, config, agent_factory
        )
        task.future = future

        logger.info(
            "[SubagentExecutor] Submitted task %s — agent=%s, prompt=%.80s",
            task_id,
            config.name,
            prompt,
        )
        return task_id

    def get_status(self, task_id: str) -> SubagentTask | None:
        """Return the current status of a submitted task."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_result(self, task_id: str) -> Any | None:
        """
        Get the result of a completed task, or ``None`` if not done.

        Does not block.  Use ``wait_for_result`` for blocking retrieval.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == SubagentStatus.COMPLETED:
                return task.result
            return None

    def wait_for_result(self, task_id: str, timeout: float | None = None) -> Any:
        """
        Block until the task completes and return its result.

        Parameters
        ----------
        task_id : str
            Task identifier.
        timeout : float | None
            Maximum seconds to wait.

        Returns
        -------
        Any
            The subagent result.

        Raises
        ------
        TimeoutError
            If the timeout is exceeded.
        RuntimeError
            If the task failed.
        KeyError
            If the task ID is not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task ID: {task_id}")

        if task.future is not None:
            try:
                task.future.result(timeout=timeout)
            except Exception:
                pass  # Error is captured in the task itself

        if task.status == SubagentStatus.COMPLETED:
            return task.result
        elif task.status == SubagentStatus.FAILED:
            raise RuntimeError(f"Subagent task {task_id} failed: {task.error}")
        elif task.status == SubagentStatus.TIMED_OUT:
            raise TimeoutError(f"Subagent task {task_id} timed out")
        else:
            raise TimeoutError(f"Subagent task {task_id} did not complete within timeout")

    def list_active(self) -> list[SubagentTask]:
        """Return all tasks that are pending or running."""
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.status in (SubagentStatus.PENDING, SubagentStatus.RUNNING)
            ]

    def get_stats(self) -> dict[str, Any]:
        """Return executor statistics."""
        with self._lock:
            statuses = {}
            for task in self._tasks.values():
                statuses[task.status.value] = statuses.get(task.status.value, 0) + 1

        return {
            "max_workers": self._max_workers,
            "total_tasks": len(self._tasks),
            "by_status": statuses,
        }

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the thread pool."""
        self._pool.shutdown(wait=wait)
        logger.info("[SubagentExecutor] Shut down (wait=%s)", wait)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_subagent(
        self,
        task: SubagentTask,
        config: SubagentConfig,
        agent_factory: Callable[[SubagentConfig], Any],
    ) -> None:
        """Execute a subagent task in a worker thread."""
        task.status = SubagentStatus.RUNNING
        task.started_at = time.monotonic()

        try:
            # Build the subagent
            agent = agent_factory(config)

            # Prepare input
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = []
            if config.system_prompt:
                messages.append(SystemMessage(content=config.system_prompt))
            messages.append(HumanMessage(content=task.prompt))

            input_state = {"messages": messages}

            # Invoke with timeout awareness
            result = agent.invoke(input_state)

            task.result = result
            task.status = SubagentStatus.COMPLETED
            task.completed_at = time.monotonic()

            elapsed = task.completed_at - task.started_at
            logger.info(
                "[SubagentExecutor] Task %s completed in %.1fs — agent=%s",
                task.task_id,
                elapsed,
                config.name,
            )

        except Exception as exc:
            task.status = SubagentStatus.FAILED
            task.error = str(exc)
            task.completed_at = time.monotonic()

            logger.error(
                "[SubagentExecutor] Task %s failed — agent=%s: %s",
                task.task_id,
                config.name,
                exc,
            )
