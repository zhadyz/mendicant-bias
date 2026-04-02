"""
Mendicant Bias V5 — Agent Runtime
==================================

Standalone LangGraph agent runtime with Mendicant intelligence middleware
wired as first-class citizens.

This package provides:

- ``make_mendicant_agent`` — One-call agent factory with full middleware chain
- ``MendicantRuntime`` — Managed runtime with thread state and tool registration
- ``load_config`` — YAML configuration loader
- ``create_model`` — Multi-provider LLM factory

The middleware chain fires in this order:

  before_agent:  SmartTaskRouter -> SemanticToolRouter
  before_model:  ContextBudget
  after_agent:   Verification -> AdaptiveLearning

Quick Start::

    from mendicant_runtime import make_mendicant_agent

    agent = make_mendicant_agent(model_name="claude-sonnet-4-20250514")
    result = agent.invoke({"messages": [("user", "Hello")]})

Named after Mendicant Bias, the Forerunner Contender-class AI from Halo.
"""

__version__ = "5.0.0"

from mendicant_runtime.agent import make_mendicant_agent, MendicantRuntime
from mendicant_runtime.config import load_config
from mendicant_runtime.models import create_model

__all__ = [
    "__version__",
    "make_mendicant_agent",
    "MendicantRuntime",
    "load_config",
    "create_model",
]
