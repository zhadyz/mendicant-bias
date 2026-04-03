"""
mcp_client.py
=============
Mendicant Bias — Simplified MCP Client

Consumes external MCP servers via stdio transport.  Discovers tools exposed
by the server and wraps them so they can be called programmatically.

This is a simplified implementation that covers stdio-based MCP servers.
For full SSE/HTTP transport, OAuth token flows, and multi-server management,
use the upstream DeerFlow MCP client (``deerflow.mcp``).

Usage
-----
::

    client = SimpleMCPClient(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    tools = client.connect()
    for tool in tools:
        print(tool.name, tool.description)
    result = tools[0].call(path="/tmp")
    client.disconnect()
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class MCPClientTool:
    """A tool discovered from an MCP server."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        server_process: subprocess.Popen,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self._process = server_process
        self._call_id = 100  # Starting ID for tool calls

    def call(self, **kwargs: Any) -> str:
        """Call the MCP tool and return the text result."""
        self._call_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._call_id,
            "method": "tools/call",
            "params": {"name": self.name, "arguments": kwargs},
        }
        try:
            self._process.stdin.write(json.dumps(request) + "\n")  # type: ignore[union-attr]
            self._process.stdin.flush()  # type: ignore[union-attr]
            line = self._process.stdout.readline()  # type: ignore[union-attr]
            if not line:
                return "[MCP Error] Server closed connection"
            response = json.loads(line)
            if "result" in response:
                content = response["result"].get("content", [])
                return "\n".join(
                    c.get("text", "")
                    for c in content
                    if c.get("type") == "text"
                )
            error = response.get("error", {})
            return f"[MCP Error] {error.get('message', 'Unknown error')}"
        except (BrokenPipeError, OSError) as exc:
            logger.error("MCP tool call failed: %s", exc)
            return f"[MCP Error] {exc}"
        except json.JSONDecodeError as exc:
            logger.error("MCP response parse error: %s", exc)
            return f"[MCP Error] Invalid JSON response: {exc}"

    def __repr__(self) -> str:
        return f"MCPClientTool(name={self.name!r})"


class SimpleMCPClient:
    """Connect to an MCP server via stdio and discover its tools.

    Parameters
    ----------
    command : str
        The executable to launch (e.g. ``"npx"``, ``"python"``).
    args : list[str] | None
        Arguments to pass to the command.
    env : dict[str, str] | None
        Extra environment variables to set for the server process.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self._process: subprocess.Popen | None = None
        self._tools: list[MCPClientTool] = []
        self._next_id = 0

    def _next_request_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def connect(self) -> list[MCPClientTool]:
        """Start the MCP server process and discover its tools.

        Returns
        -------
        list[MCPClientTool]
            The tools discovered from the server.

        Raises
        ------
        RuntimeError
            If the server fails to start or respond to initialization.
        """
        full_env = {**os.environ, **(self.env or {})}

        try:
            self._process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=full_env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"MCP server command not found: {self.command}. "
                f"Ensure the executable is installed and on PATH."
            ) from exc

        # Step 1: Initialize the MCP session
        init_req = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mendicant-bias", "version": "6.0"},
            },
        }
        self._send(init_req)
        init_response = self._recv()
        if init_response is None:
            self.disconnect()
            raise RuntimeError("MCP server did not respond to initialize request")

        logger.info(
            "MCP session initialized: %s",
            init_response.get("result", {}).get("serverInfo", {}),
        )

        # Step 2: List available tools
        list_req = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/list",
            "params": {},
        }
        self._send(list_req)
        list_response = self._recv()
        if list_response is None:
            self.disconnect()
            raise RuntimeError("MCP server did not respond to tools/list request")

        tools_data = list_response.get("result", {}).get("tools", [])
        self._tools = [
            MCPClientTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_process=self._process,
            )
            for t in tools_data
        ]

        logger.info("Discovered %d MCP tools: %s", len(self._tools), [t.name for t in self._tools])
        return self._tools

    def disconnect(self) -> None:
        """Terminate the MCP server process."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            self._tools = []

    @property
    def tools(self) -> list[MCPClientTool]:
        """The tools discovered from the connected server."""
        return self._tools

    def _send(self, request: dict) -> None:
        """Write a JSON-RPC request to the server's stdin."""
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()

    def _recv(self) -> dict | None:
        """Read a single JSON-RPC response from the server's stdout."""
        assert self._process is not None and self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            logger.error("Failed to parse MCP response: %s", line[:200])
            return None

    def __del__(self) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        tool_names = [t.name for t in self._tools]
        return f"SimpleMCPClient(command={self.command!r}, tools={tool_names})"
