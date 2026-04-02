"""
mendicant_runtime.agent
=======================
Mendicant Bias V5 — Agent Factory & Managed Runtime

Creates a LangGraph ``create_react_agent`` with the full Mendicant middleware
chain wired in proper order:

  before_agent:  SmartTaskRouter -> SemanticToolRouter
  before_model:  ContextBudget
  after_agent:   Verification -> AdaptiveLearning

Provides two levels of abstraction:

``make_mendicant_agent``
    One-call factory that returns a compiled LangGraph agent.

``MendicantRuntime``
    Managed runtime with thread state persistence, tool registration,
    config loading from YAML, and convenience invoke/stream methods.

Usage::

    # Quick — one-call factory
    from mendicant_runtime import make_mendicant_agent
    agent = make_mendicant_agent(model_name="claude-sonnet-4-20250514")

    # Full — managed runtime
    from mendicant_runtime import MendicantRuntime
    runtime = MendicantRuntime.from_yaml("mendicant.yaml")
    result = runtime.invoke("What is the capital of France?")
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from mendicant_core import MendicantConfig
from mendicant_core.middleware import (
    AdaptiveLearningMiddleware,
    ContextBudgetMiddleware,
    SemanticToolRouterMiddleware,
    SmartTaskRouterMiddleware,
    VerificationMiddleware,
)

from mendicant_runtime.config import load_config
from mendicant_runtime.models import create_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Middleware chain builder
# ---------------------------------------------------------------------------


def _build_middleware_chain(
    config: MendicantConfig,
) -> list[Any]:
    """
    Build the ordered middleware stack from a MendicantConfig.

    Middleware order:
      1. SmartTaskRouter (before_agent) — classifies task type and sets flags
      2. SemanticToolRouter (before_agent) — selects relevant tools
      3. ContextBudget (before_model) — enforces token budget
      4. Verification (after_agent) — quality gate (if enabled)
      5. AdaptiveLearning (after_agent) — records patterns

    Parameters
    ----------
    config : MendicantConfig
        The unified Mendicant configuration.

    Returns
    -------
    list
        Ordered list of middleware instances.
    """
    middlewares: list[Any] = []

    # FR5: Smart Task Router — before_agent
    middlewares.append(config.build_smart_task_router_middleware())
    logger.debug("[MiddlewareChain] Added SmartTaskRouter")

    # FR1: Semantic Tool Router — before_agent
    middlewares.append(config.build_semantic_tool_router_middleware())
    logger.debug("[MiddlewareChain] Added SemanticToolRouter")

    # FR4: Context Budget — before_model
    middlewares.append(config.build_context_budget_middleware())
    logger.debug("[MiddlewareChain] Added ContextBudget")

    # FR2: Verification Gate — after_agent (conditional)
    if config.verification.enabled:
        middlewares.append(config.build_verification_middleware())
        logger.debug("[MiddlewareChain] Added Verification")
    else:
        logger.debug("[MiddlewareChain] Verification disabled — skipped")

    # FR3: Adaptive Learning — after_agent
    middlewares.append(config.build_adaptive_learning_middleware())
    logger.debug("[MiddlewareChain] Added AdaptiveLearning")

    logger.info(
        "[MiddlewareChain] Built %d middleware: %s",
        len(middlewares),
        [type(m).__name__ for m in middlewares],
    )

    return middlewares


# ---------------------------------------------------------------------------
# One-call agent factory
# ---------------------------------------------------------------------------


def make_mendicant_agent(
    config: MendicantConfig | None = None,
    model_name: str = "claude-sonnet-4-20250514",
    tools: list | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    **model_kwargs: Any,
) -> Any:
    """
    Create a LangGraph react agent with the full Mendicant middleware chain.

    This is the simplest way to get a Mendicant-powered agent running.
    For more control over threads and tool registration, use
    ``MendicantRuntime`` instead.

    Parameters
    ----------
    config : MendicantConfig | None
        Mendicant middleware configuration.  If ``None``, defaults are used.
    model_name : str
        LLM model name (supports ``anthropic/``, ``openai/`` prefixes or
        bare names with auto-detection).
    tools : list | None
        List of LangChain tools to bind to the agent.
    temperature : float
        Model sampling temperature.
    max_tokens : int
        Maximum tokens for the model response.
    **model_kwargs
        Additional keyword arguments passed to the model constructor.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph agent ready for ``.invoke()`` or ``.stream()``.
    """
    from langgraph.prebuilt import create_react_agent

    cfg = config or MendicantConfig()

    # Build middleware stack
    middlewares = _build_middleware_chain(cfg)

    # Create model
    model = create_model(
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        **model_kwargs,
    )

    # Create agent with middleware
    agent = create_react_agent(
        model=model,
        tools=tools or [],
        state_modifier=_build_system_message(cfg),
    )

    logger.info(
        "[AgentFactory] Created agent — model=%s, tools=%d, middleware=%d",
        model_name,
        len(tools or []),
        len(middlewares),
    )

    return agent


def _build_system_message(config: MendicantConfig) -> str:
    """Build a system message describing the Mendicant agent capabilities."""
    return (
        "You are Mendicant Bias, an advanced AI agent powered by the Mendicant "
        "intelligence middleware system. You have access to five middleware engines:\n\n"
        "1. Smart Task Router — Automatically classifies task complexity\n"
        "2. Semantic Tool Router — Selects the most relevant tools via embedding similarity\n"
        "3. Context Budget — Manages token usage with intelligent compression\n"
        "4. Verification Gate — Quality checks on code-modifying outputs\n"
        "5. Adaptive Learning — Records patterns for continuous improvement\n\n"
        "Use your tools effectively. Be precise and thorough."
    )


# ---------------------------------------------------------------------------
# Managed runtime
# ---------------------------------------------------------------------------


class MendicantRuntime:
    """
    Managed Mendicant Bias agent runtime with thread state and tool registration.

    Provides a higher-level interface than ``make_mendicant_agent`` with:
    - YAML configuration loading
    - Tool registration and management
    - Thread state persistence (JSON-backed)
    - Convenience invoke/stream with automatic thread management

    Parameters
    ----------
    config : MendicantConfig
        Mendicant middleware configuration.
    runtime_config : dict[str, Any]
        Runtime settings (model, temperature, tools, thread persistence, etc.).
    """

    def __init__(
        self,
        config: MendicantConfig,
        runtime_config: dict[str, Any] | None = None,
    ) -> None:
        self._mendicant_config = config
        self._runtime_config = runtime_config or {}

        # Model settings
        self._model_name: str = self._runtime_config.get(
            "model", "anthropic/claude-sonnet-4-20250514"
        )
        self._temperature: float = float(
            self._runtime_config.get("temperature", 0.3)
        )
        self._max_tokens: int = int(
            self._runtime_config.get("max_tokens", 8192)
        )

        # Tool registry
        self._tools: list[Any] = []

        # Thread state
        self._thread_persistence: bool = bool(
            self._runtime_config.get("thread_persistence", True)
        )
        self._thread_store_path = Path(
            self._runtime_config.get("thread_store_path", ".mendicant/threads")
        )
        self._threads: dict[str, dict[str, Any]] = {}

        # Middleware chain (built lazily)
        self._middlewares: list[Any] | None = None

        # Compiled agent (built lazily)
        self._agent: Any = None

        logger.info(
            "[MendicantRuntime] Initialized — model=%s, persistence=%s",
            self._model_name,
            self._thread_persistence,
        )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "MendicantRuntime":
        """
        Create a ``MendicantRuntime`` from a YAML config file.

        Parameters
        ----------
        path : str | Path | None
            Path to ``mendicant.yaml``.  If ``None``, the default search
            order is used (cwd, then ``~/.mendicant/``).

        Returns
        -------
        MendicantRuntime
        """
        full_config = load_config(path)
        mendicant_config = MendicantConfig.from_dict(full_config["mendicant"])
        runtime_config = full_config["runtime"]

        return cls(config=mendicant_config, runtime_config=runtime_config)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MendicantRuntime":
        """
        Create a ``MendicantRuntime`` from a plain dict.

        Parameters
        ----------
        data : dict
            Dict with optional ``"mendicant"`` and ``"runtime"`` keys.

        Returns
        -------
        MendicantRuntime
        """
        mendicant_section = data.get("mendicant", {})
        runtime_section = data.get("runtime", {})

        mendicant_config = MendicantConfig.from_dict(mendicant_section)
        return cls(config=mendicant_config, runtime_config=runtime_section)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def register_tool(self, tool: Any) -> "MendicantRuntime":
        """
        Register a LangChain tool with the runtime.

        Parameters
        ----------
        tool
            A LangChain ``BaseTool`` instance or callable decorated with
            ``@tool``.

        Returns
        -------
        MendicantRuntime
            Self, for method chaining.
        """
        self._tools.append(tool)
        self._agent = None  # invalidate cached agent
        logger.debug(
            "[MendicantRuntime] Registered tool: %s",
            getattr(tool, "name", str(tool)),
        )
        return self

    def register_tools(self, tools: list[Any]) -> "MendicantRuntime":
        """
        Register multiple tools at once.

        Parameters
        ----------
        tools : list
            List of LangChain tool instances.

        Returns
        -------
        MendicantRuntime
            Self, for method chaining.
        """
        for tool in tools:
            self.register_tool(tool)
        return self

    def list_tools(self) -> list[str]:
        """Return the names of all registered tools."""
        return [getattr(t, "name", str(t)) for t in self._tools]

    # ------------------------------------------------------------------
    # Agent access
    # ------------------------------------------------------------------

    def get_agent(self) -> Any:
        """
        Return the compiled LangGraph agent, building it if necessary.

        The agent is cached and invalidated when tools are added.

        Returns
        -------
        CompiledGraph
        """
        if self._agent is None:
            self._agent = make_mendicant_agent(
                config=self._mendicant_config,
                model_name=self._model_name,
                tools=self._tools,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        return self._agent

    @property
    def middlewares(self) -> list[Any]:
        """Return the middleware chain (built lazily)."""
        if self._middlewares is None:
            self._middlewares = _build_middleware_chain(self._mendicant_config)
        return self._middlewares

    # ------------------------------------------------------------------
    # Invoke / Stream
    # ------------------------------------------------------------------

    def invoke(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to the agent and return the full response state.

        Parameters
        ----------
        message : str
            The user message text.
        thread_id : str | None
            Thread identifier for conversation continuity.  If ``None``, a
            new thread is created.
        config : dict | None
            Additional LangGraph config to pass through.

        Returns
        -------
        dict
            The agent's response state dict.
        """
        thread_id = thread_id or self._new_thread_id()
        agent = self.get_agent()

        # Build input state
        input_state = self._build_input(message, thread_id)

        # Merge run config
        run_config = self._build_run_config(thread_id, config)

        # Invoke
        result = agent.invoke(input_state, config=run_config)

        # Persist thread state
        if self._thread_persistence:
            self._save_thread_state(thread_id, result)

        return result

    async def ainvoke(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Async version of ``invoke``.

        Parameters
        ----------
        message : str
            The user message text.
        thread_id : str | None
            Thread identifier.
        config : dict | None
            Additional LangGraph config.

        Returns
        -------
        dict
        """
        thread_id = thread_id or self._new_thread_id()
        agent = self.get_agent()

        input_state = self._build_input(message, thread_id)
        run_config = self._build_run_config(thread_id, config)

        result = await agent.ainvoke(input_state, config=run_config)

        if self._thread_persistence:
            self._save_thread_state(thread_id, result)

        return result

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Any:
        """
        Stream a message to the agent, yielding state deltas.

        Parameters
        ----------
        message : str
            The user message text.
        thread_id : str | None
            Thread identifier.
        config : dict | None
            Additional LangGraph config.

        Yields
        ------
        dict
            Incremental state updates from the agent.
        """
        thread_id = thread_id or self._new_thread_id()
        agent = self.get_agent()

        input_state = self._build_input(message, thread_id)
        run_config = self._build_run_config(thread_id, config)

        return agent.stream(input_state, config=run_config)

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        """
        Retrieve saved thread state by ID.

        Parameters
        ----------
        thread_id : str
            The thread identifier.

        Returns
        -------
        dict | None
            The thread state, or ``None`` if not found.
        """
        # Check in-memory cache first
        if thread_id in self._threads:
            return self._threads[thread_id]

        # Try loading from disk
        if self._thread_persistence:
            return self._load_thread_state(thread_id)

        return None

    def list_threads(self) -> list[str]:
        """Return all known thread IDs."""
        thread_ids = set(self._threads.keys())

        if self._thread_persistence and self._thread_store_path.exists():
            for f in self._thread_store_path.glob("*.json"):
                thread_ids.add(f.stem)

        return sorted(thread_ids)

    def delete_thread(self, thread_id: str) -> bool:
        """
        Delete a thread's saved state.

        Returns ``True`` if the thread existed and was deleted.
        """
        deleted = False

        if thread_id in self._threads:
            del self._threads[thread_id]
            deleted = True

        if self._thread_persistence:
            path = self._thread_store_path / f"{thread_id}.json"
            if path.exists():
                path.unlink()
                deleted = True

        return deleted

    # ------------------------------------------------------------------
    # Status / info
    # ------------------------------------------------------------------

    @property
    def config(self) -> MendicantConfig:
        """Return the Mendicant middleware configuration."""
        return self._mendicant_config

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model_name

    def get_status(self) -> dict[str, Any]:
        """
        Return a status dict describing the runtime state.

        Useful for health checks and debugging.
        """
        return {
            "version": "5.0.0",
            "system": "mendicant-bias",
            "model": self._model_name,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "tools": self.list_tools(),
            "tool_count": len(self._tools),
            "middleware_count": len(self.middlewares),
            "middleware": [type(m).__name__ for m in self.middlewares],
            "thread_persistence": self._thread_persistence,
            "thread_count": len(self.list_threads()),
            "config": {
                "context_budget": self._mendicant_config.context_budget.model_dump(),
                "verification": self._mendicant_config.verification.model_dump(),
                "semantic_tool_router": self._mendicant_config.semantic_tool_router.model_dump(),
                "adaptive_learning": self._mendicant_config.adaptive_learning.model_dump(),
                "smart_task_router": self._mendicant_config.smart_task_router.model_dump(),
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_input(self, message: str, thread_id: str) -> dict[str, Any]:
        """Build the agent input state dict."""
        from langchain_core.messages import HumanMessage

        # Load existing thread messages if available
        existing = self.get_thread(thread_id)
        messages = []
        if existing and "messages" in existing:
            messages = existing["messages"]

        messages.append(HumanMessage(content=message))

        return {
            "messages": messages,
            "task_start_time": time.monotonic(),
        }

    def _build_run_config(
        self, thread_id: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the LangGraph run config dict."""
        run_config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
            },
        }
        if extra:
            run_config.update(extra)
        return run_config

    def _new_thread_id(self) -> str:
        """Generate a new unique thread ID."""
        return f"thread_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Thread persistence
    # ------------------------------------------------------------------

    def _save_thread_state(self, thread_id: str, state: dict[str, Any]) -> None:
        """Save thread state to in-memory cache and optionally to disk."""
        self._threads[thread_id] = state

        if not self._thread_persistence:
            return

        self._thread_store_path.mkdir(parents=True, exist_ok=True)
        path = self._thread_store_path / f"{thread_id}.json"

        try:
            # Serialise messages — extract content and type for persistence
            serialisable = self._serialise_state(state)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(serialisable, fh, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MendicantRuntime] Failed to persist thread %s: %s",
                thread_id,
                exc,
            )

    def _load_thread_state(self, thread_id: str) -> dict[str, Any] | None:
        """Load thread state from disk."""
        path = self._thread_store_path / f"{thread_id}.json"
        if not path.exists():
            return None

        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)

            # Reconstruct messages from serialised form
            state = self._deserialise_state(data)
            self._threads[thread_id] = state
            return state
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MendicantRuntime] Failed to load thread %s: %s",
                thread_id,
                exc,
            )
            return None

    @staticmethod
    def _serialise_state(state: dict[str, Any]) -> dict[str, Any]:
        """
        Convert agent state to a JSON-serialisable dict.

        LangChain message objects are converted to dicts with ``type``
        and ``content`` keys.
        """
        from langchain_core.messages import BaseMessage

        result: dict[str, Any] = {}
        for key, value in state.items():
            if key == "messages" and isinstance(value, list):
                serialised_msgs = []
                for msg in value:
                    if isinstance(msg, BaseMessage):
                        serialised_msgs.append({
                            "type": msg.type,
                            "content": msg.content
                            if isinstance(msg.content, str)
                            else str(msg.content),
                            "additional_kwargs": {
                                k: str(v)
                                for k, v in (
                                    getattr(msg, "additional_kwargs", None) or {}
                                ).items()
                            },
                        })
                    else:
                        serialised_msgs.append(msg)
                result[key] = serialised_msgs
            else:
                try:
                    json.dumps(value, default=str)
                    result[key] = value
                except (TypeError, ValueError):
                    result[key] = str(value)
        return result

    @staticmethod
    def _deserialise_state(data: dict[str, Any]) -> dict[str, Any]:
        """
        Reconstruct agent state from a JSON dict loaded from disk.

        Message dicts are converted back to LangChain message objects.
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        _MSG_TYPE_MAP = {
            "human": HumanMessage,
            "ai": AIMessage,
            "system": SystemMessage,
        }

        result: dict[str, Any] = dict(data)

        if "messages" in data and isinstance(data["messages"], list):
            reconstructed = []
            for msg_data in data["messages"]:
                if isinstance(msg_data, dict) and "type" in msg_data:
                    msg_cls = _MSG_TYPE_MAP.get(msg_data["type"])
                    if msg_cls:
                        reconstructed.append(
                            msg_cls(content=msg_data.get("content", ""))
                        )
                    else:
                        reconstructed.append(
                            HumanMessage(content=msg_data.get("content", ""))
                        )
                else:
                    reconstructed.append(msg_data)
            result["messages"] = reconstructed

        return result
