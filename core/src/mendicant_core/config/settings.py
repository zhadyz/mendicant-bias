"""
Mendicant Bias V5 — Configuration Loader with Hot Reload

Mtime-based caching: config is loaded once and cached. On subsequent calls,
if the file's mtime has changed, config is reloaded automatically.

Search order for mendicant.yaml:
1. Explicit path argument
2. MENDICANT_CONFIG_PATH environment variable
3. .mendicant/mendicant.yaml (current directory)
4. ~/.mendicant/mendicant.yaml (home directory)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try importing PyYAML — fall back gracefully if not installed
# ---------------------------------------------------------------------------

try:
    import yaml  # type: ignore[import-untyped]

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_config: dict | None = None
_config_path: Path | None = None
_config_mtime: float | None = None


# ---------------------------------------------------------------------------
# Search for config file
# ---------------------------------------------------------------------------


def _find_config_path(explicit_path: str | None = None) -> Path | None:
    """
    Search for mendicant.yaml in standard locations.

    Order:
    1. *explicit_path* argument (if provided)
    2. ``MENDICANT_CONFIG_PATH`` environment variable
    3. ``.mendicant/mendicant.yaml`` in the current working directory
    4. ``~/.mendicant/mendicant.yaml`` in the user's home directory

    Returns
    -------
    Path | None
        The first path that exists, or ``None`` if no config file is found.
    """
    candidates: list[Path] = []

    if explicit_path:
        candidates.append(Path(explicit_path))

    env_path = os.environ.get("MENDICANT_CONFIG_PATH")
    if env_path:
        candidates.append(Path(env_path))

    candidates.append(Path.cwd() / ".mendicant" / "mendicant.yaml")
    candidates.append(Path.home() / ".mendicant" / "mendicant.yaml")

    for p in candidates:
        if p.exists():
            logger.debug("[Config] Found config at %s", p)
            return p

    return None


# ---------------------------------------------------------------------------
# Mtime helper
# ---------------------------------------------------------------------------


def _get_mtime(path: Path) -> float | None:
    """
    Get file modification time, or ``None`` on error.

    Parameters
    ----------
    path : Path
        File path to stat.

    Returns
    -------
    float | None
        The mtime as a float, or ``None`` if the file does not exist
        or cannot be stat'd.
    """
    try:
        return path.stat().st_mtime
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    """
    Load a YAML file and return its contents as a dict.

    Falls back to an empty dict if PyYAML is not installed or on parse error.
    """
    if not _HAS_YAML:
        logger.warning(
            "[Config] PyYAML not installed; returning empty config. "
            "Install with: pip install pyyaml"
        )
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            return {}
        if not isinstance(data, dict):
            logger.warning("[Config] Config file %s did not parse to a dict", path)
            return {}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.error("[Config] Failed to parse %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_config(path: str | None = None) -> dict:
    """
    Get config with mtime-based caching.

    On the first call, the config file is located (see ``_find_config_path``
    for search order), loaded, parsed, and cached in module-level globals.

    On subsequent calls:
    - If no config file was found originally and no *path* is given, return
      the cached (possibly empty) dict immediately.
    - If a config file was found, check its mtime. If the file has been
      modified since last load, reload it automatically.

    Parameters
    ----------
    path : str | None
        Explicit path to a YAML config file.  Overrides the search order.

    Returns
    -------
    dict
        The parsed config dict.  Returns ``{}`` if no config is found or
        if PyYAML is not installed.
    """
    global _config, _config_path, _config_mtime

    # First load or explicit path change
    if _config is None or (path is not None and (
        _config_path is None or str(Path(path).resolve()) != str(_config_path.resolve())
    )):
        found = _find_config_path(path)
        if found is None:
            _config = {}
            _config_path = None
            _config_mtime = None
            return _config

        _config_path = found
        _config_mtime = _get_mtime(found)
        _config = _load_yaml(found)
        logger.info("[Config] Loaded config from %s", found)
        return _config

    # Check mtime for hot reload
    if _config_path is not None:
        current_mtime = _get_mtime(_config_path)
        if current_mtime is not None and current_mtime != _config_mtime:
            logger.info("[Config] Config changed on disk, reloading %s", _config_path)
            _config_mtime = current_mtime
            _config = _load_yaml(_config_path)

    return _config or {}


def reload_config(path: str | None = None) -> dict:
    """
    Force reload config, ignoring any cached state.

    Parameters
    ----------
    path : str | None
        Explicit path to a YAML config file.  If ``None``, re-searches
        using the standard search order.

    Returns
    -------
    dict
        The freshly loaded config dict.
    """
    global _config, _config_path, _config_mtime

    found = _find_config_path(path)
    if found is None:
        _config = {}
        _config_path = None
        _config_mtime = None
        return _config

    _config_path = found
    _config_mtime = _get_mtime(found)
    _config = _load_yaml(found)
    logger.info("[Config] Force-reloaded config from %s", found)
    return _config


def reset_config() -> None:
    """
    Clear config cache (for tests).

    Resets all module-level cache variables so the next ``get_config()``
    call performs a fresh search and load.
    """
    global _config, _config_path, _config_mtime
    _config = None
    _config_path = None
    _config_mtime = None


def get_mendicant_config() -> "MendicantConfig":
    """
    Get ``MendicantConfig`` from the loaded config dict.

    Loads the raw config via ``get_config()``, extracts the ``"mendicant"``
    section, and parses it into a ``MendicantConfig`` Pydantic model.

    Returns
    -------
    MendicantConfig
        The parsed config with defaults applied for missing fields.
    """
    from mendicant_core.config import MendicantConfig

    raw = get_config()
    return MendicantConfig.from_dict(raw.get("mendicant", {}))
