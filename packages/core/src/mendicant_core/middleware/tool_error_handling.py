"""Tool error handling middleware — converts tool exceptions to error messages.

Extracted from DeerFlow. Wraps tool execution so that exceptions become
error ToolMessages instead of crashing the agent run. The agent can then
read the error and choose an alternative approach.
"""

import logging
from collections.abc import Awaitable, Callable

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"


class ToolErrorHandlingMiddleware:
    """Convert tool exceptions into error ToolMessages so the run can continue.

    Wraps each tool call in a try-catch. On exception:
    - Logs the full traceback
    - Returns a ToolMessage with status="error" and a truncated detail string
    - Preserves LangGraph control-flow signals (GraphBubbleUp)
    """

    def _build_error_message(self, request, exc: Exception) -> ToolMessage:
        tool_call = getattr(request, "tool_call", request) if not isinstance(request, dict) else request
        tool_name = str(tool_call.get("name", "unknown_tool") if isinstance(tool_call, dict) else getattr(tool_call, "name", "unknown_tool"))
        tool_call_id = str(tool_call.get("id", _MISSING_TOOL_CALL_ID) if isinstance(tool_call, dict) else getattr(tool_call, "id", _MISSING_TOOL_CALL_ID))
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:
            detail = detail[:497] + "..."

        content = (
            f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: "
            f"{detail}. Continue with available context, or choose an alternative tool."
        )
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def wrap_tool_call(self, request, handler: Callable):
        """Sync tool call wrapper — catches exceptions, returns error ToolMessage."""
        try:
            return handler(request)
        except Exception as exc:
            # Preserve LangGraph control-flow signals
            try:
                from langgraph.errors import GraphBubbleUp
                if isinstance(exc, GraphBubbleUp):
                    raise
            except ImportError:
                pass
            logger.exception(
                "Tool execution failed (sync): %s",
                getattr(request, "tool_call", request),
            )
            return self._build_error_message(request, exc)

    async def awrap_tool_call(self, request, handler: Callable[..., Awaitable]):
        """Async tool call wrapper — catches exceptions, returns error ToolMessage."""
        try:
            return await handler(request)
        except Exception as exc:
            try:
                from langgraph.errors import GraphBubbleUp
                if isinstance(exc, GraphBubbleUp):
                    raise
            except ImportError:
                pass
            logger.exception(
                "Tool execution failed (async): %s",
                getattr(request, "tool_call", request),
            )
            return self._build_error_message(request, exc)
