"""
mendicant_bias.hooks
====================
Generate and manage Claude Code settings.json hooks configuration for
Mendicant Bias Phase 3 integration.

Provides:
- generate_hooks_config()  — Build the hooks dict for CC settings
- install_hooks()          — Merge Mendicant hooks into CC's settings.json
- remove_hooks()           — Remove Mendicant hooks from CC's settings.json
"""

from __future__ import annotations

import json
import logging
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_cc_settings_path() -> Path:
    """Resolve the default Claude Code settings.json path per platform."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "Claude" / "settings.json"
        return Path.home() / "AppData" / "Roaming" / "Claude" / "settings.json"
    elif system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "settings.json"
        )
    else:
        # Linux / other
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / "claude" / "settings.json"


def generate_hooks_config(
    gateway_url: str = "http://localhost:8001",
) -> dict[str, Any]:
    """
    Generate the hooks section for Claude Code settings.json.

    Parameters
    ----------
    gateway_url : str
        Base URL of the Mendicant Bias gateway (default http://localhost:8001).

    Returns
    -------
    dict
        A dict with a single "hooks" key containing the CC hook configuration.
    """
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"{gateway_url}/hooks/session-start",
                            "timeout": 10,
                            "statusMessage": "⬡ Mendicant Bias: Loading memory...",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"{gateway_url}/hooks/pre-tool-use",
                            "timeout": 5,
                            "statusMessage": "⬡ Mendicant Bias: Classifying...",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"{gateway_url}/hooks/post-tool-use",
                            "timeout": 15,
                            "statusMessage": "⬡ Mendicant Bias: Verifying write...",
                        }
                    ],
                },
                {
                    "matcher": "Edit",
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"{gateway_url}/hooks/post-tool-use",
                            "timeout": 15,
                            "statusMessage": "⬡ Mendicant Bias: Verifying edit...",
                        }
                    ],
                },
            ],
        }
    }


def install_hooks(
    settings_path: Path | None = None,
    gateway_url: str = "http://localhost:8001",
) -> bool:
    """
    Merge Mendicant hooks into Claude Code's settings.json.

    Reads the existing settings, merges the Mendicant hook configuration
    (overwriting any existing Mendicant hooks), and writes back atomically.

    Parameters
    ----------
    settings_path : Path | None
        Path to CC settings.json.  Defaults to the platform-appropriate location.
    gateway_url : str
        Gateway URL for the hook endpoints.

    Returns
    -------
    bool
        True if hooks were successfully installed.
    """
    path = settings_path or _default_cc_settings_path()

    # Load existing settings or start fresh
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[Hooks] Failed to read existing settings: %s", exc)
            existing = {}

    # Generate Mendicant hooks config
    mendicant_hooks = generate_hooks_config(gateway_url)["hooks"]

    # Merge into existing hooks section
    if "hooks" not in existing:
        existing["hooks"] = {}

    # For each event type, replace/add Mendicant entries while preserving
    # non-Mendicant hooks from other integrations
    for event_name, event_hooks in mendicant_hooks.items():
        if event_name not in existing["hooks"]:
            existing["hooks"][event_name] = []

        # Remove any existing Mendicant hooks (identified by gateway URL)
        existing_event = existing["hooks"][event_name]
        cleaned = []
        for hook_group in existing_event:
            if isinstance(hook_group, dict) and "hooks" in hook_group:
                non_mendicant = [
                    h
                    for h in hook_group["hooks"]
                    if not (
                        isinstance(h, dict)
                        and isinstance(h.get("url", ""), str)
                        and "/hooks/" in h.get("url", "")
                        and gateway_url in h.get("url", "")
                    )
                ]
                if non_mendicant:
                    hook_group["hooks"] = non_mendicant
                    cleaned.append(hook_group)
            else:
                cleaned.append(hook_group)

        # Append Mendicant hooks
        cleaned.extend(event_hooks)
        existing["hooks"][event_name] = cleaned

    # Write atomically
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".settings_", suffix=".tmp"
        )
        payload = json.dumps(existing, indent=2, ensure_ascii=False)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, str(path))
        logger.info("[Hooks] Installed Mendicant hooks into %s", path)
        return True
    except Exception as exc:
        logger.error("[Hooks] Failed to write settings: %s", exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


def remove_hooks(
    settings_path: Path | None = None,
    gateway_url: str = "http://localhost:8001",
) -> bool:
    """
    Remove Mendicant hooks from Claude Code's settings.json.

    Identifies Mendicant hooks by matching the gateway URL in hook URLs,
    and removes them while preserving all other hook configurations.

    Parameters
    ----------
    settings_path : Path | None
        Path to CC settings.json.  Defaults to the platform-appropriate location.
    gateway_url : str
        Gateway URL to match when identifying Mendicant hooks.

    Returns
    -------
    bool
        True if hooks were successfully removed (or were already absent).
    """
    path = settings_path or _default_cc_settings_path()

    if not path.exists():
        logger.info("[Hooks] Settings file not found at %s — nothing to remove", path)
        return True

    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[Hooks] Failed to read settings: %s", exc)
        return False

    hooks_section = existing.get("hooks")
    if not hooks_section or not isinstance(hooks_section, dict):
        logger.info("[Hooks] No hooks section found — nothing to remove")
        return True

    modified = False
    for event_name in list(hooks_section.keys()):
        event_hooks = hooks_section[event_name]
        if not isinstance(event_hooks, list):
            continue

        cleaned = []
        for hook_group in event_hooks:
            if isinstance(hook_group, dict) and "hooks" in hook_group:
                non_mendicant = [
                    h
                    for h in hook_group["hooks"]
                    if not (
                        isinstance(h, dict)
                        and isinstance(h.get("url", ""), str)
                        and "/hooks/" in h.get("url", "")
                        and gateway_url in h.get("url", "")
                    )
                ]
                if non_mendicant:
                    hook_group["hooks"] = non_mendicant
                    cleaned.append(hook_group)
                else:
                    modified = True
            else:
                cleaned.append(hook_group)

        if len(cleaned) != len(event_hooks):
            modified = True
        hooks_section[event_name] = cleaned

        # Remove empty event lists
        if not cleaned:
            del hooks_section[event_name]
            modified = True

    # Remove empty hooks section
    if not hooks_section:
        del existing["hooks"]
        modified = True

    if not modified:
        logger.info("[Hooks] No Mendicant hooks found — nothing to remove")
        return True

    # Write back
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".settings_", suffix=".tmp"
        )
        payload = json.dumps(existing, indent=2, ensure_ascii=False)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, str(path))
        logger.info("[Hooks] Removed Mendicant hooks from %s", path)
        return True
    except Exception as exc:
        logger.error("[Hooks] Failed to write settings: %s", exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
