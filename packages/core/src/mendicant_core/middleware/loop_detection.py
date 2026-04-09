"""Loop detection middleware — prevents repetitive tool call loops.

Extracted from DeerFlow (P0 safety feature).

Detection strategy:
  1. After each model response, hash the tool calls (name + args).
  2. Track recent hashes in a sliding window per thread.
  3. If the same hash appears >= warn_threshold times, inject a
     warning message (once per hash).
  4. If it appears >= hard_limit times, strip all tool_calls from the
     response so the agent is forced to produce a final text answer.
"""

import hashlib
import json
import logging
import threading
from collections import OrderedDict, defaultdict

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_DEFAULT_WARN_THRESHOLD = 3
_DEFAULT_HARD_LIMIT = 5
_DEFAULT_WINDOW_SIZE = 20
_DEFAULT_MAX_TRACKED_THREADS = 100


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """Deterministic, order-independent hash of tool calls (name + args)."""
    normalized = [
        {"name": tc.get("name", ""), "args": tc.get("args", {})}
        for tc in tool_calls
    ]
    normalized.sort(
        key=lambda tc: (
            tc["name"],
            json.dumps(tc["args"], sort_keys=True, default=str),
        )
    )
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


_WARNING_MSG = (
    "[LOOP DETECTED] You are repeating the same tool calls. "
    "Stop calling tools and produce your final answer now. "
    "If you cannot complete the task, summarize what you accomplished so far."
)

_HARD_STOP_MSG = (
    "[FORCED STOP] Repeated tool calls exceeded the safety limit. "
    "Producing final answer with results collected so far."
)


class LoopDetectionMiddleware:
    """Detects and breaks repetitive tool call loops.

    Operates as an ``after_model`` hook: inspects the last AIMessage for
    tool_calls, hashes them, and tracks frequency in a sliding window.

    Parameters
    ----------
    warn_threshold : int
        Identical call sets before injecting a warning. Default: 3.
    hard_limit : int
        Identical call sets before stripping tool_calls. Default: 5.
    window_size : int
        Sliding window size for tracking. Default: 20.
    max_tracked_threads : int
        LRU eviction limit for tracked threads. Default: 100.
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
    ):
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self._lock = threading.Lock()
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)

    def _get_thread_id(self, state: dict) -> str:
        """Extract thread_id from state or config."""
        thread_id = state.get("thread_id")
        if thread_id:
            return str(thread_id)
        return "default"

    def _evict_if_needed(self) -> None:
        """Evict LRU threads if over limit. Must hold self._lock."""
        while len(self._history) > self.max_tracked_threads:
            evicted_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_id, None)
            logger.debug("Evicted loop tracking for thread %s (LRU)", evicted_id)

    def _track_and_check(self, state: dict) -> tuple[str | None, bool]:
        """Track tool calls and check for loops.

        Returns (warning_message_or_none, should_hard_stop).
        """
        messages = state.get("messages", [])
        if not messages:
            return None, False

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None, False

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None, False

        thread_id = self._get_thread_id(state)
        call_hash = _hash_tool_calls(tool_calls)

        with self._lock:
            if thread_id in self._history:
                self._history.move_to_end(thread_id)
            else:
                self._history[thread_id] = []
                self._evict_if_needed()

            history = self._history[thread_id]
            history.append(call_hash)
            if len(history) > self.window_size:
                history[:] = history[-self.window_size:]

            count = history.count(call_hash)
            tool_names = [tc.get("name", "?") for tc in tool_calls]

            if count >= self.hard_limit:
                logger.error(
                    "Loop hard limit reached — forcing stop (thread=%s, hash=%s, count=%d, tools=%s)",
                    thread_id, call_hash, count, tool_names,
                )
                return _HARD_STOP_MSG, True

            if count >= self.warn_threshold:
                warned = self._warned[thread_id]
                if call_hash not in warned:
                    warned.add(call_hash)
                    logger.warning(
                        "Repetitive tool calls detected (thread=%s, hash=%s, count=%d, tools=%s)",
                        thread_id, call_hash, count, tool_names,
                    )
                    return _WARNING_MSG, False
                return None, False

        return None, False

    def after_model(self, state: dict) -> dict | None:
        """After-model hook: detect and break loops."""
        warning, hard_stop = self._track_and_check(state)

        if hard_stop:
            messages = state.get("messages", [])
            last_msg = messages[-1]
            stripped_msg = last_msg.model_copy(
                update={
                    "tool_calls": [],
                    "content": (last_msg.content or "") + f"\n\n{_HARD_STOP_MSG}",
                }
            )
            return {"messages": [stripped_msg]}

        if warning:
            return {"messages": [HumanMessage(content=warning)]}

        return None

    async def aafter_model(self, state: dict) -> dict | None:
        """Async variant — delegates to sync (CPU-bound hashing)."""
        return self.after_model(state)

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                self._history.pop(thread_id, None)
                self._warned.pop(thread_id, None)
            else:
                self._history.clear()
                self._warned.clear()
