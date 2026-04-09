"""
Mahoraga Dharma Wheel — Claude Code Terminal Animation

Renders a Braille Dharma wheel animation directly inside Claude Code's
append-only output stream. Designed around Claude Code's constraints:

  - No ANSI escape codes (stripped from bash output)
  - No cursor positioning (Ink renderer owns the terminal)
  - Pure Unicode Braille characters (U+2800-U+28FF)
  - Progressive bash streaming creates the animation

The animation embraces scrolling — each keyframe cascades down the
terminal like a visual timeline. The scroll IS the animation.

Uses the same wheel geometry as wheel.py, scaled for compact display.
"""

from __future__ import annotations

import math
import random
import sys
import time

# ── Grid ────────────────────────────────────────────────────────────────────

PW, PH = 64, 64
CW, CH = PW // 2, PH // 4  # 32 chars × 16 rows
PCX, PCY = PW / 2.0, PH / 2.0
BRAILLE_BASE = 0x2800

DOT_MAP = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
]

# ── Geometry (from wheel.py, scaled to 64×64) ──────────────────────────────

_base_pixels: list[tuple[int, int]] = []


def _build_model() -> None:
    if _base_pixels:
        return

    # Inner hub ring (radius 5–8) and outer rim (radius 18–21)
    for y in range(-32, 32):
        for x in range(-32, 32):
            dist = math.sqrt((x + 0.5) ** 2 + (y + 0.5) ** 2)
            if (5 <= dist <= 8) or (18 <= dist <= 21):
                _base_pixels.append((x, y))

    # 8 spokes — cardinal and diagonal, thickened
    for r in range(8, 24):
        _base_pixels.extend([(-1, -r), (0, -r)])       # N
        _base_pixels.extend([(-1, r - 1), (0, r - 1)]) # S
        _base_pixels.extend([(r - 1, -1), (r - 1, 0)]) # E
        _base_pixels.extend([(-r, -1), (-r, 0)])        # W

    o_start = int(8 * 0.7071)
    o_end = int(24 * 0.7071)
    for o in range(o_start, o_end + 1):
        # NE, SE, SW, NW diagonals — thickened
        _base_pixels.extend([
            (o, -o), (o - 1, -o), (o, -o + 1),
            (o, o), (o - 1, o), (o, o - 1),
            (-o, -o), (-o + 1, -o), (-o, -o + 1),
            (-o, o), (-o + 1, o), (-o, o - 1),
        ])

    # Knobs at spoke tips
    knob_dist = 25
    knob_r = 3.0
    diag = knob_dist * 0.7071
    centers = [
        (0, -knob_dist), (0, knob_dist),
        (knob_dist, 0), (-knob_dist, 0),
        (diag, -diag), (diag, diag),
        (-diag, -diag), (-diag, diag),
    ]
    for cx, cy in centers:
        for y in range(-32, 32):
            for x in range(-32, 32):
                if math.sqrt((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) <= knob_r:
                    _base_pixels.append((x, y))


# ── Rendering ───────────────────────────────────────────────────────────────


def _render_frame(
    angle: float,
    glow: float = 0.0,
    shake_x: float = 0.0,
    shake_y: float = 0.0,
    particles: list[tuple[float, float]] | None = None,
) -> str:
    """Render a single Braille frame. No ANSI. Pure Unicode."""
    grid = bytearray(PW * PH)

    sx, sy = shake_x * 1.5, shake_y * 1.5

    def stamp(x: float, y: float) -> None:
        px, py = int(round(x + sx)), int(round(y + sy))
        if 0 <= px < PW and 0 <= py < PH:
            grid[py * PW + px] = 1

    def draw_ring(cx: float, cy: float, rad: float, thickness: float) -> None:
        steps = max(1, int(math.ceil(2 * math.pi * rad * 2)))
        for i in range(steps):
            a = (i / steps) * math.pi * 2
            bx, by = cx + rad * math.cos(a), cy + rad * math.sin(a)
            nx, ny = math.cos(a), math.sin(a)
            ht = thickness / 2.0
            w = -ht
            while w <= ht:
                stamp(bx + nx * w, by + ny * w)
                w += 0.5

    # Glow ring — visible as extra outer halo
    if glow > 0.2:
        draw_ring(PCX, PCY, 24, 1 + glow)

    # Rotate and stamp wheel body
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    for px, py in _base_pixels:
        rx = px * cos_a - py * sin_a
        ry = px * sin_a + py * cos_a
        stamp(PCX + rx, PCY + ry)

    # Inner hub glow
    if glow > 0.3:
        draw_ring(PCX, PCY, 5, 1.5)
        for i in range(8):
            a = angle + i * math.pi / 4
            draw_ring(PCX + 25 * math.cos(a), PCY + 25 * math.sin(a), 4.5, 1)

    # Particle spray
    if particles:
        for px, py in particles:
            stamp(PCX + px, PCY + py)
            # Thicken particles slightly
            stamp(PCX + px + 0.5, PCY + py)
            stamp(PCX + px, PCY + py + 0.5)

    # Compile Braille characters
    lines: list[str] = []
    for cy in range(CH):
        row: list[str] = []
        for cx in range(CW):
            braille = 0
            for dy in range(4):
                for dx in range(2):
                    px_pos = cx * 2 + dx
                    py_pos = cy * 4 + dy
                    if px_pos < PW and py_pos < PH and grid[py_pos * PW + px_pos]:
                        braille |= DOT_MAP[dy][dx]
            row.append(chr(BRAILLE_BASE + braille) if braille else " ")
        lines.append("".join(row).rstrip())

    # Trim empty leading/trailing lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def _spray_particles(count: int, r_min: float, r_max: float) -> list[tuple[float, float]]:
    """Generate random particle positions radiating outward."""
    return [
        (
            random.uniform(r_min, r_max) * math.cos(random.uniform(0, math.tau)),
            random.uniform(r_min, r_max) * math.sin(random.uniform(0, math.tau)),
        )
        for _ in range(count)
    ]


# ── Box drawing ─────────────────────────────────────────────────────────────


def _box(content: str, label: str = "") -> str:
    """Wrap content in a Unicode box with optional centered label."""
    lines = content.split("\n")
    max_content = max((len(line) for line in lines), default=0)
    width = max(max_content + 2, len(label) + 4)

    if label:
        pad_left = max(0, (width - len(label) - 2) // 2)
        pad_right = max(0, width - pad_left - len(label) - 2)
        top = "┏" + "━" * pad_left + " " + label + " " + "━" * pad_right + "┓"
    else:
        top = "┏" + "━" * width + "┓"

    boxed = [top]
    for line in lines:
        padding = max(0, width - len(line))
        boxed.append("┃" + line + " " * padding + "┃")
    boxed.append("┗" + "━" * width + "┛")
    return "\n".join(boxed)


# ── Animation ───────────────────────────────────────────────────────────────


def play(text: str = "A D A P T E D", angle_offset: float = 0.0) -> None:
    """Play the Mahoraga wheel adaptation animation for Claude Code.

    Outputs sequential Braille keyframes via stdout. The progressive
    streaming of bash output creates a flip-book animation in the
    terminal. Each frame is a composed moment in the wheel's turning.

    Parameters
    ----------
    text : str
        Text shown below the wheel after adaptation.
    angle_offset : float
        Starting angle (accumulates across adaptations).
    """
    _build_model()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    turn = math.pi / 4  # 1/8 rotation per adaptation
    out = sys.stdout.write
    flush = sys.stdout.flush

    # ── Phase timing ────────────────────────────────────────────────────

    keyframes: list[dict] = [
        # Phase 1: REST — the wheel before the turn
        {
            "angle": angle_offset,
            "glow": 0.0,
            "shake": 0.0,
            "particles": 0,
            "label": "",
            "delay": 0.35,
        },
        # Phase 2: GATHER — energy begins to collect
        {
            "angle": angle_offset + math.sin(0.3 * 60) * 0.008,
            "glow": 0.1,
            "shake": 0.2,
            "particles": 0,
            "label": "  gathering",
            "delay": 0.30,
        },
        # Phase 3: TENSION — vibration intensifies
        {
            "angle": angle_offset + math.sin(0.7 * 60) * 0.025,
            "glow": 0.4,
            "shake": 0.8,
            "particles": 0,
            "label": "  tension...",
            "delay": 0.25,
        },
        # Phase 4: PEAK TENSION — about to break
        {
            "angle": angle_offset + math.sin(1.1 * 60) * 0.04,
            "glow": 0.75,
            "shake": 1.5,
            "particles": 6,
            "label": "  ━━ TENSION ━━",
            "delay": 0.15,
        },
        # Phase 5: SNAP — the wheel turns
        {
            "angle": angle_offset + turn * 0.85,
            "glow": 1.0,
            "shake": 2.0,
            "particles": 30,
            "label": "  ━━━━ S N A P ━━━━",
            "delay": 0.12,
        },
        # Phase 6: OVERSHOOT — momentum carries past
        {
            "angle": angle_offset + turn * 1.05,
            "glow": 1.0,
            "shake": 1.2,
            "particles": 20,
            "label": "  ▸▸ IMPACT ◂◂",
            "delay": 0.10,
        },
        # Phase 7: SETTLE — oscillation dampens
        {
            "angle": angle_offset + turn + math.sin(math.pi * 1.5) * 0.006,
            "glow": 0.5,
            "shake": 0.3,
            "particles": 5,
            "label": "  settling...",
            "delay": 0.30,
        },
        # Phase 8: RESOLVED — the wheel has turned
        {
            "angle": angle_offset + turn,
            "glow": 0.15,
            "shake": 0.0,
            "particles": 0,
            "label": "",
            "delay": 0.0,
        },
    ]

    # ── Output keyframes ────────────────────────────────────────────────

    total_frames = len(keyframes)
    sep_thin = "  · · ·"
    sep_heavy = "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    for i, kf in enumerate(keyframes):
        shake = kf["shake"]
        sx = (random.random() - 0.5) * shake if shake > 0 else 0.0
        sy = (random.random() - 0.5) * shake if shake > 0 else 0.0

        particles = (
            _spray_particles(kf["particles"], 20, 30)
            if kf["particles"] > 0
            else None
        )

        frame = _render_frame(kf["angle"], kf["glow"], sx, sy, particles)

        if i == 0:
            # Opening frame — boxed
            out(_box(frame, "☸ MAHORAGA"))
            out("\n")
        elif i == total_frames - 1:
            # Final frame — heavy separator + boxed
            out("\n")
            out(sep_heavy)
            out("\n\n")
            out(_box(frame, f"☸ {text} ☸"))
            out("\n")
        else:
            # Mid-animation frames — thin separator + label
            out("\n")
            out(sep_thin if kf["glow"] < 0.8 else sep_heavy)
            out("\n\n")
            out(frame)
            out("\n")
            if kf["label"]:
                out(kf["label"])
                out("\n")

        flush()

        if kf["delay"] > 0:
            time.sleep(kf["delay"])


def render_single(angle: float = 0.0, label: str = "") -> str:
    """Render a single static frame. Returns string, no side effects."""
    _build_model()
    frame = _render_frame(angle)
    if label:
        return _box(frame, label)
    return frame


# ── CLI entry ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    import argparse

    parser = argparse.ArgumentParser(description="Mahoraga Dharma Wheel — terminal animation")
    parser.add_argument("--text", default="A D A P T E D", help="Text shown after adaptation")
    parser.add_argument("--angle", type=float, default=0.0, help="Starting angle in radians")
    parser.add_argument("--static", action="store_true", help="Render single static frame")
    parser.add_argument("--frames", action="store_true", help="Render all 8 keyframes at once (no delays)")
    args = parser.parse_args()

    if args.static:
        print(render_single(args.angle, f"☸ MAHORAGA ─── {args.text}"))
    elif args.frames:
        # Dump all frames instantly for piping/testing
        _build_model()
        turn = math.pi / 4
        angles = [
            args.angle,
            args.angle + 0.008,
            args.angle + 0.025,
            args.angle + 0.04,
            args.angle + turn * 0.85,
            args.angle + turn * 1.05,
            args.angle + turn + 0.006,
            args.angle + turn,
        ]
        for i, a in enumerate(angles):
            print(_render_frame(a, glow=0.0 if i in (0, 7) else 0.5))
            print()
    else:
        play(text=args.text, angle_offset=args.angle)
