"""
Mendicant Bias V5 — Agent Runtime Engine
=========================================

A real execution engine for LangGraph agents with Mendicant intelligence
middleware wired as first-class citizens.

This package provides:

- ``make_mendicant_agent`` -- One-call agent factory with full middleware chain
- ``MendicantRuntime`` -- Managed execution engine with sandbox, memory,
  subagents, agent profiles, and middleware hooks
- ``MendicantThreadState`` -- Full thread state schema (sandbox, verification,
  classification, artifacts, subagent results)
- ``LocalSandboxProvider`` / ``LocalSandbox`` -- Per-thread isolated workspaces
- ``SubagentExecutor`` / ``SubagentConfig`` -- Parallel named-agent execution
- ``load_config`` -- YAML configuration loader
- ``create_model`` -- Multi-provider LLM factory

The middleware chain fires in this order:

  before_agent:  SmartTaskRouter -> SemanticToolRouter
  before_model:  ContextBudget
  after_agent:   Verification -> AdaptiveLearning

Quick Start::

    from mendicant_runtime import make_mendicant_agent

    agent = make_mendicant_agent(model_name="claude-sonnet-4-20250514")
    result = agent.invoke({"messages": [("user", "Hello")]})

Full Runtime::

    from mendicant_runtime import MendicantRuntime

    runtime = MendicantRuntime.from_yaml("mendicant.yaml")
    runtime.set_agent("the_didact")
    result = runtime.invoke("Research quantum error correction")

Named after Mendicant Bias, the Forerunner Contender-class AI from Halo.
"""

__version__ = "5.0.0"

from mendicant_runtime.agent import make_mendicant_agent, MendicantRuntime
from mendicant_runtime.config import load_config
from mendicant_runtime.claude_model import ClaudeChatModel
from mendicant_runtime.credentials import ClaudeCredential, load_claude_credential
from mendicant_runtime.models import create_model
from mendicant_runtime.sandbox import LocalSandbox, LocalSandboxProvider
from mendicant_runtime.subagents import SubagentConfig, SubagentExecutor, SubagentTask
from mendicant_runtime.thread_state import (
    MendicantThreadState,
    SandboxState,
    TaskClassification,
    ThreadDataState,
    VerificationState,
)

__all__ = [
    "__version__",
    # Agent factory & runtime
    "make_mendicant_agent",
    "MendicantRuntime",
    # Configuration
    "load_config",
    "create_model",
    # Claude model & credentials
    "ClaudeChatModel",
    "ClaudeCredential",
    "load_claude_credential",
    # Thread state
    "MendicantThreadState",
    "SandboxState",
    "ThreadDataState",
    "TaskClassification",
    "VerificationState",
    # Sandbox
    "LocalSandbox",
    "LocalSandboxProvider",
    # Subagents
    "SubagentConfig",
    "SubagentExecutor",
    "SubagentTask",
]
