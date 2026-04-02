"""
mendicant_runtime.config
========================
YAML configuration loader for the Mendicant Bias runtime.

Searches for ``mendicant.yaml`` in the following order:
  1. Explicit *path* argument
  2. ``./mendicant.yaml`` (current working directory)
  3. ``~/.mendicant/mendicant.yaml`` (user home)

The YAML file has two top-level sections:

.. code-block:: yaml

    mendicant:
      context_budget:
        default_budget: 20000
      verification:
        model: gpt-4o-mini
        min_score: 0.75
      semantic_tool_router:
        top_k: 8
      adaptive_learning:
        max_patterns: 500
      smart_task_router:
        embedding_weight: 0.6

    runtime:
      model: anthropic/claude-sonnet-4-20250514
      temperature: 0.3
      max_tokens: 8192
      tools: []
      thread_persistence: true
      thread_store_path: .mendicant/threads

The ``mendicant`` section is parsed into a ``MendicantConfig`` for middleware.
The ``runtime`` section drives model selection, tool loading, and thread management.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default config file name
_CONFIG_FILENAME = "mendicant.yaml"

# Default runtime settings
_DEFAULT_RUNTIME: dict[str, Any] = {
    "model": "anthropic/claude-sonnet-4-20250514",
    "temperature": 0.3,
    "max_tokens": 8192,
    "tools": [],
    "thread_persistence": True,
    "thread_store_path": ".mendicant/threads",
}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """
    Load ``mendicant.yaml`` configuration.

    Search order:
      1. *path* argument (if provided)
      2. ``./mendicant.yaml`` (current working directory)
      3. ``~/.mendicant/mendicant.yaml`` (user home)

    If no config file is found, returns a dict with sensible defaults for
    both the ``mendicant`` and ``runtime`` sections.

    Parameters
    ----------
    path : str | Path | None
        Explicit path to a YAML config file.

    Returns
    -------
    dict[str, Any]
        Full config dict with ``"mendicant"`` and ``"runtime"`` keys.

    Raises
    ------
    FileNotFoundError
        If *path* is explicitly provided but the file does not exist.
    """
    resolved = _resolve_config_path(path)

    if resolved is not None:
        raw = _load_yaml_file(resolved)
        logger.info("[Config] Loaded config from %s", resolved)
    else:
        logger.info("[Config] No config file found; using defaults")
        raw = {}

    return _normalise(raw)


def _resolve_config_path(path: str | Path | None) -> Path | None:
    """Resolve the config file path using the search order."""
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p

    # Search order: cwd -> user home
    candidates = [
        Path.cwd() / _CONFIG_FILENAME,
        Path.home() / ".mendicant" / _CONFIG_FILENAME,
    ]

    # Also check MENDICANT_CONFIG environment variable
    env_path = os.environ.get("MENDICANT_CONFIG")
    if env_path:
        candidates.insert(0, Path(env_path))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load and parse a YAML file. Returns a dict."""
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required to load YAML config files. "
            "Install it with: pip install pyyaml"
        )

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML dict at top level, got {type(data).__name__}")
    return data


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise the raw YAML dict into a canonical two-section structure.

    Ensures ``"mendicant"`` and ``"runtime"`` keys always exist with
    at least default values.
    """
    mendicant_section = raw.get("mendicant", {})
    runtime_section = raw.get("runtime", {})

    # Merge runtime defaults with loaded values
    merged_runtime: dict[str, Any] = {**_DEFAULT_RUNTIME}
    if isinstance(runtime_section, dict):
        merged_runtime.update(runtime_section)

    return {
        "mendicant": mendicant_section if isinstance(mendicant_section, dict) else {},
        "runtime": merged_runtime,
    }
