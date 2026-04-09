"""
mendicant_runtime.agent
=======================
Mendicant Bias V5 — Agent Factory & Managed Runtime Engine

A real execution engine modeled after DeerFlow's ``make_lead_agent`` depth:

- Sandbox lifecycle (acquire on invoke, persist across turns)
- Memory injection into every system prompt
- Named agent profile loading with tool filtering
- Subagent spawning via ``SubagentExecutor``
- Full ``MendicantThreadState`` with verification, classification, artifacts
- Proper middleware chain wired into before_agent/before_model/after_agent hooks
- Real async streaming with event yielding

Provides two levels of abstraction:

``make_mendicant_agent``
    One-call factory that returns a compiled LangGraph agent (simple cases).

``MendicantRuntime``
    Full managed execution engine with sandbox, memory, subagents, agent
    profiles, middleware hooks, and thread state persistence.

Usage::

    # Quick — one-call factory
    from mendicant_runtime import make_mendicant_agent
    agent = make_mendicant_agent(model_name="claude-sonnet-4-20250514")

    # Full — managed runtime
    from mendicant_runtime import MendicantRuntime
    runtime = MendicantRuntime.from_yaml("mendicant.yaml")
    runtime.set_agent("the_didact")
    result = runtime.invoke("Research quantum error correction")

    # Subagent spawning
    task_id = runtime.spawn_subagent("Summarize findings", "the_scribe")
    result = runtime.wait_for_subagent(task_id, timeout=120)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from mendicant_core import MendicantConfig
from mendicant_core.agents import AgentLoader, AgentProfile
from mendicant_core.memory import MemoryInjector, MemoryStore
from mendicant_core.middleware import (
    AdaptiveLearningMiddleware,
    ContextBudgetMiddleware,
    SemanticToolRouterMiddleware,
    SmartTaskRouterMiddleware,
    VerificationMiddleware,
)

from mendicant_core.sandbox.tools import create_sandbox_tools

from mendicant_runtime.config import load_config
from mendicant_runtime.models import create_model
from mendicant_runtime.sandbox import LocalSandbox, LocalSandboxProvider
from mendicant_runtime.subagents import SubagentConfig, SubagentExecutor, SubagentTask
from mendicant_runtime.thread_state import MendicantThreadState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sandbox tool proxy — swappable sandbox reference for per-thread binding
# ---------------------------------------------------------------------------


class _SandboxProxy:
    """
    A thin proxy around a ``Sandbox`` that can be swapped per-invocation.

    The ``MendicantRuntime`` keeps one set of LangChain sandbox tools across
    all threads, but each thread has its own ``LocalSandbox``.  This proxy
    lets us create the tools once and bind to the correct sandbox before
    each invoke.
    """

    def __init__(self) -> None:
        self._sandbox: Any = None

    def bind(self, sandbox: Any) -> None:
        """Bind to a sandbox instance for the current invocation."""
        self._sandbox = sandbox

    def execute_command(self, command: str) -> str:
        if self._sandbox is None:
            return "ERROR: No sandbox bound"
        return self._sandbox.execute_command(command)

    def read_file(self, path: str) -> str:
        if self._sandbox is None:
            raise FileNotFoundError("No sandbox bound")
        return self._sandbox.read_file(path)

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        if self._sandbox is None:
            raise PermissionError("No sandbox bound")
        return self._sandbox.write_file(path, content, append=append)

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        if self._sandbox is None:
            raise NotADirectoryError("No sandbox bound")
        return self._sandbox.list_dir(path, max_depth=max_depth)


# ---------------------------------------------------------------------------
# Middleware chain builder
# ---------------------------------------------------------------------------


def _build_middleware_chain(
    config: MendicantConfig,
) -> list[Any]:
    """
    Build the ordered middleware stack from a MendicantConfig.

    Middleware order:
      1. SmartTaskRouter (before_agent) -- classifies task type and sets flags
      2. SemanticToolRouter (before_agent) -- selects relevant tools
      3. ContextBudget (before_model) -- enforces token budget
      4. Verification (after_agent) -- quality gate (if enabled)
      5. AdaptiveLearning (after_agent) -- records patterns

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

    # FR5: Smart Task Router -- before_agent
    middlewares.append(config.build_smart_task_router_middleware())
    logger.debug("[MiddlewareChain] Added SmartTaskRouter")

    # FR1: Semantic Tool Router -- before_agent
    middlewares.append(config.build_semantic_tool_router_middleware())
    logger.debug("[MiddlewareChain] Added SemanticToolRouter")

    # FR4: Context Budget -- before_model
    middlewares.append(config.build_context_budget_middleware())
    logger.debug("[MiddlewareChain] Added ContextBudget")

    # FR2: Verification Gate -- after_agent (conditional)
    if config.verification.enabled:
        middlewares.append(config.build_verification_middleware())
        logger.debug("[MiddlewareChain] Added Verification")
    else:
        logger.debug("[MiddlewareChain] Verification disabled -- skipped")

    # FR3: Adaptive Learning -- after_agent
    middlewares.append(config.build_adaptive_learning_middleware())
    logger.debug("[MiddlewareChain] Added AdaptiveLearning")

    logger.info(
        "[MiddlewareChain] Built %d middleware: %s",
        len(middlewares),
        [type(m).__name__ for m in middlewares],
    )

    return middlewares


def _categorize_middlewares(
    middlewares: list[Any],
) -> dict[str, list[Any]]:
    """
    Categorize middleware instances by their execution phase.

    Returns a dict with keys ``"before_agent"``, ``"before_model"``,
    ``"after_agent"`` — each mapping to the ordered list of middlewares
    for that phase.
    """
    before_agent: list[Any] = []
    before_model: list[Any] = []
    after_agent: list[Any] = []

    for mw in middlewares:
        mw_type = type(mw).__name__

        if isinstance(mw, (SmartTaskRouterMiddleware, SemanticToolRouterMiddleware)):
            before_agent.append(mw)
        elif isinstance(mw, ContextBudgetMiddleware):
            before_model.append(mw)
        elif isinstance(mw, (VerificationMiddleware, AdaptiveLearningMiddleware)):
            after_agent.append(mw)
        else:
            # Default: treat unknown middleware as before_agent
            logger.debug(
                "[MiddlewareChain] Unknown middleware %s — defaulting to before_agent",
                mw_type,
            )
            before_agent.append(mw)

    return {
        "before_agent": before_agent,
        "before_model": before_model,
        "after_agent": after_agent,
    }


# ---------------------------------------------------------------------------
# Middleware → LangGraph hook adapters
# ---------------------------------------------------------------------------


class _NullRuntime:
    """
    Lightweight stand-in for ``langgraph.runtime.Runtime`` when middleware
    runs inside a LangGraph ``pre_model_hook`` / ``post_model_hook`` (where
    no real ``Runtime`` object is provided).

    All five Mendicant middleware methods access ``runtime.context`` to obtain
    a dict with optional keys like ``thread_id``, ``verification_enabled``,
    ``thread_budgets``, etc.  This class provides a ``context`` dict that
    can be populated from the graph state.
    """

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context: dict[str, Any] = context or {}


def _run_middleware_list(
    middlewares: list[Any],
    state: dict[str, Any],
    hook_name: str,
) -> dict[str, Any]:
    """
    Execute a list of middleware instances against *state*, returning a merged
    state-update dict.

    Each middleware is probed for *hook_name* (e.g. ``"before_agent"``,
    ``"before_model"``, ``"after_agent"``), then falls back to ``"process"``.
    A lightweight ``_NullRuntime`` is passed as the second argument to
    satisfy the ``(state, runtime)`` signature that middleware methods expect.
    """
    # Build a runtime context from state fields that middleware might need
    runtime = _NullRuntime(context={
        "thread_id": state.get("thread_id"),
        "verification_enabled": state.get("verification_enabled", True),
    })

    delta: dict[str, Any] = {}
    for mw in middlewares:
        fn = getattr(mw, hook_name, None) or getattr(mw, "process", None)
        if fn is None:
            continue
        try:
            result = fn(state, runtime)
            if result is not None:
                delta.update(result)
                # Also merge into the state snapshot so downstream middleware
                # sees earlier middleware outputs (e.g. task_type from
                # SmartTaskRouter is visible to SemanticToolRouter).
                state = {**state, **result}
        except TypeError:
            # Some middleware may only accept (state) — try without runtime
            try:
                result = fn(state)
                if result is not None:
                    delta.update(result)
                    state = {**state, **result}
            except Exception as exc:
                logger.warning(
                    "[MiddlewareHook] %s.%s failed: %s",
                    type(mw).__name__,
                    hook_name,
                    exc,
                )
        except Exception as exc:
            logger.warning(
                "[MiddlewareHook] %s.%s failed: %s",
                type(mw).__name__,
                hook_name,
                exc,
            )
    return delta


def _make_pre_model_hook(
    middlewares: list[Any],
):
    """
    Return a callable suitable for ``create_react_agent(pre_model_hook=...)``.

    The hook receives graph state and must return a dict containing at least
    ``"messages"`` (to propagate the conversation to the LLM).  Middleware
    state updates (``task_type``, ``selected_tools``, ``context_budget_usage``,
    etc.) are merged in.
    """

    def pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        # Run before_agent middleware (SmartTaskRouter, SemanticToolRouter)
        before_agent_delta = _run_middleware_list(
            [m for m in middlewares if isinstance(m, (SmartTaskRouterMiddleware, SemanticToolRouterMiddleware))],
            state,
            "before_agent",
        )

        # Merge into state for before_model middleware
        merged_state = {**state, **before_agent_delta}

        # Run before_model middleware (ContextBudget)
        before_model_delta = _run_middleware_list(
            [m for m in middlewares if isinstance(m, ContextBudgetMiddleware)],
            merged_state,
            "before_model",
        )

        # Build the return dict — must include messages for the LLM
        result: dict[str, Any] = {}
        result.update(before_agent_delta)
        result.update(before_model_delta)

        # Ensure messages are propagated (ContextBudget may have compressed them)
        if "messages" not in result:
            result["messages"] = state.get("messages", [])

        return result

    return pre_model_hook


def _make_post_model_hook(
    middlewares: list[Any],
):
    """
    Return a callable suitable for ``create_react_agent(post_model_hook=...)``.

    The hook receives graph state after the LLM call and returns a
    state-update dict with verification results, learning metadata, etc.
    """

    def post_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        delta = _run_middleware_list(middlewares, state, "after_agent")
        return delta

    return post_model_hook


# ---------------------------------------------------------------------------
# Default system prompt
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are Mendicant Bias, an advanced AI agent powered by the Mendicant "
    "intelligence middleware system. You have access to five middleware engines:\n\n"
    "1. Smart Task Router -- Automatically classifies task complexity\n"
    "2. Semantic Tool Router -- Selects the most relevant tools via embedding similarity\n"
    "3. Context Budget -- Manages token usage with intelligent compression\n"
    "4. Verification Gate -- Quality checks on code-modifying outputs\n"
    "5. Adaptive Learning -- Records patterns for continuous improvement\n\n"
    "Use your tools effectively. Be precise and thorough."
)

_MIDDLEWARE_DESCRIPTION = (
    "\n---\n"
    "Active middleware capabilities:\n"
    "- SmartTaskRouter: Task classification and complexity routing\n"
    "- SemanticToolRouter: Embedding-based tool selection\n"
    "- ContextBudget: Token budget enforcement with compression strategies\n"
    "- VerificationGate: Blind pre-analysis quality gate on code outputs\n"
    "- AdaptiveLearning: Pattern recording for continuous improvement\n"
)


# ---------------------------------------------------------------------------
# One-call agent factory
# ---------------------------------------------------------------------------


def make_mendicant_agent(
    config: MendicantConfig | None = None,
    model_name: str = "claude-sonnet-4-20250514",
    tools: list | None = None,
    system_prompt: str | None = None,
    state_schema: type | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    **model_kwargs: Any,
) -> Any:
    """
    Create a LangGraph react agent with the full Mendicant middleware chain.

    This is the simplest way to get a Mendicant-powered agent running.
    For more control over sandbox, memory, subagents, and agent profiles,
    use ``MendicantRuntime`` instead.

    Parameters
    ----------
    config : MendicantConfig | None
        Mendicant middleware configuration.  If ``None``, defaults are used.
    model_name : str
        LLM model name (supports ``anthropic/``, ``openai/`` prefixes or
        bare names with auto-detection).
    tools : list | None
        List of LangChain tools to bind to the agent.
    system_prompt : str | None
        Custom system prompt.  If ``None``, the default Mendicant prompt is used.
    state_schema : type | None
        Custom state schema.  If ``None``, ``MendicantThreadState`` is used.
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
    phases = _categorize_middlewares(middlewares)

    # Create model
    model = create_model(
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        **model_kwargs,
    )

    # Use provided system prompt or default
    prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    # ---------------------------------------------------------------
    # Build pre_model_hook / post_model_hook from middleware chain
    # ---------------------------------------------------------------
    # pre_model_hook runs before each LLM call.  We merge:
    #   - before_agent  middleware (SmartTaskRouter, SemanticToolRouter)
    #   - before_model  middleware (ContextBudget)
    # post_model_hook runs after each LLM call.  We merge:
    #   - after_agent   middleware (Verification, AdaptiveLearning)
    #
    # The hook receives graph state and must return a state-update dict.
    # At minimum, pre_model_hook must include "messages" or
    # "llm_input_messages" so LangGraph knows what to feed the model.
    # ---------------------------------------------------------------

    pre_mw = phases.get("before_agent", []) + phases.get("before_model", [])
    post_mw = phases.get("after_agent", [])

    pre_hook = _make_pre_model_hook(pre_mw) if pre_mw else None
    post_hook = _make_post_model_hook(post_mw) if post_mw else None

    # Create agent with full state schema and middleware hooks
    agent = create_react_agent(
        model=model,
        tools=tools or [],
        prompt=prompt,
        state_schema=state_schema or MendicantThreadState,
        pre_model_hook=pre_hook,
        post_model_hook=post_hook,
    )

    logger.info(
        "[AgentFactory] Created agent -- model=%s, tools=%d, middleware=%d, "
        "pre_hook=%s, post_hook=%s, state=%s",
        model_name,
        len(tools or []),
        len(middlewares),
        [type(m).__name__ for m in pre_mw] if pre_mw else "(none)",
        [type(m).__name__ for m in post_mw] if post_mw else "(none)",
        (state_schema or MendicantThreadState).__name__,
    )

    return agent


# ---------------------------------------------------------------------------
# Managed runtime engine
# ---------------------------------------------------------------------------


class MendicantRuntime:
    """
    Mendicant Bias managed execution engine.

    A real runtime that manages the full agent lifecycle:
    - Sandbox acquisition and release (per-thread isolated workspaces)
    - Memory loading, injection into system prompts, and persistence
    - Named agent profiles with tool filtering and custom system prompts
    - Subagent spawning and parallel execution
    - Full MendicantThreadState with verification, classification, artifacts
    - Middleware chain wired into proper before_agent/before_model/after_agent hooks
    - Thread state persistence (JSON-backed)
    - Sync and async invoke/stream

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
        self._base_tools: list[Any] = []  # Unfiltered tools (before agent profile filtering)

        # Sandbox tool proxy — shared across threads, rebound per-invoke
        self._sandbox_proxy = _SandboxProxy()
        sandbox_tools = create_sandbox_tools(self._sandbox_proxy)
        self._sandbox_tools = sandbox_tools
        self._tools.extend(sandbox_tools)
        self._base_tools.extend(sandbox_tools)

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
        self._middleware_phases: dict[str, list[Any]] | None = None

        # Compiled agent (built lazily, invalidated on config changes)
        self._agent: Any = None
        self._current_system_prompt: str | None = None

        # ---------------------------------------------------------------
        # NEW: Sandbox provider — per-thread isolated workspaces
        # ---------------------------------------------------------------
        self._sandbox_provider = LocalSandboxProvider(
            base_dir=str(self._thread_store_path)
        )

        # ---------------------------------------------------------------
        # NEW: Subagent executor — parallel named-agent execution
        # ---------------------------------------------------------------
        self._subagent_executor = SubagentExecutor(
            max_workers=int(self._runtime_config.get("max_concurrent_subagents", 3))
        )

        # ---------------------------------------------------------------
        # NEW: Memory store + injector
        # ---------------------------------------------------------------
        memory_path = self._runtime_config.get("memory_path", ".mendicant/memory.json")
        self._memory_store = MemoryStore(memory_path)
        self._memory_injector = MemoryInjector(
            max_tokens=int(self._runtime_config.get("memory_injection_tokens", 2000))
        )

        # ---------------------------------------------------------------
        # NEW: Agent profiles
        # ---------------------------------------------------------------
        self._agent_name: str | None = None
        self._agent_profile: AgentProfile | None = None
        self._agent_loader: AgentLoader | None = None

        # Initialize agent loader if profiles dir is configured
        profiles_dir = self._runtime_config.get("agents_profiles_dir")
        mapping_path = self._runtime_config.get("agent_mapping_path")
        if profiles_dir:
            self._agent_loader = AgentLoader(
                profiles_dir=profiles_dir,
                mapping_path=mapping_path,
            )

        logger.info(
            "[MendicantRuntime] Initialized -- model=%s, persistence=%s, "
            "sandbox=%s, memory=%s, agents=%s",
            self._model_name,
            self._thread_persistence,
            self._sandbox_provider._base_dir,
            memory_path,
            profiles_dir or "(none)",
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
    # Agent profile management
    # ------------------------------------------------------------------

    def set_agent(self, agent_name: str) -> "MendicantRuntime":
        """
        Load a named agent profile and configure the runtime for it.

        Sets the system prompt from the agent's markdown content, filters
        tools based on the agent's declared tool list, and invalidates
        the cached compiled agent so it is rebuilt on next invoke.

        Parameters
        ----------
        agent_name : str
            Agent profile name (e.g. ``"the_didact"``, ``"hollowed_eyes"``).

        Returns
        -------
        MendicantRuntime
            Self, for method chaining.

        Raises
        ------
        ValueError
            If no agent loader is configured or the agent name is not found.
        """
        if self._agent_loader is None:
            raise ValueError(
                "Cannot set agent: no agents_profiles_dir configured in runtime config. "
                "Add 'agents_profiles_dir' to your mendicant.yaml runtime section."
            )

        profile = self._agent_loader.get_profile(agent_name)
        if profile is None:
            available = self._agent_loader.list_agents()
            raise ValueError(
                f"Agent '{agent_name}' not found. Available agents: {available}"
            )

        self._agent_name = agent_name
        self._agent_profile = profile
        self._agent = None  # Invalidate cached agent
        self._current_system_prompt = None  # Will be rebuilt

        # Filter tools if the agent declares an allowed list
        if profile.tools:
            self._apply_tool_filter(profile.tools)

        logger.info(
            "[MendicantRuntime] Set agent: %s (model=%s, tools=%s, domains=%s)",
            agent_name,
            profile.model,
            profile.tools or "(all)",
            profile.domains,
        )
        return self

    def clear_agent(self) -> "MendicantRuntime":
        """Clear the active agent profile, reverting to the default Mendicant identity."""
        self._agent_name = None
        self._agent_profile = None
        self._agent = None
        self._current_system_prompt = None
        # Restore unfiltered tools
        if self._base_tools:
            self._tools = list(self._base_tools)
        logger.info("[MendicantRuntime] Cleared agent profile — using default identity")
        return self

    @property
    def agent_name(self) -> str | None:
        """Return the name of the active agent profile, or ``None``."""
        return self._agent_name

    @property
    def agent_profile(self) -> AgentProfile | None:
        """Return the active agent profile, or ``None``."""
        return self._agent_profile

    def list_agents(self) -> list[str]:
        """Return all available agent profile names."""
        if self._agent_loader is None:
            return []
        return self._agent_loader.list_agents()

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
        self._base_tools.append(tool)
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
        """Return the names of all registered tools (after any agent filtering)."""
        return [getattr(t, "name", str(t)) for t in self._tools]

    # ------------------------------------------------------------------
    # Agent access
    # ------------------------------------------------------------------

    def get_agent(self) -> Any:
        """
        Return the compiled LangGraph agent, building it if necessary.

        The agent is cached and invalidated when tools are added or the
        agent profile changes.

        Returns
        -------
        CompiledGraph
        """
        if self._agent is None:
            system_prompt = self._build_full_system_prompt()
            self._current_system_prompt = system_prompt
            self._agent = make_mendicant_agent(
                config=self._mendicant_config,
                model_name=self._effective_model_name,
                tools=self._tools,
                system_prompt=system_prompt,
                state_schema=MendicantThreadState,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        return self._agent

    @property
    def middlewares(self) -> list[Any]:
        """Return the middleware chain (built lazily)."""
        if self._middlewares is None:
            self._middlewares = _build_middleware_chain(self._mendicant_config)
            self._middleware_phases = _categorize_middlewares(self._middlewares)
        return self._middlewares

    @property
    def middleware_phases(self) -> dict[str, list[Any]]:
        """Return middleware categorized by execution phase."""
        if self._middleware_phases is None:
            _ = self.middlewares  # triggers build
        return self._middleware_phases  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Invoke — full execution with sandbox, memory, middleware hooks
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

        Full execution lifecycle:
        1. Acquire sandbox for the thread
        2. Load and inject memory into system prompt
        3. Run before_agent middleware hooks
        4. Execute the LangGraph agent
        5. Run after_agent middleware hooks
        6. Persist thread state

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
            The agent's response state dict (MendicantThreadState).
        """
        thread_id = thread_id or self._new_thread_id()

        # --- Sandbox lifecycle ---
        sandbox = self._sandbox_provider.acquire(thread_id)
        self._sandbox_proxy.bind(sandbox)

        # --- Memory injection ---
        memory_data = self._memory_store.load()
        memory_context = self._memory_injector.format_for_injection(memory_data)

        # Rebuild system prompt with fresh memory context
        system_prompt = self._build_full_system_prompt(memory_context=memory_context)
        if system_prompt != self._current_system_prompt:
            self._current_system_prompt = system_prompt
            self._agent = None  # Invalidate — prompt changed

        agent = self.get_agent()

        # --- Build input state with full schema ---
        input_state = self._build_input(message, thread_id, sandbox)

        # --- Run before_agent hooks ---
        input_state = self._run_before_agent_hooks(input_state)

        # --- Merge run config ---
        run_config = self._build_run_config(thread_id, config)

        # --- Invoke ---
        result = agent.invoke(input_state, config=run_config)

        # --- Run after_agent hooks ---
        result = self._run_after_agent_hooks(result)

        # --- Persist thread state ---
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
        Async version of ``invoke`` with the same full lifecycle.

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

        # Sandbox
        sandbox = self._sandbox_provider.acquire(thread_id)
        self._sandbox_proxy.bind(sandbox)

        # Memory
        memory_data = self._memory_store.load()
        memory_context = self._memory_injector.format_for_injection(memory_data)

        system_prompt = self._build_full_system_prompt(memory_context=memory_context)
        if system_prompt != self._current_system_prompt:
            self._current_system_prompt = system_prompt
            self._agent = None

        agent = self.get_agent()

        input_state = self._build_input(message, thread_id, sandbox)
        input_state = self._run_before_agent_hooks(input_state)
        run_config = self._build_run_config(thread_id, config)

        result = await agent.ainvoke(input_state, config=run_config)

        result = self._run_after_agent_hooks(result)

        if self._thread_persistence:
            self._save_thread_state(thread_id, result)

        return result

    # ------------------------------------------------------------------
    # Stream — real async streaming with event yielding
    # ------------------------------------------------------------------

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        Stream a message to the agent, yielding state deltas.

        Performs the same sandbox/memory/middleware lifecycle as ``invoke``,
        but yields incremental state updates.

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

        # Lifecycle setup
        sandbox = self._sandbox_provider.acquire(thread_id)
        self._sandbox_proxy.bind(sandbox)
        memory_data = self._memory_store.load()
        memory_context = self._memory_injector.format_for_injection(memory_data)

        system_prompt = self._build_full_system_prompt(memory_context=memory_context)
        if system_prompt != self._current_system_prompt:
            self._current_system_prompt = system_prompt
            self._agent = None

        agent = self.get_agent()

        input_state = self._build_input(message, thread_id, sandbox)
        input_state = self._run_before_agent_hooks(input_state)
        run_config = self._build_run_config(thread_id, config)

        # Yield stream events
        last_state = None
        for event in agent.stream(input_state, config=run_config):
            last_state = event
            yield event

        # After-agent hooks on the final state
        if last_state is not None:
            self._run_after_agent_hooks(last_state)

            if self._thread_persistence:
                self._save_thread_state(thread_id, last_state)

    async def astream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Async stream with the full execution lifecycle.

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

        # Lifecycle setup
        sandbox = self._sandbox_provider.acquire(thread_id)
        self._sandbox_proxy.bind(sandbox)
        memory_data = self._memory_store.load()
        memory_context = self._memory_injector.format_for_injection(memory_data)

        system_prompt = self._build_full_system_prompt(memory_context=memory_context)
        if system_prompt != self._current_system_prompt:
            self._current_system_prompt = system_prompt
            self._agent = None

        agent = self.get_agent()

        input_state = self._build_input(message, thread_id, sandbox)
        input_state = self._run_before_agent_hooks(input_state)
        run_config = self._build_run_config(thread_id, config)

        last_state = None
        async for event in agent.astream(input_state, config=run_config):
            last_state = event
            yield event

        if last_state is not None:
            self._run_after_agent_hooks(last_state)

            if self._thread_persistence:
                self._save_thread_state(thread_id, last_state)

    # ------------------------------------------------------------------
    # Subagent spawning
    # ------------------------------------------------------------------

    def spawn_subagent(
        self,
        task: str,
        agent_name: str,
        *,
        model: str | None = None,
        max_turns: int = 10,
        timeout_seconds: float = 900.0,
    ) -> str:
        """
        Spawn a named agent as a subagent running in parallel.

        Parameters
        ----------
        task : str
            The task description / prompt for the subagent.
        agent_name : str
            Agent profile name (must be loadable by the AgentLoader).
        model : str | None
            Override model.  If ``None``, inherits from agent profile or
            falls back to the runtime's model.
        max_turns : int
            Maximum turns for the subagent.
        timeout_seconds : float
            Maximum execution time.

        Returns
        -------
        str
            Task ID for polling the result.
        """
        # Load agent profile for system prompt
        system_prompt = ""
        resolved_model = model or self._model_name

        if self._agent_loader:
            profile = self._agent_loader.get_profile(agent_name)
            if profile:
                system_prompt = profile.content
                if profile.model and profile.model != "sonnet" and not model:
                    resolved_model = profile.model

        sub_config = SubagentConfig(
            name=agent_name,
            system_prompt=system_prompt,
            model=resolved_model,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )

        return self._subagent_executor.submit(
            prompt=task,
            config=sub_config,
            agent_factory=self._create_subagent,
        )

    def wait_for_subagent(self, task_id: str, timeout: float | None = None) -> Any:
        """
        Block until a spawned subagent completes and return its result.

        Parameters
        ----------
        task_id : str
            Task ID returned by ``spawn_subagent``.
        timeout : float | None
            Maximum seconds to wait.

        Returns
        -------
        Any
            The subagent's response.
        """
        return self._subagent_executor.wait_for_result(task_id, timeout=timeout)

    def get_subagent_status(self, task_id: str) -> SubagentTask | None:
        """Return the status of a spawned subagent task."""
        return self._subagent_executor.get_status(task_id)

    def list_active_subagents(self) -> list[SubagentTask]:
        """Return all pending/running subagent tasks."""
        return self._subagent_executor.list_active()

    # ------------------------------------------------------------------
    # Memory access
    # ------------------------------------------------------------------

    @property
    def memory_store(self) -> MemoryStore:
        """Return the memory store for direct fact management."""
        return self._memory_store

    @property
    def memory_injector(self) -> MemoryInjector:
        """Return the memory injector."""
        return self._memory_injector

    def reload_memory(self) -> None:
        """Force-reload memory from disk (clears cache)."""
        self._memory_store._data = None
        self._memory_store.load()
        # Invalidate agent so the system prompt is rebuilt with fresh memory
        self._agent = None
        self._current_system_prompt = None
        logger.info("[MendicantRuntime] Memory reloaded — agent will be rebuilt")

    # ------------------------------------------------------------------
    # Sandbox access
    # ------------------------------------------------------------------

    @property
    def sandbox_provider(self) -> LocalSandboxProvider:
        """Return the sandbox provider for direct sandbox management."""
        return self._sandbox_provider

    def get_sandbox(self, thread_id: str) -> LocalSandbox | None:
        """Return the sandbox for a given thread, or ``None``."""
        return self._sandbox_provider.get(thread_id)

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
        Delete a thread's saved state and release its sandbox.

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

        # Release sandbox (without cleanup — user might want the files)
        self._sandbox_provider.release(thread_id, cleanup=False)

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
        Return a comprehensive status dict describing the runtime state.

        Useful for health checks and debugging.
        """
        return {
            "version": "5.0.0",
            "system": "mendicant-bias",
            "model": self._model_name,
            "effective_model": self._effective_model_name,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "tools": self.list_tools(),
            "tool_count": len(self._tools),
            "middleware_count": len(self.middlewares),
            "middleware": [type(m).__name__ for m in self.middlewares],
            "middleware_phases": {
                phase: [type(m).__name__ for m in mws]
                for phase, mws in self.middleware_phases.items()
            },
            "thread_persistence": self._thread_persistence,
            "thread_count": len(self.list_threads()),
            "active_sandboxes": len(self._sandbox_provider.list_active()),
            "agent_name": self._agent_name,
            "agent_profile": self._agent_profile.name if self._agent_profile else None,
            "available_agents": self.list_agents(),
            "memory": self._memory_store.get_stats(),
            "subagents": self._subagent_executor.get_stats(),
            "config": {
                "context_budget": self._mendicant_config.context_budget.model_dump(),
                "verification": self._mendicant_config.verification.model_dump(),
                "semantic_tool_router": self._mendicant_config.semantic_tool_router.model_dump(),
                "adaptive_learning": self._mendicant_config.adaptive_learning.model_dump(),
                "smart_task_router": self._mendicant_config.smart_task_router.model_dump(),
            },
        }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self, *, cleanup_sandboxes: bool = False) -> None:
        """
        Gracefully shut down the runtime.

        Parameters
        ----------
        cleanup_sandboxes : bool
            If ``True``, remove all sandbox directories.
        """
        self._subagent_executor.shutdown(wait=True)

        if cleanup_sandboxes:
            self._sandbox_provider.cleanup_all()

        logger.info("[MendicantRuntime] Shut down (cleanup_sandboxes=%s)", cleanup_sandboxes)

    # ------------------------------------------------------------------
    # Internal: system prompt builder
    # ------------------------------------------------------------------

    @property
    def _effective_model_name(self) -> str:
        """Resolve the effective model name (agent profile override or default)."""
        if self._agent_profile and self._agent_profile.model and self._agent_profile.model != "sonnet":
            return self._agent_profile.model
        return self._model_name

    def _build_full_system_prompt(self, memory_context: str = "") -> str:
        """
        Build the complete system prompt with agent identity + memory + middleware.

        Priority order:
        1. Agent profile content (if active)  OR  default Mendicant prompt
        2. Memory context (if available)
        3. Middleware capability description

        Parameters
        ----------
        memory_context : str
            Pre-formatted memory injection string from MemoryInjector.

        Returns
        -------
        str
        """
        parts: list[str] = []

        # 1. Agent identity or default
        if self._agent_profile and self._agent_profile.content:
            parts.append(self._agent_profile.content)
        else:
            parts.append(_DEFAULT_SYSTEM_PROMPT)

        # 2. Memory injection
        if memory_context:
            parts.append(memory_context)

        # 3. Middleware description
        parts.append(_MIDDLEWARE_DESCRIPTION)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal: input / config builders
    # ------------------------------------------------------------------

    def _build_input(
        self,
        message: str,
        thread_id: str,
        sandbox: LocalSandbox | None = None,
    ) -> dict[str, Any]:
        """Build the agent input state dict with full MendicantThreadState fields."""
        # Load existing thread messages if available
        existing = self.get_thread(thread_id)
        messages: list[BaseMessage] = []
        if existing and "messages" in existing:
            messages = existing["messages"]

        messages.append(HumanMessage(content=message))

        # Compute turn count
        turn_count = 0
        if existing and "turn_count" in existing:
            turn_count = existing["turn_count"]
        turn_count += 1

        input_state: dict[str, Any] = {
            "messages": messages,
            "task_start_time": time.monotonic(),
            "memory_injected": True,
            "agent_name": self._agent_name,
            "turn_count": turn_count,
        }

        # Sandbox state
        if sandbox is not None:
            input_state["sandbox"] = {"sandbox_id": sandbox.id}
            input_state["thread_data"] = sandbox.to_thread_data()

        return input_state

    def _build_run_config(
        self, thread_id: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the LangGraph run config dict with metadata."""
        run_config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
            },
            "metadata": {
                "agent_name": self._agent_name or "mendicant_bias",
                "model_name": self._effective_model_name,
            },
        }
        if extra:
            # Deep-merge configurable
            if "configurable" in extra:
                run_config["configurable"].update(extra.pop("configurable"))
            if "metadata" in extra:
                run_config["metadata"].update(extra.pop("metadata"))
            run_config.update(extra)
        return run_config

    def _new_thread_id(self) -> str:
        """Generate a new unique thread ID."""
        return f"thread_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Internal: middleware hooks
    # ------------------------------------------------------------------

    def _run_before_agent_hooks(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Execute before_agent middleware on the input state.

        Each middleware's ``before_agent`` method (if it exists) receives
        and returns the state dict.
        """
        for mw in self.middleware_phases.get("before_agent", []):
            hook = getattr(mw, "before_agent", None) or getattr(mw, "process", None)
            if hook is not None:
                try:
                    result = hook(state)
                    if result is not None:
                        state = result
                except Exception as exc:
                    logger.warning(
                        "[MendicantRuntime] before_agent hook %s failed: %s",
                        type(mw).__name__,
                        exc,
                    )
        return state

    def _run_before_model_hooks(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute before_model middleware on the state."""
        for mw in self.middleware_phases.get("before_model", []):
            hook = getattr(mw, "before_model", None) or getattr(mw, "process", None)
            if hook is not None:
                try:
                    result = hook(state)
                    if result is not None:
                        state = result
                except Exception as exc:
                    logger.warning(
                        "[MendicantRuntime] before_model hook %s failed: %s",
                        type(mw).__name__,
                        exc,
                    )
        return state

    def _run_after_agent_hooks(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Execute after_agent middleware on the response state.

        This is where Verification and AdaptiveLearning run.
        """
        for mw in self.middleware_phases.get("after_agent", []):
            hook = getattr(mw, "after_agent", None) or getattr(mw, "process", None)
            if hook is not None:
                try:
                    result = hook(state)
                    if result is not None:
                        state = result
                except Exception as exc:
                    logger.warning(
                        "[MendicantRuntime] after_agent hook %s failed: %s",
                        type(mw).__name__,
                        exc,
                    )
        return state

    # ------------------------------------------------------------------
    # Internal: tool filtering
    # ------------------------------------------------------------------

    def _apply_tool_filter(self, allowed_tools: list[str]) -> None:
        """
        Filter the tool registry to only include tools in the agent's
        allowed list.

        The unfiltered set is preserved in ``_base_tools`` so it can be
        restored when the agent is cleared.
        """
        if not allowed_tools:
            return

        allowed_set = set(allowed_tools)
        self._tools = [
            t for t in self._tools
            if getattr(t, "name", str(t)) in allowed_set
        ]
        logger.debug(
            "[MendicantRuntime] Filtered tools to %d (allowed: %s)",
            len(self._tools),
            allowed_tools,
        )

    # ------------------------------------------------------------------
    # Internal: subagent factory
    # ------------------------------------------------------------------

    def _create_subagent(self, config: SubagentConfig) -> Any:
        """Factory for creating subagent instances."""
        model_name = config.model if config.model != "inherit" else self._model_name
        return make_mendicant_agent(
            config=self._mendicant_config,
            model_name=model_name,
            tools=self._base_tools or self._tools,
            system_prompt=config.system_prompt or None,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

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
            # Serialise messages -- extract content and type for persistence
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
        and ``content`` keys.  MendicantThreadState fields (sandbox,
        thread_data, verification, etc.) are preserved.
        """
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
