"""
Mendicant Bias V5 -- Sandbox Execution System
==============================================

Adapted from DeerFlow's sandbox architecture. Provides isolated,
thread-local execution environments for agent tool calls.

Components:
    - **Sandbox** (ABC) -- Abstract interface for command execution and file I/O.
      Swap LocalSandbox for Docker, Kubernetes, or cloud backends by implementing
      this interface.
    - **LocalSandbox** -- Filesystem sandbox with virtual path mapping.
      Maps ``/mnt/user-data/`` to per-thread directories so the agent never sees
      host paths.
    - **SandboxProvider** (ABC) -- Lifecycle manager (acquire/release).
    - **LocalSandboxProvider** -- One sandbox per thread, auto-creates workspace
      directories (workspace, uploads, outputs).
    - **SandboxState / ThreadDataState** -- Lightweight state dataclasses that
      live inside ``MendicantThreadState``.
    - **MendicantThreadState** -- Extended ``AgentState`` carrying sandbox, thread
      data, middleware results, and task metadata through the LangGraph graph.

Security:
    - Path traversal rejection (``..`` segments)
    - Virtual-to-physical path translation (agent never sees host paths)
    - Output masking (physical paths replaced with virtual paths in stdout)
    - Configurable command timeout (default 60 s)

Usage::

    provider = LocalSandboxProvider(base_dir=".mendicant/threads")
    sandbox = provider.acquire(thread_id="abc123")

    sandbox.write_file("/mnt/user-data/workspace/hello.py", "print('hi')")
    output = sandbox.execute_command("python /mnt/user-data/workspace/hello.py")
    print(output)  # "hi"

    provider.release(sandbox.id)
"""

from __future__ import annotations

import logging
import subprocess
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State dataclasses (stored inside MendicantThreadState)
# ---------------------------------------------------------------------------


class SandboxState(TypedDict):
    """Sandbox state carried through the LangGraph graph."""

    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    """Thread workspace paths carried through the LangGraph graph."""

    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


# ---------------------------------------------------------------------------
# Artifact / image merge reducers (for Annotated fields in ThreadState)
# ---------------------------------------------------------------------------


def merge_artifacts(
    existing: list[str] | None, new: list[str] | None
) -> list[str]:
    """Reducer for artifacts list -- deduplicate preserving order."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


# ---------------------------------------------------------------------------
# MendicantThreadState -- the graph state schema
# ---------------------------------------------------------------------------

# Import AgentState at module level so the class body can reference it.
# If langchain is unavailable at import time we fall back to a plain
# TypedDict so that the sandbox and provider code can still be used
# without a full LangGraph installation.
try:
    from langchain.agents import AgentState as _AgentStateBase
except ImportError:  # pragma: no cover
    # Minimal fallback so the rest of the module still works.
    class _AgentStateBase(TypedDict):  # type: ignore[no-redef]
        messages: list[Any]


class MendicantThreadState(_AgentStateBase):
    """Thread state for Mendicant agent execution.

    Extends ``AgentState`` with sandbox, thread data, middleware outputs,
    and task-level metadata so every node in the LangGraph graph can read
    and write shared execution context.
    """

    # -- Sandbox / thread data --
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]

    # -- Conversation metadata --
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]

    # -- Middleware outputs --
    task_type: NotRequired[str | None]
    verification_enabled: NotRequired[bool | None]
    subagent_enabled: NotRequired[bool | None]
    thinking_enabled: NotRequired[bool | None]

    # -- Semantic tool router --
    selected_tools: NotRequired[list[str] | None]
    tool_scores: NotRequired[dict[str, float] | None]

    # -- Verification gate --
    verification_result: NotRequired[dict[str, Any] | None]
    verification_verdict: NotRequired[str | None]

    # -- Context budget --
    context_budget_usage: NotRequired[dict[str, Any] | None]

    # -- Adaptive learning --
    learning_metadata: NotRequired[dict[str, Any] | None]

    # -- Timing --
    task_start_time: NotRequired[float | None]


# ---------------------------------------------------------------------------
# Abstract Sandbox interface
# ---------------------------------------------------------------------------


class Sandbox(ABC):
    """Abstract sandbox for isolated code execution.

    Implementations may run commands locally, inside Docker containers,
    or on remote Kubernetes pods.  The agent code interacts exclusively
    through virtual paths (``/mnt/user-data/...``); the sandbox
    translates them to physical locations.
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        ...

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """Execute a shell command and return combined stdout + stderr."""
        ...

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file's text content."""
        ...

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write *content* to *path*, optionally appending."""
        ...

    @abstractmethod
    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """List directory contents up to *max_depth* levels."""
        ...


# ---------------------------------------------------------------------------
# LocalSandbox -- filesystem-backed implementation
# ---------------------------------------------------------------------------


class LocalSandbox(Sandbox):
    """Local filesystem sandbox with virtual path mapping.

    Virtual paths like ``/mnt/user-data/workspace/foo.py`` are
    translated to physical paths under *base_path* via *path_mappings*.
    Command output is post-processed so that physical paths are
    replaced with their virtual equivalents (``_mask_paths``).
    """

    def __init__(
        self,
        sandbox_id: str,
        base_path: Path,
        path_mappings: dict[str, str] | None = None,
        command_timeout: int = 60,
    ) -> None:
        self._id = sandbox_id
        self._base_path = Path(base_path)
        self._path_mappings = path_mappings or {}
        self._command_timeout = command_timeout
        self._base_path.mkdir(parents=True, exist_ok=True)

    # -- Property ----------------------------------------------------------

    @property
    def id(self) -> str:
        return self._id

    @property
    def base_path(self) -> Path:
        """Physical base path (for testing / inspection)."""
        return self._base_path

    # -- Path translation --------------------------------------------------

    def _resolve_path(self, virtual_path: str) -> Path:
        """Translate a virtual path to an actual filesystem path.

        Longest-prefix-first matching mirrors DeerFlow's strategy so that
        ``/mnt/user-data/workspace`` is matched before ``/mnt/user-data``.
        """
        sorted_mappings = sorted(
            self._path_mappings.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        for virtual_prefix, actual_prefix in sorted_mappings:
            if virtual_path == virtual_prefix or virtual_path.startswith(
                virtual_prefix + "/"
            ):
                relative = virtual_path[len(virtual_prefix) :].lstrip("/")
                return Path(actual_prefix) / relative

        # Fallback: strip the standard virtual prefix and resolve under base.
        clean = virtual_path.lstrip("/")
        if clean.startswith("mnt/user-data/"):
            clean = clean[len("mnt/user-data/") :]
        return self._base_path / clean

    def _validate_path(self, path: str, *, read_only: bool = False) -> Path:
        """Validate *path* is safe (no traversal, within sandbox).

        Raises ``PermissionError`` on path traversal attempts.
        """
        if ".." in path.split("/"):
            raise PermissionError(f"Path traversal detected: {path}")
        resolved = self._resolve_path(path)
        # Ensure the resolved path doesn't escape via symlinks.
        try:
            resolved.resolve().relative_to(self._base_path.resolve().parent)
        except ValueError:
            raise PermissionError(
                f"Access denied: resolved path escapes sandbox: {path}"
            )
        return resolved

    def _mask_paths(self, output: str) -> str:
        """Replace physical paths in *output* with virtual equivalents.

        Longest-prefix-first replacement avoids partial matches.
        """
        result = output
        sorted_mappings = sorted(
            self._path_mappings.items(),
            key=lambda kv: len(kv[1]),
            reverse=True,
        )
        for virtual_prefix, actual_prefix in sorted_mappings:
            result = result.replace(actual_prefix, virtual_prefix)
        # Catch any remaining base-path leaks.
        result = result.replace(str(self._base_path), "/mnt/user-data")
        return result

    # -- Command execution -------------------------------------------------

    def execute_command(self, command: str) -> str:
        """Execute *command* with virtual-to-physical path translation.

        The command string is scanned for virtual path prefixes and each
        is replaced with its physical counterpart before execution.
        Output is then masked back to virtual paths.
        """
        translated = command
        sorted_mappings = sorted(
            self._path_mappings.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        for virtual_prefix, actual_prefix in sorted_mappings:
            translated = translated.replace(virtual_prefix, actual_prefix)

        try:
            result = subprocess.run(
                translated,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._command_timeout,
                cwd=str(self._base_path),
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return self._mask_paths(output.strip())
        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out ({self._command_timeout}s limit)"
        except Exception as e:
            return f"ERROR: {self._mask_paths(str(e))}"

    # -- File operations ---------------------------------------------------

    def read_file(self, path: str) -> str:
        resolved = self._validate_path(path, read_only=True)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if resolved.is_dir():
            raise IsADirectoryError(f"Path is a directory, not a file: {path}")
        return resolved.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        resolved = self._validate_path(path, read_only=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with resolved.open(mode, encoding="utf-8") as f:
            f.write(content)

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        resolved = self._validate_path(path, read_only=True)
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        entries: list[str] = []
        self._walk(resolved, entries, depth=0, max_depth=max_depth, prefix="")
        return entries

    def _walk(
        self,
        dir_path: Path,
        entries: list[str],
        depth: int,
        max_depth: int,
        prefix: str,
    ) -> None:
        if depth >= max_depth:
            return
        try:
            items = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name),
            )
            for item in items:
                rel = f"{prefix}{item.name}{'/' if item.is_dir() else ''}"
                entries.append(rel)
                if item.is_dir():
                    self._walk(
                        item,
                        entries,
                        depth + 1,
                        max_depth,
                        f"{prefix}{item.name}/",
                    )
        except PermissionError:
            pass


# ---------------------------------------------------------------------------
# Abstract SandboxProvider
# ---------------------------------------------------------------------------


class SandboxProvider(ABC):
    """Manages sandbox lifecycle (acquire / get / release)."""

    @abstractmethod
    def acquire(self, thread_id: str) -> Sandbox:
        """Acquire (or reuse) a sandbox for *thread_id*."""
        ...

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """Retrieve an existing sandbox by its *sandbox_id*."""
        ...

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """Release a sandbox, freeing any held resources."""
        ...


# ---------------------------------------------------------------------------
# LocalSandboxProvider -- one sandbox per thread
# ---------------------------------------------------------------------------


class LocalSandboxProvider(SandboxProvider):
    """Provides local filesystem sandboxes, one per thread.

    Each call to :meth:`acquire` creates (or reuses) a directory tree::

        {base_dir}/{thread_id}/user-data/workspace/
        {base_dir}/{thread_id}/user-data/uploads/
        {base_dir}/{thread_id}/user-data/outputs/

    The sandbox's virtual path mappings are configured so that
    ``/mnt/user-data/workspace`` resolves to the physical workspace
    directory, and so on.
    """

    def __init__(self, base_dir: str = ".mendicant/threads") -> None:
        self._base_dir = Path(base_dir)
        self._sandboxes: dict[str, LocalSandbox] = {}
        self._lock = threading.Lock()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def acquire(self, thread_id: str) -> LocalSandbox:
        with self._lock:
            if thread_id in self._sandboxes:
                return self._sandboxes[thread_id]

            thread_dir = self._base_dir / thread_id / "user-data"
            workspace = thread_dir / "workspace"
            uploads = thread_dir / "uploads"
            outputs = thread_dir / "outputs"

            for d in (workspace, uploads, outputs):
                d.mkdir(parents=True, exist_ok=True)

            sandbox = LocalSandbox(
                sandbox_id=f"local_{thread_id}",
                base_path=workspace,
                path_mappings={
                    "/mnt/user-data/workspace": str(workspace),
                    "/mnt/user-data/uploads": str(uploads),
                    "/mnt/user-data/outputs": str(outputs),
                    "/mnt/user-data": str(thread_dir),
                },
            )
            self._sandboxes[thread_id] = sandbox
            logger.info(
                "Acquired local sandbox %s for thread %s", sandbox.id, thread_id
            )
            return sandbox

    def get(self, sandbox_id: str) -> LocalSandbox | None:
        with self._lock:
            for s in self._sandboxes.values():
                if s.id == sandbox_id:
                    return s
            return None

    def release(self, sandbox_id: str) -> None:
        with self._lock:
            to_remove: str | None = None
            for tid, s in self._sandboxes.items():
                if s.id == sandbox_id:
                    to_remove = tid
                    break
            if to_remove:
                del self._sandboxes[to_remove]
                logger.info("Released sandbox %s", sandbox_id)

    def active_count(self) -> int:
        """Number of currently acquired sandboxes."""
        with self._lock:
            return len(self._sandboxes)

    def thread_ids(self) -> list[str]:
        """List of thread IDs with active sandboxes."""
        with self._lock:
            return list(self._sandboxes.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # State
    "SandboxState",
    "ThreadDataState",
    "MendicantThreadState",
    "merge_artifacts",
    # Sandbox
    "Sandbox",
    "LocalSandbox",
    # Provider
    "SandboxProvider",
    "LocalSandboxProvider",
]
