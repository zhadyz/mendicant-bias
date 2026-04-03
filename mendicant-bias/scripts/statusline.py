#!/usr/bin/env python3
"""
Mendicant Bias — Three-state intelligent status line for Claude Code.

Two signal files, zero network calls, sub-millisecond execution.

States:
  INVISIBLE   — Mendicant not used this session. Clean terminal.
  DIM BLUE    — Mendicant active this session, currently idle.
                ⬡ MENDICANT
  BRIGHT BLUE — Mendicant actively processing a hook right now.
                ⬡ MENDICANT ── classifying Bash...
"""

import os
import sys
import tempfile
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Appearance ──────────────────────────────────────────────────────────────

BRIGHT  = "\033[38;2;0;170;255m"
MEDIUM  = "\033[38;2;0;120;200m"
SUBTLE  = "\033[38;2;40;55;80m"
RESET   = "\033[0m"
BOLD    = "\033[1m"

ICON = "\u2b21"
NAME = "MENDICANT"
SEP  = "\u2500\u2500"

# ── Signal files (cross-platform temp dir) ──────────────────────────────────

_TMPDIR = tempfile.gettempdir()
SESSION_FILE = os.path.join(_TMPDIR, "mendicant_session")
HOOK_FILE    = os.path.join(_TMPDIR, "mendicant_hook_active")

SESSION_MAX_AGE = 4 * 3600
HOOK_MAX_AGE    = 10


def _file_age(path: str) -> float | None:
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _render(parts: list[tuple[str, str]]) -> str:
    return "".join(f"{c}{t}{RESET}" for c, t in parts)


def main():
    # State 3: BRIGHT BLUE — actively processing
    hook_age = _file_age(HOOK_FILE)
    if hook_age is not None and hook_age < HOOK_MAX_AGE:
        hook_msg = _read_file(HOOK_FILE) or "processing..."
        print(_render([
            (BRIGHT + BOLD, f"{ICON} {NAME}"),
            (SUBTLE,        f" {SEP} "),
            (BRIGHT,        hook_msg),
        ]))
        return

    # State 2: DIM BLUE — session active, currently idle
    session_age = _file_age(SESSION_FILE)
    if session_age is not None and session_age < SESSION_MAX_AGE:
        print(_render([
            (MEDIUM, f"{ICON} {NAME}"),
        ]))
        return

    # State 1: INVISIBLE — not used this session


if __name__ == "__main__":
    main()
