"""Auto-load Claude Code OAuth credentials for Mendicant Bias runtime.

Implements credential loading from multiple sources so that Mendicant Bias
can piggyback on the user's Claude subscription (via Claude Code CLI) without
requiring a separate ``ANTHROPIC_API_KEY``.

Lookup order:
  1. ``$CLAUDE_CODE_OAUTH_TOKEN`` or ``$ANTHROPIC_AUTH_TOKEN`` env var
  2. ``$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`` (fd handoff from parent process)
  3. ``$CLAUDE_CODE_CREDENTIALS_PATH`` (override file location)
  4. ``~/.claude/.credentials.json`` (default Claude Code installation)

The credentials file contains::

    {
      "claudeAiOauth": {
        "accessToken": "sk-ant-oat01-...",
        "refreshToken": "sk-ant-ort01-...",
        "expiresAt": 1773430695128,
        ...
      }
    }
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Required beta headers for Claude Code OAuth tokens
OAUTH_ANTHROPIC_BETAS = "oauth-2025-04-20,claude-code-20250219,interleaved-thinking-2025-05-14"


def is_oauth_token(token: str) -> bool:
    """Return ``True`` if *token* is a Claude Code OAuth token (not a standard API key)."""
    return isinstance(token, str) and "sk-ant-oat" in token


@dataclass
class ClaudeCredential:
    """Resolved Claude credential — may be OAuth or a plain API key."""

    access_token: str
    source: str = ""
    is_oauth: bool = False
    refresh_token: str = ""
    expires_at: int = 0

    @property
    def is_expired(self) -> bool:
        """Check expiry with a 1-minute safety buffer (timestamps in ms)."""
        if self.expires_at <= 0:
            return False
        return time.time() * 1000 > self.expires_at - 60_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _home_dir() -> Path:
    """Return the user's home directory, respecting ``$HOME``."""
    home = os.getenv("HOME")
    if home:
        return Path(home).expanduser()
    return Path.home()


def _load_json_file(path: Path, label: str) -> dict[str, Any] | None:
    """Load and parse a JSON file, returning ``None`` on any error."""
    if not path.exists():
        logger.debug("%s not found: %s", label, path)
        return None
    if path.is_dir():
        logger.warning("%s path is a directory, expected a file: %s", label, path)
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", label, exc)
        return None


def _read_secret_from_file_descriptor(env_var: str) -> str | None:
    """Read a secret from a numeric file descriptor stored in *env_var*."""
    fd_value = os.getenv(env_var)
    if not fd_value:
        return None
    try:
        fd = int(fd_value)
    except ValueError:
        logger.warning("%s must be an integer file descriptor, got: %s", env_var, fd_value)
        return None
    try:
        secret = os.read(fd, 1024 * 1024).decode().strip()
    except OSError as exc:
        logger.warning("Failed to read %s: %s", env_var, exc)
        return None
    return secret or None


def _credential_from_direct_token(access_token: str, source: str) -> ClaudeCredential | None:
    """Wrap a raw token string in a :class:`ClaudeCredential`."""
    token = access_token.strip()
    if not token:
        return None
    return ClaudeCredential(
        access_token=token,
        source=source,
        is_oauth=is_oauth_token(token),
    )


def _iter_credential_paths() -> list[Path]:
    """Return candidate credential file paths (override first, then default)."""
    paths: list[Path] = []
    override = os.getenv("CLAUDE_CODE_CREDENTIALS_PATH")
    if override:
        paths.append(Path(override).expanduser())

    default = _home_dir() / ".claude" / ".credentials.json"
    if not paths or paths[-1] != default:
        paths.append(default)

    return paths


def _extract_credential_from_file(data: dict[str, Any], source: str) -> ClaudeCredential | None:
    """Extract a :class:`ClaudeCredential` from a parsed credentials JSON blob."""
    oauth = data.get("claudeAiOauth", {})
    access_token = oauth.get("accessToken", "")
    if not access_token:
        logger.debug("Credentials container exists but no accessToken found")
        return None

    cred = ClaudeCredential(
        access_token=access_token,
        refresh_token=oauth.get("refreshToken", ""),
        expires_at=oauth.get("expiresAt", 0),
        source=source,
        is_oauth=is_oauth_token(access_token),
    )

    if cred.is_expired:
        logger.warning("Claude Code OAuth token is expired. Run 'claude' to refresh.")
        return None

    return cred


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_claude_credential() -> ClaudeCredential | None:
    """Load the best available Claude credential.

    Checks sources in priority order and returns the first valid credential,
    or ``None`` if nothing is found.

    Priority:
      1. ``$CLAUDE_CODE_OAUTH_TOKEN`` or ``$ANTHROPIC_AUTH_TOKEN`` env var
      2. ``$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR``
      3. ``$CLAUDE_CODE_CREDENTIALS_PATH`` / ``~/.claude/.credentials.json``
    """
    # --- env var tokens ---
    direct_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    if direct_token:
        cred = _credential_from_direct_token(direct_token, "env-var")
        if cred:
            logger.info("Loaded Claude credential from environment variable")
        return cred

    # --- file descriptor ---
    fd_token = _read_secret_from_file_descriptor("CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR")
    if fd_token:
        cred = _credential_from_direct_token(fd_token, "file-descriptor")
        if cred:
            logger.info("Loaded Claude credential from file descriptor")
        return cred

    # --- credential files ---
    override_path = os.getenv("CLAUDE_CODE_CREDENTIALS_PATH")
    override_path_obj = Path(override_path).expanduser() if override_path else None
    for cred_path in _iter_credential_paths():
        data = _load_json_file(cred_path, "Claude credentials")
        if data is None:
            continue
        cred = _extract_credential_from_file(data, "credentials-file")
        if cred:
            label = "override path" if (override_path_obj and cred_path == override_path_obj) else "default path"
            logger.info(
                "Loaded Claude OAuth credential from %s (expires_at=%s)",
                label,
                cred.expires_at,
            )
            return cred

    return None
