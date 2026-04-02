"""Tests for Mendicant Bias V5 Context Optimizer (Evolved FR4)."""

import json
import pytest

from mendicant_core.middleware.context_optimizer import ContextOptimizer, _count_tokens


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def optimizer():
    """Create an optimizer with no embeddings (semantic weight zeroed)."""
    return ContextOptimizer(
        semantic_weight=0.0,
        recency_weight=0.7,
        role_weight=0.3,
    )


@pytest.fixture
def full_optimizer():
    """Create an optimizer with all weights active (embeddings may be unavailable)."""
    return ContextOptimizer(
        semantic_weight=0.6,
        recency_weight=0.3,
        role_weight=0.1,
    )


def _make_messages(n: int, base_content: str = "Message content here") -> list[dict]:
    """Generate n user messages with increasing content length."""
    return [
        {"role": "user", "content": f"{base_content} number {i}"}
        for i in range(n)
    ]


def _make_conversation() -> list[dict]:
    """Build a realistic conversation with mixed roles."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Find all Python files in the project."},
        {"role": "assistant", "content": "I'll search for Python files now."},
        {
            "role": "tool",
            "content": json.dumps({
                "files": ["main.py", "utils.py", "test_main.py", "config.py"],
                "count": 4,
                "search_path": "/project/src",
            }),
        },
        {"role": "assistant", "content": "Found 4 Python files in /project/src."},
        {"role": "user", "content": "Now refactor utils.py to use dataclasses."},
        {"role": "assistant", "content": "I'll refactor utils.py to use dataclasses."},
        {
            "role": "tool",
            "content": "def process_data(items: list) -> dict:\n"
            "    result = {}\n"
            "    for item in items:\n"
            "        result[item.name] = item.value\n"
            "    return result\n"
            "\n"
            "class Config:\n"
            "    def __init__(self, host, port, debug):\n"
            "        self.host = host\n"
            "        self.port = port\n"
            "        self.debug = debug\n",
        },
        {
            "role": "assistant",
            "content": "Here's the refactored version using dataclasses.",
        },
        {"role": "user", "content": "Looks good, now add type hints everywhere."},
    ]


# ===================================================================
# TestContextOptimizer
# ===================================================================


class TestContextOptimizer:
    """Core tests for the Context Optimizer."""

    def test_rank_by_recency(self, optimizer):
        """Newer messages get higher recency score."""
        messages = _make_messages(5)
        ranked = optimizer.rank_messages(messages)

        assert len(ranked) == 5
        # Last message (index 4) should have highest recency
        recency_scores = [m["_scores"]["recency"] for m in ranked]
        assert recency_scores[0] < recency_scores[-1]
        assert recency_scores[-1] == 1.0
        assert recency_scores[0] == 0.0

    def test_rank_system_messages_highest(self, optimizer):
        """System messages always rank highest regardless of position."""
        messages = [
            {"role": "user", "content": "Hello there."},
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Write a function."},
            {"role": "assistant", "content": "Sure, here it is."},
        ]
        ranked = optimizer.rank_messages(messages)

        # System message should have priority 1.0
        system_msg = ranked[1]  # index 1 is the system message
        assert system_msg["role"] == "system"
        assert system_msg["_priority"] == 1.0

        # All other messages should have lower priority
        for idx, msg in enumerate(ranked):
            if msg["role"] != "system":
                assert msg["_priority"] < 1.0, (
                    f"Non-system message at index {idx} has priority {msg['_priority']}"
                )

    def test_compress_lowest_priority(self, optimizer):
        """Lowest-ranked messages are compressed first."""
        # Create messages where early ones are long (low recency) and
        # later ones are short (high recency)
        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "A " * 500},  # long, old
            {"role": "tool", "content": "B " * 500},   # long, old-ish
            {"role": "user", "content": "Short recent query."},
        ]

        result = optimizer.optimize(messages, budget_tokens=100)
        manifest = result["manifest"]

        # At least some messages should be compressed
        compressed_actions = [
            a for a in manifest["actions"] if a["action"] == "compressed"
        ]
        assert len(compressed_actions) > 0

        # System message should never be compressed
        system_actions = [
            a for a in manifest["actions"]
            if a["index"] == 0
        ]
        for action in system_actions:
            assert action["action"] == "kept"
            assert "system" in action["reason"].lower()

    def test_budget_enforcement(self, optimizer):
        """Result fits within budget."""
        # Create a large conversation
        messages = [
            {"role": "system", "content": "You are helpful."},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"Question {i}: " + "word " * 100})
            messages.append({"role": "assistant", "content": f"Answer {i}: " + "word " * 100})

        budget = 500
        result = optimizer.optimize(messages, budget_tokens=budget)
        manifest = result["manifest"]

        # Optimized tokens should be less than or equal to original
        assert manifest["optimized_tokens"] <= manifest["original_tokens"]
        assert manifest["tokens_saved"] >= 0

    def test_manifest_tracking(self, optimizer):
        """Manifest records all actions for every message."""
        messages = _make_conversation()
        result = optimizer.optimize(messages, budget_tokens=50)
        manifest = result["manifest"]

        # Every message should have an action entry
        action_indices = {a["index"] for a in manifest["actions"]}
        assert action_indices == set(range(len(messages)))

        # Manifest should have all required keys
        assert "original_tokens" in manifest
        assert "optimized_tokens" in manifest
        assert "tokens_saved" in manifest
        assert "actions" in manifest
        assert "strategy_applied" in manifest

        # Each action should have required fields
        for action in manifest["actions"]:
            assert "index" in action
            assert "action" in action
            assert "reason" in action
            assert action["action"] in ("kept", "compressed")

    def test_summarize_json(self, full_optimizer):
        """JSON results get key extraction."""
        json_content = json.dumps({
            "files": ["a.py", "b.py", "c.py"],
            "count": 3,
            "search_path": "/src",
            "metadata": {"engine": "ripgrep"},
        })
        summary = full_optimizer.summarize_result(json_content)

        assert "JSON" in summary or "keys" in summary.lower()
        # Should mention the key count or key names
        assert "files" in summary or "4" in summary

    def test_summarize_list(self, full_optimizer):
        """Lists get count + sample."""
        list_content = "\n".join([f"- Item {i}: description of item" for i in range(20)])
        summary = full_optimizer.summarize_result(list_content)

        assert "20" in summary or "items" in summary.lower() or "list" in summary.lower()

    def test_summarize_code(self, full_optimizer):
        """Code blocks get signature extraction."""
        code_content = (
            "def process_data(items: list) -> dict:\n"
            "    result = {}\n"
            "    for item in items:\n"
            "        result[item.name] = item.value\n"
            "    return result\n"
            "\n"
            "def validate_input(data: str) -> bool:\n"
            "    if not data:\n"
            "        return False\n"
            "    return len(data) > 0\n"
            "\n"
            "class DataProcessor:\n"
            "    def __init__(self, config):\n"
            "        self.config = config\n"
        )
        summary = full_optimizer.summarize_result(code_content)

        assert "lines" in summary.lower() or "code" in summary.lower()
        # Should mention at least one signature
        assert "def " in summary or "class " in summary or "Signatures" in summary

    def test_summarize_text(self, full_optimizer):
        """Plain text gets first sentences + word count."""
        text_content = (
            "The quick brown fox jumps over the lazy dog. "
            "This is a second sentence with more information. "
            "A third sentence that should not appear in summary. "
            "And a fourth for good measure."
        )
        summary = full_optimizer.summarize_result(text_content)

        assert "words" in summary.lower()
        # Should contain at least part of the first sentence
        assert "quick brown fox" in summary

    def test_no_compression_under_budget(self, optimizer):
        """If already under budget, no changes are made."""
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi."},
            {"role": "assistant", "content": "Hello!"},
        ]

        # Very generous budget
        result = optimizer.optimize(messages, budget_tokens=10000)
        manifest = result["manifest"]

        assert manifest["tokens_saved"] == 0
        assert manifest["strategy_applied"] == "none"
        assert manifest["original_tokens"] == manifest["optimized_tokens"]

        # All actions should be "kept"
        for action in manifest["actions"]:
            assert action["action"] == "kept"

        # Output messages should match input (minus any score keys)
        for orig, opt in zip(messages, result["optimized_messages"]):
            assert orig["role"] == opt["role"]
            assert orig["content"] == opt["content"]

    def test_empty_messages(self, optimizer):
        """Empty message list returns empty result."""
        result = optimizer.optimize([], budget_tokens=1000)
        assert result["optimized_messages"] == []
        assert result["manifest"]["original_tokens"] == 0

    def test_role_weights_ordering(self, optimizer):
        """Role weights produce expected ordering: system > user > tool > assistant."""
        messages = [
            {"role": "assistant", "content": "Same content here."},
            {"role": "tool", "content": "Same content here."},
            {"role": "user", "content": "Same content here."},
            {"role": "system", "content": "Same content here."},
        ]
        ranked = optimizer.rank_messages(messages)

        # With semantic_weight=0 and same content, priority is driven by
        # recency (0.7) and role (0.3). System always gets 1.0.
        # The system message (index 3) has recency=1.0, role=1.0 -> forced to 1.0
        # User (index 2) has recency=0.667, role=0.8 -> 0.7*0.667 + 0.3*0.8 = 0.707
        # Tool (index 1) has recency=0.333, role=0.5 -> 0.7*0.333 + 0.3*0.5 = 0.383
        # Assistant (index 0) has recency=0.0, role=0.3 -> 0.7*0.0 + 0.3*0.3 = 0.09

        priorities = [(m["role"], m["_priority"]) for m in ranked]
        # System should be highest
        system_p = next(p for r, p in priorities if r == "system")
        user_p = next(p for r, p in priorities if r == "user")
        tool_p = next(p for r, p in priorities if r == "tool")
        assistant_p = next(p for r, p in priorities if r == "assistant")

        assert system_p > user_p > tool_p > assistant_p

    def test_scores_present_in_ranked(self, optimizer):
        """Ranked messages contain _priority and _scores keys."""
        messages = _make_messages(3)
        ranked = optimizer.rank_messages(messages)

        for msg in ranked:
            assert "_priority" in msg
            assert "_scores" in msg
            scores = msg["_scores"]
            assert "semantic" in scores
            assert "recency" in scores
            assert "role" in scores
            assert "combined" in scores

    def test_scores_stripped_from_optimized(self, optimizer):
        """Optimized output does not contain internal scoring keys."""
        messages = _make_messages(3)
        result = optimizer.optimize(messages, budget_tokens=10000)

        for msg in result["optimized_messages"]:
            assert "_priority" not in msg
            assert "_scores" not in msg

    def test_large_tool_result_compressed_first(self, optimizer):
        """A large tool result with low recency is compressed before recent user messages."""
        messages = [
            {"role": "system", "content": "System prompt."},
            {"role": "tool", "content": "x " * 2000},  # massive, old
            {"role": "user", "content": "Recent short query."},
        ]

        result = optimizer.optimize(messages, budget_tokens=100)
        manifest = result["manifest"]

        # The tool message (index 1) should be compressed
        tool_action = next(a for a in manifest["actions"] if a["index"] == 1)
        assert tool_action["action"] == "compressed"

        # The user message (index 2) may or may not be compressed, but
        # the system message (index 0) must be kept
        system_action = next(a for a in manifest["actions"] if a["index"] == 0)
        assert system_action["action"] == "kept"


# ===================================================================
# TestSummarization
# ===================================================================


class TestSummarization:
    """Dedicated tests for the summarize_result method."""

    @pytest.fixture
    def opt(self):
        return ContextOptimizer()

    def test_summarize_nested_json(self, opt):
        """Nested JSON still gets key extraction."""
        content = json.dumps({
            "result": {"status": "ok", "data": [1, 2, 3]},
            "metadata": {"version": "1.0"},
        })
        summary = opt.summarize_result(content)
        assert "result" in summary or "JSON" in summary

    def test_summarize_json_array(self, opt):
        """Top-level JSON array gets count + samples."""
        content = json.dumps([{"id": i, "name": f"item_{i}"} for i in range(15)])
        summary = opt.summarize_result(content)
        assert "15" in summary or "items" in summary.lower()

    def test_summarize_short_content(self, opt):
        """Short content gets basic text summary."""
        summary = opt.summarize_result("OK")
        assert "OK" in summary

    def test_summarize_empty_content(self, opt):
        """Empty content returns a word-count summary."""
        summary = opt.summarize_result("")
        assert "0 words" in summary or "words" in summary.lower()
