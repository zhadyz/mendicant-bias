"""
mendicant_runtime.sandbox
=========================
Local sandbox provider for the Mendicant Bias runtime.

Modeled after DeerFlow's sandbox lifecycle (acquire / get / release) but
simplified for standalone use.  Each thread gets an isolated workspace
directory under the configured base path.

The sandbox is acquired on ``runtime.invoke()`` and held for the duration
of the thread (not released between turns, matching DeerFlow's behavior).
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LocalSandbox:
    """
    A thread-local workspace backed by a filesystem directory.

    Provides the minimal interface needed by the runtime to pass sandbox
    state through ``MendicantThreadState``.

    Parameters
    ----------
    sandbox_id : str
        Unique identifier for this sandbox instance.
    base_path : Path
        Root directory for the sandbox (workspace, uploads, outputs live here).
    """

    def __init__(self, sandbox_id: str, base_path: Path) -> None:
        self.id = sandbox_id
        self._base_path = base_path

        # Create directory structure
        self.workspace_path = base_path / "workspace"
        self.uploads_path = base_path / "uploads"
        self.outputs_path = base_path / "outputs"

        for p in (self.workspace_path, self.uploads_path, self.outputs_path):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base_path

    def to_thread_data(self) -> dict[str, str]:
        """Return a dict suitable for ``MendicantThreadState.thread_data``."""
        return {
            "workspace_path": str(self.workspace_path),
            "uploads_path": str(self.uploads_path),
            "outputs_path": str(self.outputs_path),
        }

    def cleanup(self) -> None:
        """Remove all sandbox files.  Safe to call multiple times."""
        if self._base_path.exists():
            try:
                shutil.rmtree(self._base_path)
                logger.debug("[Sandbox] Cleaned up %s", self._base_path)
            except OSError as exc:
                logger.warning("[Sandbox] Failed to clean up %s: %s", self._base_path, exc)


class LocalSandboxProvider:
    """
    Thread-safe sandbox provider that manages per-thread workspaces.

    Parameters
    ----------
    base_dir : str | Path
        Parent directory under which per-thread sandboxes are created.
        Defaults to ``.mendicant/threads``.
    """

    def __init__(self, base_dir: str | Path = ".mendicant/threads") -> None:
        self._base_dir = Path(base_dir)
        self._sandboxes: dict[str, LocalSandbox] = {}
        self._lock = threading.Lock()

        logger.info("[SandboxProvider] Initialized — base_dir=%s", self._base_dir)

    def acquire(self, thread_id: str) -> LocalSandbox:
        """
        Acquire (or retrieve) a sandbox for the given thread.

        If a sandbox already exists for this thread, it is returned as-is
        (persistent across turns, like DeerFlow).  Otherwise a new one is
        created.

        Parameters
        ----------
        thread_id : str
            Thread identifier.

        Returns
        -------
        LocalSandbox
        """
        with self._lock:
            if thread_id in self._sandboxes:
                logger.debug("[SandboxProvider] Reusing sandbox for thread %s", thread_id)
                return self._sandboxes[thread_id]

            sandbox_id = f"sandbox_{uuid.uuid4().hex[:8]}"
            sandbox_path = self._base_dir / thread_id / "user-data"
            sandbox = LocalSandbox(sandbox_id=sandbox_id, base_path=sandbox_path)
            self._sandboxes[thread_id] = sandbox

            logger.info(
                "[SandboxProvider] Acquired sandbox %s for thread %s",
                sandbox_id,
                thread_id,
            )
            return sandbox

    def get(self, thread_id: str) -> LocalSandbox | None:
        """Return the sandbox for *thread_id*, or ``None`` if not acquired."""
        with self._lock:
            return self._sandboxes.get(thread_id)

    def release(self, thread_id: str, *, cleanup: bool = False) -> bool:
        """
        Release a thread's sandbox.

        Parameters
        ----------
        thread_id : str
            Thread identifier.
        cleanup : bool
            If ``True``, delete the sandbox directory as well.

        Returns
        -------
        bool
            ``True`` if a sandbox was released.
        """
        with self._lock:
            sandbox = self._sandboxes.pop(thread_id, None)

        if sandbox is None:
            return False

        if cleanup:
            sandbox.cleanup()

        logger.info("[SandboxProvider] Released sandbox for thread %s (cleanup=%s)", thread_id, cleanup)
        return True

    def list_active(self) -> list[str]:
        """Return thread IDs with active sandboxes."""
        with self._lock:
            return list(self._sandboxes.keys())

    def cleanup_all(self) -> int:
        """Release and clean up all active sandboxes.  Returns count released."""
        with self._lock:
            count = len(self._sandboxes)
            for sandbox in self._sandboxes.values():
                sandbox.cleanup()
            self._sandboxes.clear()

        logger.info("[SandboxProvider] Cleaned up %d sandboxes", count)
        return count
