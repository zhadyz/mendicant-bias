"""
mendicant_runtime.models
========================
Multi-provider LLM factory for the Mendicant Bias runtime.

Supports model name formats:

  - ``anthropic/claude-sonnet-4-20250514`` (explicit provider prefix)
  - ``claude-sonnet-4-20250514`` (auto-detected as Anthropic)
  - ``openai/gpt-4o`` (explicit OpenAI)
  - ``gpt-4o`` (auto-detected as OpenAI)

Provider detection heuristics:
  - Names starting with ``claude`` -> Anthropic
  - Names starting with ``gpt``, ``o1``, ``o3`` -> OpenAI
  - Everything else -> attempt OpenAI-compatible endpoint

Usage::

    from mendicant_runtime.models import create_model

    model = create_model("anthropic/claude-sonnet-4-20250514", temperature=0.3)
    model = create_model("gpt-4o", max_tokens=4096)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Provider prefixes that trigger specific integrations
_ANTHROPIC_PREFIXES = ("claude",)
_OPENAI_PREFIXES = ("gpt", "o1", "o3", "o4")


def create_model(name: str, **kwargs: Any) -> Any:
    """
    Create a LangChain chat model from a model name string.

    The *name* can include an explicit provider prefix (e.g.
    ``"anthropic/claude-sonnet-4-20250514"``) or be a bare model name that will
    be auto-detected.

    Parameters
    ----------
    name : str
        Model identifier, optionally with ``provider/`` prefix.
    **kwargs
        Extra keyword arguments forwarded to the model constructor
        (e.g. ``temperature``, ``max_tokens``, ``api_key``).

    Returns
    -------
    BaseChatModel
        A LangChain chat model instance.

    Raises
    ------
    ValueError
        If the provider cannot be determined.
    ImportError
        If the required LangChain provider package is not installed.
    """
    provider, model_id = _parse_model_name(name)

    if provider == "anthropic":
        return _create_anthropic(model_id, **kwargs)
    elif provider == "openai":
        return _create_openai(model_id, **kwargs)
    else:
        raise ValueError(
            f"Unknown model provider '{provider}' for model '{name}'. "
            f"Supported providers: anthropic, openai. "
            f"Use format 'provider/model-name' to specify explicitly."
        )


def _parse_model_name(name: str) -> tuple[str, str]:
    """
    Parse a model name into (provider, model_id).

    Examples
    --------
    >>> _parse_model_name("anthropic/claude-sonnet-4-20250514")
    ("anthropic", "claude-sonnet-4-20250514")
    >>> _parse_model_name("claude-sonnet-4-20250514")
    ("anthropic", "claude-sonnet-4-20250514")
    >>> _parse_model_name("gpt-4o")
    ("openai", "gpt-4o")
    """
    # Check for explicit provider prefix
    if "/" in name:
        parts = name.split("/", 1)
        return parts[0].lower().strip(), parts[1].strip()

    # Auto-detect provider from model name
    lower = name.lower()
    for prefix in _ANTHROPIC_PREFIXES:
        if lower.startswith(prefix):
            return "anthropic", name

    for prefix in _OPENAI_PREFIXES:
        if lower.startswith(prefix):
            return "openai", name

    # Default to OpenAI-compatible for unknown models
    logger.warning(
        "[Models] Could not auto-detect provider for '%s'; defaulting to openai",
        name,
    )
    return "openai", name


def _create_anthropic(model_id: str, **kwargs: Any) -> Any:
    """Create an Anthropic ChatAnthropic model."""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ImportError(
            "langchain-anthropic is required for Anthropic models. "
            "Install it with: pip install langchain-anthropic"
        ) from exc

    # Map common kwargs to Anthropic-specific names
    params: dict[str, Any] = {"model": model_id}

    if "temperature" in kwargs:
        params["temperature"] = kwargs.pop("temperature")
    if "max_tokens" in kwargs:
        params["max_tokens"] = kwargs.pop("max_tokens")
    if "api_key" in kwargs:
        params["anthropic_api_key"] = kwargs.pop("api_key")
    if "timeout" in kwargs:
        params["timeout"] = kwargs.pop("timeout")

    # Pass through any remaining kwargs
    params.update(kwargs)

    logger.info("[Models] Creating Anthropic model: %s", model_id)
    return ChatAnthropic(**params)


def _create_openai(model_id: str, **kwargs: Any) -> Any:
    """Create an OpenAI ChatOpenAI model."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "langchain-openai is required for OpenAI models. "
            "Install it with: pip install langchain-openai"
        ) from exc

    params: dict[str, Any] = {"model": model_id}

    if "temperature" in kwargs:
        params["temperature"] = kwargs.pop("temperature")
    if "max_tokens" in kwargs:
        params["max_tokens"] = kwargs.pop("max_tokens")
    if "api_key" in kwargs:
        params["openai_api_key"] = kwargs.pop("api_key")
    if "base_url" in kwargs:
        params["openai_api_base"] = kwargs.pop("base_url")
    if "timeout" in kwargs:
        params["request_timeout"] = kwargs.pop("timeout")

    # Pass through any remaining kwargs
    params.update(kwargs)

    logger.info("[Models] Creating OpenAI model: %s", model_id)
    return ChatOpenAI(**params)


def list_supported_providers() -> list[str]:
    """Return the list of supported provider names."""
    return ["anthropic", "openai"]
