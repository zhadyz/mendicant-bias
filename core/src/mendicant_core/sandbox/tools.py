"""
Sandbox tools for Mendicant Bias agents.

Wraps ``LocalSandbox`` operations as LangChain tools that can be bound to a
LangGraph agent.  Each tool delegates to the sandbox instance, preserving
virtual path translation and output masking.

Usage::

    from mendicant_core.sandbox.tools import create_sandbox_tools

    sandbox = provider.acquire(thread_id)
    tools = create_sandbox_tools(sandbox)
    # tools is [bash, read_file, write_file, list_dir]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from mendicant_core.sandbox import Sandbox

logger = logging.getLogger(__name__)


def create_sandbox_tools(sandbox: "Sandbox") -> list:
    """
    Create LangChain tools bound to a specific sandbox instance.

    Parameters
    ----------
    sandbox : Sandbox
        A ``LocalSandbox`` (or any ``Sandbox`` subclass) that provides
        ``execute_command``, ``read_file``, ``write_file``, and ``list_dir``.

    Returns
    -------
    list
        Four LangChain ``@tool`` callables: ``bash``, ``read_file``,
        ``write_file``, ``list_dir``.
    """

    @tool
    def bash(command: str) -> str:
        """Execute a shell command in the sandbox. Returns stdout + stderr.

        The sandbox translates virtual paths (e.g. /mnt/user-data/workspace/...)
        to physical locations and masks physical paths in the output.
        """
        logger.debug("[SandboxTool] bash: %s", command[:120])
        return sandbox.execute_command(command)

    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file in the sandbox.

        Supports virtual paths like /mnt/user-data/workspace/file.py.
        Raises FileNotFoundError if the file does not exist.
        """
        logger.debug("[SandboxTool] read_file: %s", path)
        try:
            return sandbox.read_file(path)
        except FileNotFoundError:
            return f"ERROR: File not found: {path}"
        except IsADirectoryError:
            return f"ERROR: Path is a directory, not a file: {path}"
        except PermissionError as exc:
            return f"ERROR: {exc}"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox. Creates parent directories if needed.

        Supports virtual paths like /mnt/user-data/workspace/file.py.
        """
        logger.debug("[SandboxTool] write_file: %s (%d chars)", path, len(content))
        try:
            sandbox.write_file(path, content)
            return f"Written to {path}"
        except PermissionError as exc:
            return f"ERROR: {exc}"

    @tool
    def list_dir(path: str = "/mnt/user-data/workspace") -> str:
        """List directory contents in the sandbox. Returns a tree view.

        Supports virtual paths. Defaults to the workspace root.
        """
        logger.debug("[SandboxTool] list_dir: %s", path)
        try:
            entries = sandbox.list_dir(path)
            return "\n".join(entries) if entries else "(empty)"
        except NotADirectoryError:
            return f"ERROR: Not a directory: {path}"
        except PermissionError as exc:
            return f"ERROR: {exc}"

    return [bash, read_file, write_file, list_dir]
