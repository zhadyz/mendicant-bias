#!/usr/bin/env python3
"""
Mendicant Bias — Animated status line for Claude Code.

The status line command runs on every CC state change. Each call
outputs a different frame, creating animation driven by CC's own
event loop. Not 60fps — event-driven. But it moves.

States:
  INVISIBLE   — not used this session
  DIM BLUE    — session active, idle (⬡ MENDICANT)
  GOLD WHEEL  — Mahoraga recently adapted (☸ spinning mini-wheel frames)
  BRIGHT BLUE — hook actively processing (⬡ MENDICANT ── classifying...)
"""

import math
import os
import sys
import tempfile
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Colors ──────────────────────────────────────────────────────────────────

BRIGHT = "\033[38;2;0;170;255m"
MEDIUM = "\033[38;2;0;120;200m"
GOLD   = "\033[38;2;200;170;80m"
SUBTLE = "\033[38;2;40;55;80m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

NAME = "MENDICANT"
SEP  = "\u2500\u2500"

# ── Signal files ────────────────────────────────────────────────────────────

_TMPDIR = tempfile.gettempdir()
SESSION_FILE = os.path.join(_TMPDIR, "mendicant_session")
HOOK_FILE    = os.path.join(_TMPDIR, "mendicant_hook_active")
ADAPT_FILE   = os.path.join(_TMPDIR, "mendicant_adapted")
FRAME_FILE   = os.path.join(_TMPDIR, "mendicant_wheel_frame")

SESSION_MAX_AGE = 4 * 3600
HOOK_MAX_AGE    = 10
ADAPT_MAX_AGE   = 8  # Animate for 8 seconds after adaptation

# ── Mini wheel frames ──────────────────────────────────────────────────────
# Pre-rendered single-line Braille wheel at 8 rotation angles.
# Each "frame" is a compact representation of the dharma wheel.

def _build_wheel_frames() -> list[str]:
    """Build 16 frames of an animated spinning wheel using Braille spinners."""
    # Instead of rotating the whole wheel geometry (which looks the same
    # due to 8-fold symmetry), use a DIFFERENT approach:
    # Static wheel + animated orbiting dot that circles the rim.
    # The dot position changes per frame, creating visible motion.

    R = 10
    PW, PH = 24, 24
    CX, CY = PW / 2, PH / 2
    BRAILLE_BASE = 0x2800
    DOT_MAP = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]

    # Build static wheel once
    static_pixels = set()

    # Outer circle
    for i in range(128):
        a = i * math.pi * 2 / 128
        static_pixels.add((int(CX + R * math.cos(a)), int(CY + R * math.sin(a))))

    # Inner circle
    for i in range(64):
        a = i * math.pi * 2 / 64
        static_pixels.add((int(CX + 3 * math.cos(a)), int(CY + 3 * math.sin(a))))

    # 8 fixed spokes
    for spoke in range(8):
        sa = spoke * math.pi / 4
        for r_step in range(30, R * 10):
            r = r_step / 10.0
            static_pixels.add((int(CX + r * math.cos(sa)), int(CY + r * math.sin(sa))))

    # 8 fixed knobs
    for spoke in range(8):
        sa = spoke * math.pi / 4
        kx, ky = CX + R * math.cos(sa), CY + R * math.sin(sa)
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dx * dx + dy * dy <= 1:
                    static_pixels.add((int(kx + dx), int(ky + dy)))

    # Filter valid
    static_pixels = {(x, y) for x, y in static_pixels if 0 <= x < PW and 0 <= y < PH}

    frames = []
    NUM_FRAMES = 16

    for frame_idx in range(NUM_FRAMES):
        grid = [[False] * PW for _ in range(PH)]

        # Draw static wheel
        for x, y in static_pixels:
            grid[y][x] = True

        # Animated element: bright orbiting dot + trail
        orbit_angle = frame_idx * math.pi * 2 / NUM_FRAMES
        orbit_r = R + 1.5
        for trail in range(4):  # 4-pixel trail
            ta = orbit_angle - trail * 0.15
            ox = int(CX + orbit_r * math.cos(ta))
            oy = int(CY + orbit_r * math.sin(ta))
            if 0 <= ox < PW and 0 <= oy < PH:
                grid[oy][ox] = True
            # Thicken the lead dot
            if trail == 0:
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        nx, ny = ox + dx, oy + dy
                        if 0 <= nx < PW and 0 <= ny < PH:
                            grid[ny][nx] = True

        # Encode
        CW_b = PW // 2
        CH_b = PH // 4
        rows = []
        for cy_b in range(CH_b):
            row_chars = []
            for cx_b in range(CW_b):
                braille = 0
                for dy in range(4):
                    for dx in range(2):
                        px, py = cx_b * 2 + dx, cy_b * 4 + dy
                        if px < PW and py < PH and grid[py][px]:
                            braille |= DOT_MAP[dy][dx]
                row_chars.append(chr(BRAILLE_BASE + braille) if braille else ' ')
            rows.append(''.join(row_chars).rstrip())

        frames.append(tuple(rows))

    return frames


_FRAMES: list[tuple[str, str]] | None = None


def _get_frames() -> list[tuple[str, str]]:
    global _FRAMES
    if _FRAMES is None:
        _FRAMES = _build_wheel_frames()
    return _FRAMES


def _get_frame_index() -> int:
    """Read and increment persistent frame counter."""
    try:
        with open(FRAME_FILE, "r") as f:
            idx = int(f.read().strip())
    except (OSError, ValueError):
        idx = 0

    next_idx = (idx + 1) % 16
    try:
        with open(FRAME_FILE, "w") as f:
            f.write(str(next_idx))
    except OSError:
        pass

    return idx


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
    # State 4: BRIGHT BLUE — hook actively processing
    hook_age = _file_age(HOOK_FILE)
    if hook_age is not None and hook_age < HOOK_MAX_AGE:
        hook_msg = _read_file(HOOK_FILE) or "processing..."
        print(_render([
            (BRIGHT + BOLD, f"\u2b21 {NAME}"),
            (SUBTLE,        f" {SEP} "),
            (BRIGHT,        hook_msg),
        ]))
        return

    # State 3: GOLD ANIMATED WHEEL — Mahoraga recently adapted
    adapt_age = _file_age(ADAPT_FILE)
    if adapt_age is not None and adapt_age < ADAPT_MAX_AGE:
        adapt_msg = _read_file(ADAPT_FILE) or "adapted"
        frames = _get_frames()
        idx = _get_frame_index()
        rows = frames[idx % len(frames)]

        # Wheel rows with text beside middle rows
        output_lines = []
        mid = len(rows) // 2
        for i, row in enumerate(rows):
            if i == mid - 1:
                line = _render([(GOLD, row), (SUBTLE, f" {SEP} "), (GOLD + BOLD, f"\u2638 {NAME}")])
            elif i == mid:
                line = _render([(GOLD, row), (SUBTLE, f" {SEP} "), (GOLD, adapt_msg)])
            else:
                line = _render([(GOLD, row)])
            output_lines.append(line)

        print('\n'.join(output_lines))
        return

    # State 2: DIM BLUE — session active, idle
    session_age = _file_age(SESSION_FILE)
    if session_age is not None and session_age < SESSION_MAX_AGE:
        print(_render([
            (MEDIUM, f"\u2b21 {NAME}"),
        ]))
        return

    # State 1: INVISIBLE


if __name__ == "__main__":
    main()
