"""Tests for Mendicant Bias new middleware — Dangling Tool Call, Guardrails, Summarization."""

import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ===================================================================
# TestDanglingToolCallMiddleware
# ===================================================================


class TestDanglingToolCallMiddleware:
    """Test that dangling tool calls are patched with placeholder ToolMessages."""

    def _make_middleware(self):
        from mendicant_core.middleware.dangling_tool_call import DanglingToolCallMiddleware

        return DanglingToolCallMiddleware()

    def test_no_messages_returns_none(self):
        """Empty message list should produce no patches."""
        mw = self._make_middleware()
        result = mw._build_patched_messages([])
        assert result is None

    def test_no_dangling_returns_none(self):
        """When all tool_calls have matching ToolMessages, no patching needed."""
        mw = self._make_middleware()
        messages = [
            HumanMessage(content="hello"),
            AIMessage(content="", tool_calls=[{"id": "tc1", "name": "bash", "args": {}}]),
            ToolMessage(content="ok", tool_call_id="tc1", name="bash"),
        ]
        result = mw._build_patched_messages(messages)
        assert result is None

    def test_patches_single_dangling_call(self):
        """A single dangling tool call gets a placeholder ToolMessage."""
        mw = self._make_middleware()
        messages = [
            HumanMessage(content="hello"),
            AIMessage(content="", tool_calls=[{"id": "tc1", "name": "bash", "args": {}}]),
        ]
        result = mw._build_patched_messages(messages)
        assert result is not None
        assert len(result) == 3  # original 2 + 1 patch
        patch = result[2]
        assert isinstance(patch, ToolMessage)
        assert patch.tool_call_id == "tc1"
        assert patch.name == "bash"
        assert "interrupted" in patch.content.lower()
        assert patch.status == "error"

    def test_patches_multiple_dangling_calls(self):
        """Multiple dangling tool calls in one AIMessage all get patches."""
        mw = self._make_middleware()
        messages = [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": "bash", "args": {}},
                    {"id": "tc2", "name": "read_file", "args": {}},
                ],
            ),
        ]
        result = mw._build_patched_messages(messages)
        assert result is not None
        # 1 AIMessage + 2 patches
        assert len(result) == 3
        assert result[1].tool_call_id == "tc1"
        assert result[2].tool_call_id == "tc2"

    def test_patches_inserted_after_correct_ai_message(self):
        """Patches are inserted immediately after the dangling AIMessage, not at end."""
        mw = self._make_middleware()
        messages = [
            AIMessage(content="", tool_calls=[{"id": "tc1", "name": "bash", "args": {}}]),
            HumanMessage(content="what happened?"),
            AIMessage(content="", tool_calls=[{"id": "tc2", "name": "ls", "args": {}}]),
        ]
        result = mw._build_patched_messages(messages)
        assert result is not None
        # Expected: AI(tc1), patch(tc1), Human, AI(tc2), patch(tc2)
        assert len(result) == 5
        assert isinstance(result[0], AIMessage)
        assert isinstance(result[1], ToolMessage) and result[1].tool_call_id == "tc1"
        assert isinstance(result[2], HumanMessage)
        assert isinstance(result[3], AIMessage)
        assert isinstance(result[4], ToolMessage) and result[4].tool_call_id == "tc2"

    def test_partial_dangling_only_patches_missing(self):
        """When one tool call has a response and another doesn't, only the missing one is patched."""
        mw = self._make_middleware()
        messages = [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": "bash", "args": {}},
                    {"id": "tc2", "name": "read_file", "args": {}},
                ],
            ),
            ToolMessage(content="ok", tool_call_id="tc1", name="bash"),
        ]
        result = mw._build_patched_messages(messages)
        assert result is not None
        # Find all ToolMessages for tc2
        tc2_msgs = [m for m in result if isinstance(m, ToolMessage) and m.tool_call_id == "tc2"]
        assert len(tc2_msgs) == 1
        assert "interrupted" in tc2_msgs[0].content.lower()

    def test_no_duplicate_patches(self):
        """The same tool_call_id should not be patched twice."""
        mw = self._make_middleware()
        messages = [
            AIMessage(content="", tool_calls=[{"id": "tc1", "name": "bash", "args": {}}]),
        ]
        result = mw._build_patched_messages(messages)
        tc1_patches = [m for m in result if isinstance(m, ToolMessage) and m.tool_call_id == "tc1"]
        assert len(tc1_patches) == 1

    def test_ai_message_without_tool_calls_ignored(self):
        """Plain AIMessages (no tool_calls) should not trigger patching."""
        mw = self._make_middleware()
        messages = [
            HumanMessage(content="hello"),
            AIMessage(content="just text, no tools"),
        ]
        result = mw._build_patched_messages(messages)
        assert result is None


# ===================================================================
# TestGuardrailMiddleware — AllowlistProvider
# ===================================================================


class TestAllowlistProvider:
    """Test the AllowlistProvider."""

    def _make_provider(self, allowed=None, denied=None):
        from mendicant_core.middleware.guardrails import AllowlistProvider, GuardrailRequest

        return AllowlistProvider(allowed_tools=allowed, denied_tools=denied), GuardrailRequest

    def test_empty_allowlist_allows_all(self):
        """When no tools are specified, everything is allowed."""
        provider, GuardrailRequest = self._make_provider()
        decision = provider.evaluate(GuardrailRequest(tool_name="bash", tool_input={}))
        assert decision.allow is True

    def test_allowlist_allows_listed_tool(self):
        """A tool in the allowlist is allowed."""
        provider, GuardrailRequest = self._make_provider(allowed=["bash", "read_file"])
        decision = provider.evaluate(GuardrailRequest(tool_name="bash", tool_input={}))
        assert decision.allow is True

    def test_allowlist_blocks_unlisted_tool(self):
        """A tool not in the allowlist is blocked."""
        provider, GuardrailRequest = self._make_provider(allowed=["bash"])
        decision = provider.evaluate(GuardrailRequest(tool_name="write_file", tool_input={}))
        assert decision.allow is False
        assert decision.reasons[0].code == "mendicant.tool_not_allowed"

    def test_denylist_blocks_denied_tool(self):
        """A tool in the denylist is blocked even with no allowlist."""
        provider, GuardrailRequest = self._make_provider(denied=["dangerous_tool"])
        decision = provider.evaluate(GuardrailRequest(tool_name="dangerous_tool", tool_input={}))
        assert decision.allow is False
        assert decision.reasons[0].code == "mendicant.tool_denied"

    def test_combined_allowlist_and_denylist(self):
        """Denylist takes priority: a tool in both lists is denied."""
        provider, GuardrailRequest = self._make_provider(allowed=["bash", "rm_rf"], denied=["rm_rf"])
        decision = provider.evaluate(GuardrailRequest(tool_name="rm_rf", tool_input={}))
        assert decision.allow is False


# ===================================================================
# TestGuardrailMiddleware — DenylistProvider
# ===================================================================


class TestDenylistProvider:
    """Test the DenylistProvider."""

    def _make_provider(self, denied):
        from mendicant_core.middleware.guardrails import DenylistProvider, GuardrailRequest

        return DenylistProvider(denied_tools=denied), GuardrailRequest

    def test_allows_non_denied_tool(self):
        """A tool not in the denylist is allowed."""
        provider, GuardrailRequest = self._make_provider(denied=["rm_rf"])
        decision = provider.evaluate(GuardrailRequest(tool_name="bash", tool_input={}))
        assert decision.allow is True

    def test_blocks_denied_tool(self):
        """A tool in the denylist is blocked."""
        provider, GuardrailRequest = self._make_provider(denied=["rm_rf", "drop_table"])
        decision = provider.evaluate(GuardrailRequest(tool_name="drop_table", tool_input={}))
        assert decision.allow is False
        assert decision.reasons[0].code == "mendicant.tool_denied"

    def test_empty_denylist_allows_all(self):
        """An empty denylist allows everything."""
        provider, GuardrailRequest = self._make_provider(denied=[])
        decision = provider.evaluate(GuardrailRequest(tool_name="anything", tool_input={}))
        assert decision.allow is True


# ===================================================================
# TestGuardrailMiddleware — Middleware behaviour
# ===================================================================


class TestGuardrailMiddleware:
    """Test the GuardrailMiddleware wrap_tool_call behaviour."""

    def _make_middleware(self, provider, fail_closed=True):
        from mendicant_core.middleware.guardrails import GuardrailMiddleware

        return GuardrailMiddleware(provider, fail_closed=fail_closed)

    def _make_tool_call_request(self, name="bash", args=None, tc_id="tc1"):
        """Build a mock ToolCallRequest."""
        mock = MagicMock()
        mock.tool_call = {"name": name, "args": args or {}, "id": tc_id}
        return mock

    def test_allowed_call_passes_through(self):
        """An allowed tool call should be forwarded to the handler."""
        from mendicant_core.middleware.guardrails import AllowlistProvider

        provider = AllowlistProvider(allowed_tools=["bash"])
        mw = self._make_middleware(provider)

        request = self._make_tool_call_request(name="bash")
        expected = ToolMessage(content="result", tool_call_id="tc1", name="bash")
        handler = MagicMock(return_value=expected)

        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == expected

    def test_denied_call_returns_error_message(self):
        """A denied tool call should return an error ToolMessage without calling the handler."""
        from mendicant_core.middleware.guardrails import AllowlistProvider

        provider = AllowlistProvider(allowed_tools=["bash"])
        mw = self._make_middleware(provider)

        request = self._make_tool_call_request(name="rm_rf")
        handler = MagicMock()

        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)
        assert "denied" in result.content.lower()
        assert result.status == "error"

    def test_fail_closed_on_provider_error(self):
        """When the provider raises and fail_closed=True, the call is blocked."""
        provider = MagicMock()
        provider.evaluate.side_effect = RuntimeError("provider broke")
        mw = self._make_middleware(provider, fail_closed=True)

        request = self._make_tool_call_request(name="bash")
        handler = MagicMock()

        result = mw.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert isinstance(result, ToolMessage)
        assert "denied" in result.content.lower() or "blocked" in result.content.lower()

    def test_fail_open_on_provider_error(self):
        """When the provider raises and fail_closed=False, the call passes through."""
        provider = MagicMock()
        provider.evaluate.side_effect = RuntimeError("provider broke")
        mw = self._make_middleware(provider, fail_closed=False)

        request = self._make_tool_call_request(name="bash")
        expected = ToolMessage(content="ok", tool_call_id="tc1", name="bash")
        handler = MagicMock(return_value=expected)

        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == expected


# ===================================================================
# TestGuardrailMiddleware — Async behaviour
# ===================================================================


class TestGuardrailMiddlewareAsync:
    """Test the async awrap_tool_call behaviour."""

    def _make_middleware(self, provider, fail_closed=True):
        from mendicant_core.middleware.guardrails import GuardrailMiddleware

        return GuardrailMiddleware(provider, fail_closed=fail_closed)

    def _make_tool_call_request(self, name="bash", args=None, tc_id="tc1"):
        mock = MagicMock()
        mock.tool_call = {"name": name, "args": args or {}, "id": tc_id}
        return mock

    @pytest.mark.asyncio
    async def test_async_allowed_passes_through(self):
        """Async: allowed tool call forwarded to handler."""
        from mendicant_core.middleware.guardrails import AllowlistProvider

        provider = AllowlistProvider(allowed_tools=["bash"])
        mw = self._make_middleware(provider)

        request = self._make_tool_call_request(name="bash")
        expected = ToolMessage(content="result", tool_call_id="tc1", name="bash")

        async def handler(req):
            return expected

        result = await mw.awrap_tool_call(request, handler)
        assert result == expected

    @pytest.mark.asyncio
    async def test_async_denied_returns_error(self):
        """Async: denied tool call returns error ToolMessage."""
        from mendicant_core.middleware.guardrails import AllowlistProvider

        provider = AllowlistProvider(allowed_tools=["bash"])
        mw = self._make_middleware(provider)

        request = self._make_tool_call_request(name="rm_rf")

        async def handler(req):
            return ToolMessage(content="should not reach", tool_call_id="tc1", name="rm_rf")

        result = await mw.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert "denied" in result.content.lower()


# ===================================================================
# TestSummarizationMiddleware
# ===================================================================


class TestSummarizationMiddleware:
    """Test the Summarization Middleware."""

    def _make_middleware(self, max_messages=10, keep_recent=3, preview_chars=50):
        from mendicant_core.middleware.summarization import SummarizationMiddleware

        return SummarizationMiddleware(
            max_messages=max_messages,
            keep_recent=keep_recent,
            preview_chars=preview_chars,
        )

    def _make_runtime(self):
        mock = MagicMock()
        mock.context = {}
        return mock

    def test_no_summarization_below_threshold(self):
        """When messages are under max_messages, returns None (no change)."""
        mw = self._make_middleware(max_messages=10)
        state = {"messages": [HumanMessage(content=f"msg {i}") for i in range(5)]}
        result = mw.before_model(state, self._make_runtime())
        assert result is None

    def test_no_summarization_at_threshold(self):
        """Exactly at max_messages, returns None."""
        mw = self._make_middleware(max_messages=10)
        state = {"messages": [HumanMessage(content=f"msg {i}") for i in range(10)]}
        result = mw.before_model(state, self._make_runtime())
        assert result is None

    def test_summarization_over_threshold(self):
        """Over max_messages triggers summarization."""
        mw = self._make_middleware(max_messages=5, keep_recent=2)
        messages = [HumanMessage(content=f"message number {i}") for i in range(8)]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        assert result is not None
        # Should have: 1 summary + 2 recent = 3 messages (no system msgs in this case)
        assert len(result["messages"]) == 3
        # First message is the summary
        assert "CONTEXT SUMMARY" in result["messages"][0].content
        assert "6 older messages condensed" in result["messages"][0].content

    def test_system_messages_preserved(self):
        """System messages are always preserved at the front."""
        mw = self._make_middleware(max_messages=5, keep_recent=2)
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            SystemMessage(content="Additional system instruction."),
        ] + [HumanMessage(content=f"msg {i}") for i in range(8)]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        assert result is not None
        # System messages should be at the front
        assert isinstance(result["messages"][0], SystemMessage)
        assert isinstance(result["messages"][1], SystemMessage)
        assert result["messages"][0].content == "You are a helpful assistant."

    def test_keep_recent_messages_intact(self):
        """The most recent non-system messages are kept verbatim."""
        mw = self._make_middleware(max_messages=5, keep_recent=3)
        messages = [HumanMessage(content=f"message_{i}") for i in range(10)]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        assert result is not None
        # Last 3 should be the original recent messages
        recent = result["messages"][-3:]
        assert recent[0].content == "message_7"
        assert recent[1].content == "message_8"
        assert recent[2].content == "message_9"

    def test_summary_contains_previews(self):
        """The summary message includes truncated previews of older messages."""
        mw = self._make_middleware(max_messages=5, keep_recent=2, preview_chars=20)
        messages = [HumanMessage(content="A" * 50)] + [
            HumanMessage(content=f"msg {i}") for i in range(7)
        ]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        summary = result["messages"][0]
        # The long message should be truncated with "..."
        assert "..." in summary.content
        # The summary should mention the role type
        assert "[human]" in summary.content

    def test_few_non_system_under_keep_recent_no_change(self):
        """If non-system messages <= keep_recent, even above max_messages with system, no change."""
        mw = self._make_middleware(max_messages=3, keep_recent=5)
        messages = [
            SystemMessage(content="sys"),
            SystemMessage(content="sys2"),
            SystemMessage(content="sys3"),
            SystemMessage(content="sys4"),
            HumanMessage(content="msg1"),
            HumanMessage(content="msg2"),
        ]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        # 2 non-system messages <= keep_recent=5, so no summarization
        assert result is None

    def test_empty_messages(self):
        """Empty messages list returns None."""
        mw = self._make_middleware(max_messages=5)
        state = {"messages": []}
        result = mw.before_model(state, self._make_runtime())
        assert result is None

    def test_max_summary_entries_limit(self):
        """Summary should only include the last max_summary_entries older messages."""
        mw = self._make_middleware(max_messages=5, keep_recent=2)
        mw.max_summary_entries = 3
        messages = [HumanMessage(content=f"msg_{i}") for i in range(20)]
        state = {"messages": messages}
        result = mw.before_model(state, self._make_runtime())
        summary = result["messages"][0]
        # 18 older messages, but only 3 entries in summary
        assert "18 older messages condensed" in summary.content
        # Count the [human] entries in the summary
        human_entries = summary.content.count("[human]")
        assert human_entries == 3


# ===================================================================
# TestMCPClient (unit tests, no real server)
# ===================================================================


class TestMCPClientTool:
    """Unit tests for MCPClientTool without a real server process."""

    def test_repr(self):
        from mendicant_core.mcp_client import MCPClientTool

        mock_process = MagicMock()
        tool = MCPClientTool(
            name="test_tool",
            description="A test",
            input_schema={},
            server_process=mock_process,
        )
        assert "test_tool" in repr(tool)

    def test_call_success(self):
        """Tool call returns text content from MCP response."""
        from mendicant_core.mcp_client import MCPClientTool

        mock_process = MagicMock()
        response = {
            "jsonrpc": "2.0",
            "id": 101,
            "result": {
                "content": [{"type": "text", "text": "hello world"}]
            },
        }
        mock_process.stdout.readline.return_value = json.dumps(response)
        mock_process.stdin = MagicMock()

        tool = MCPClientTool(
            name="greet",
            description="Greet",
            input_schema={},
            server_process=mock_process,
        )
        result = tool.call(name="Alice")
        assert result == "hello world"

    def test_call_error_response(self):
        """Tool call handles MCP error response."""
        from mendicant_core.mcp_client import MCPClientTool

        mock_process = MagicMock()
        response = {
            "jsonrpc": "2.0",
            "id": 101,
            "error": {"code": -32600, "message": "Invalid request"},
        }
        mock_process.stdout.readline.return_value = json.dumps(response)
        mock_process.stdin = MagicMock()

        tool = MCPClientTool(
            name="broken",
            description="Broken",
            input_schema={},
            server_process=mock_process,
        )
        result = tool.call()
        assert "Invalid request" in result

    def test_call_empty_response(self):
        """Tool call handles server closing connection."""
        from mendicant_core.mcp_client import MCPClientTool

        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.stdin = MagicMock()

        tool = MCPClientTool(
            name="dead",
            description="Dead",
            input_schema={},
            server_process=mock_process,
        )
        result = tool.call()
        assert "Error" in result


class TestSimpleMCPClient:
    """Unit tests for SimpleMCPClient without a real server."""

    def test_repr_no_tools(self):
        from mendicant_core.mcp_client import SimpleMCPClient

        client = SimpleMCPClient(command="echo", args=["hello"])
        assert "echo" in repr(client)
        assert "tools=[]" in repr(client)

    def test_disconnect_without_connect(self):
        """Disconnecting before connecting should not raise."""
        from mendicant_core.mcp_client import SimpleMCPClient

        client = SimpleMCPClient(command="echo")
        client.disconnect()  # Should not raise

    def test_tools_property_empty_initially(self):
        """Tools property should be empty before connect."""
        from mendicant_core.mcp_client import SimpleMCPClient

        client = SimpleMCPClient(command="echo")
        assert client.tools == []


# We need json for test_call_success etc.
import json
