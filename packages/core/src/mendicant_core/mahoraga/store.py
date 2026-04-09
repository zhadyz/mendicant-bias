"""
AdaptationStore — Persistent storage for Mahoraga adaptation rules.

Atomic file I/O modeled after MemoryStore. Supports backup before
destructive operations.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AdaptationStore:
    """Persistent storage for adaptation rules. Atomic file I/O."""

    def __init__(self, path: str | Path = ".mendicant/mahoraga.json") -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[dict[str, Any]]:
        """Load rules from disk. Returns empty list if file is absent or corrupt."""
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return raw
            # Handle wrapped format: {"rules": [...]}
            if isinstance(raw, dict) and "rules" in raw:
                return raw["rules"]
            logger.warning(
                "[Mahoraga] Unexpected format in %s — starting fresh", self._path
            )
            return []
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "[Mahoraga] Corrupt store at %s: %s — starting fresh",
                self._path,
                exc,
            )
            return []

    def save(self, rules: list[dict[str, Any]]) -> None:
        """Atomic write: temp file + os.replace to prevent corruption."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(rules, indent=2, ensure_ascii=False)

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=".mahoraga_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, str(self._path))
            logger.debug("[Mahoraga] Saved %d rules to %s", len(rules), self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def backup(self) -> Path | None:
        """Create a timestamped backup before major changes. Returns backup path."""
        if not self._path.exists():
            return None

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_path = self._path.with_suffix(f".backup_{ts}.json")
        shutil.copy2(str(self._path), str(backup_path))
        logger.info("[Mahoraga] Backup created at %s", backup_path)
        return backup_path
