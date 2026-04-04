"""
Mahoraga Dharma Wheel — Terminal Animation

Braille-rendered 8-spoke Dharma wheel with truecolor ANSI,
screen shake, particle glow, and snap rotation animation.

Plays in the terminal when Mahoraga adapts to a new pattern.
The wheel turns 1/8th rotation per adaptation — visual proof
that the system learned something.

Original design by the user. Integrated into Mendicant Bias.
"""

import math
import time
import sys
import os
import random

# --- Configurations ---
PW, PH = 96, 96
CW, CH = PW // 2, PH // 4
PCX, PCY = PW / 2.0, PH / 2.0
BRAILLE_BASE = 0x2800

DOT_MAP = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80]
]

# --- Build the Flawless Math Model ---
base_pixels = []

def _add_dot(x, y):
    base_pixels.append((x, y))

def _build_model():
    """Build the wheel geometry once."""
    if base_pixels:
        return

    # 1. Inner and Outer Circles
    for y in range(-48, 48):
        for x in range(-48, 48):
            px, py = x + 0.5, y + 0.5
            dist = math.sqrt(px * px + py * py)
            if (6 <= dist <= 10) or (25 <= dist <= 29):
                _add_dot(x, y)

    # 2. Spokes (Thickened and Straightened)
    for r in range(10, 37):
        _add_dot(-1, -r); _add_dot(0, -r)
        _add_dot(-1, r - 1); _add_dot(0, r - 1)
        _add_dot(r - 1, -1); _add_dot(r - 1, 0)
        _add_dot(-r, -1); _add_dot(-r, 0)

    o_start = int(10 * 0.7071)
    o_end = int(37 * 0.7071)
    for o in range(o_start, o_end + 1):
        _add_dot(o, -o); _add_dot(o - 1, -o); _add_dot(o, -o - 1); _add_dot(o - 1, -o - 1)
        _add_dot(o, o); _add_dot(o - 1, o); _add_dot(o, o - 1); _add_dot(o - 1, o - 1)
        _add_dot(-o, -o); _add_dot(-o - 1, -o); _add_dot(-o, -o - 1); _add_dot(-o - 1, -o - 1)
        _add_dot(-o, o); _add_dot(-o - 1, o); _add_dot(-o, o - 1); _add_dot(-o - 1, o - 1)

    # 3. Knobs
    center_dist = 40
    radius = 4.5
    diag_dist = center_dist * 0.7071
    centers = [
        (0, -center_dist), (0, center_dist),
        (center_dist, 0), (-center_dist, 0),
        (diag_dist, -diag_dist), (diag_dist, diag_dist),
        (-diag_dist, -diag_dist), (-diag_dist, diag_dist)
    ]
    for cx, cy in centers:
        for y in range(-48, 48):
            for x in range(-48, 48):
                px, py = x + 0.5, y + 0.5
                if math.sqrt((px - cx) ** 2 + (py - cy) ** 2) <= radius:
                    _add_dot(x, y)


# --- Renderer ---

def _move_cursor_home():
    sys.stdout.write('\033[H')
    sys.stdout.flush()


def _draw_frame(angle, glow, shake_x, shake_y, show_adapted, adapted_alpha, adapted_text="ADAPTED"):
    pixels = [0.0] * (PW * PH)
    colors = [{'r': 0, 'g': 0, 'b': 0, 'n': 0} for _ in range(CW * CH)]

    sx, sy = shake_x * 2, shake_y * 2

    def set_pixel(x, y, brightness, r, g, b):
        px, py = int(round(x + sx)), int(round(y + sy))
        if 0 <= px < PW and 0 <= py < PH:
            idx = py * PW + px
            if brightness > pixels[idx]:
                pixels[idx] = brightness
            cx_cell, cy_cell = px // 2, py // 4
            ci = cy_cell * CW + cx_cell
            if 0 <= ci < len(colors):
                colors[ci]['r'] += r * brightness
                colors[ci]['g'] += g * brightness
                colors[ci]['b'] += b * brightness
                colors[ci]['n'] += brightness

    def draw_circle(cx, cy, rad, thickness, brightness, r, g, b):
        circ = 2 * math.pi * rad
        steps = max(1, int(math.ceil(circ * 2)))
        for i in range(steps):
            a = (i / steps) * math.pi * 2
            px, py = cx + rad * math.cos(a), cy + rad * math.sin(a)
            half_t = thickness / 2.0
            nx, ny = math.cos(a), math.sin(a)
            w = -half_t
            while w <= half_t:
                set_pixel(px + nx * w, py + ny * w, brightness, r, g, b)
                w += 0.5

    # Base colors
    bR = 160 + glow * 70
    bG = 140 + glow * 50
    bB = 90 + glow * 90
    glR = 140 + glow * 80
    glG = 100 + glow * 60
    glB = 200 + glow * 55

    if glow > 0.15:
        draw_circle(PCX, PCY, 35, 2, glow * 0.3, glR, glG, glB)

    cosA, sinA = math.cos(angle), math.sin(angle)
    for px, py in base_pixels:
        rx = px * cosA - py * sinA
        ry = px * sinA + py * cosA
        set_pixel(PCX + rx, PCY + ry, 0.95 + glow * 0.05, bR, bG, bB)

    if glow > 0.25:
        draw_circle(PCX, PCY, 9, 1.5, glow * 0.35, glR, glG, glB)
        for i in range(8):
            a = angle + i * math.pi / 4
            bx = PCX + 40 * math.cos(a)
            by = PCY + 40 * math.sin(a)
            draw_circle(bx, by, 6.5, 1, glow * 0.35, glR, glG, glB)

    # Compile Braille Frame
    output = []
    for cy in range(CH):
        row_str = []
        for cx in range(CW):
            braille = 0
            for dy in range(4):
                for dx in range(2):
                    px_pos = cx * 2 + dx
                    py_pos = cy * 4 + dy
                    if px_pos < PW and py_pos < PH and pixels[py_pos * PW + px_pos] > 0.25:
                        braille |= DOT_MAP[dy][dx]

            if braille == 0:
                row_str.append(' ')
            else:
                ci = cy * CW + cx
                col = colors[ci]
                r, g, b = 100, 100, 100
                if col['n'] > 0:
                    r = min(255, int(round(col['r'] / col['n'])))
                    g = min(255, int(round(col['g'] / col['n'])))
                    b = min(255, int(round(col['b'] / col['n'])))
                char = chr(BRAILLE_BASE + braille)
                row_str.append(f"\033[38;2;{r};{g};{b}m{char}\033[0m")
        output.append("".join(row_str))

    if show_adapted and adapted_alpha > 0:
        text = f'\u25b8 {adapted_text} \u25c2'
        aR = min(255, int(180 + adapted_alpha * 40))
        aG = min(255, int(140 + adapted_alpha * 30))
        pad = max(0, (CW - len(text)) // 2)
        output.append('\n' + ' ' * pad + f"\033[38;2;{aR};{aG};255m{text}\033[0m")

    _move_cursor_home()
    sys.stdout.write("\n".join(output) + "\n")
    sys.stdout.flush()


# --- Animation ---

# Track cumulative angle across adaptations
_current_angle = 0.0


def play_adaptation(text: str = "A D A P T E D", blocking: bool = True):
    """
    Play the Mahoraga wheel adaptation animation.

    The wheel turns 1/8th rotation each time, accumulating across
    calls — visual proof of how many times the system has adapted.

    Parameters
    ----------
    text : str
        Text to display below the wheel after adaptation.
    blocking : bool
        If True, blocks until animation completes (~3 seconds).
    """
    global _current_angle

    _build_model()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    start_angle = _current_angle
    turn = math.pi / 4

    TENSION = 1.3
    SNAP = 0.25
    IMPACT = 0.12
    SETTLE = 0.45
    COOL = 0.9
    TOTAL = TENSION + SNAP + IMPACT + SETTLE + COOL

    # Clear screen
    sys.stdout.write('\033[2J\033[H')
    sys.stdout.flush()

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= TOTAL:
            break

        angle = start_angle
        glow = 0
        sx, sy = 0.0, 0.0
        showA = False
        aAlpha = 0.0

        if elapsed < TENSION:
            tp = elapsed / TENSION
            freq = 6 + tp * 45
            amp = (tp ** 3) * 0.045
            angle = start_angle + math.sin(elapsed * freq * 10) * amp
            glow = (tp ** 3) * 0.85
            sh = (tp ** 2) * 1.5
            sx = (random.random() - 0.5) * sh
            sy = (random.random() - 0.5) * sh

        elif elapsed < TENSION + SNAP:
            sp = (elapsed - TENSION) / SNAP
            e = 1 - (1 - sp) ** 5
            ov = e * 1.07 - 0.07 * (sp ** 4)
            angle = start_angle + turn * min(ov, 1.05)
            glow = 1.0
            sh = (1 - sp) * 3
            sx = (random.random() - 0.5) * sh
            sy = (random.random() - 0.5) * sh

        elif elapsed < TENSION + SNAP + IMPACT:
            ip = (elapsed - TENSION - SNAP) / IMPACT
            angle = start_angle + turn * (1 + 0.05 * (1 - ip))
            glow = 1.0 - ip * 0.15
            sh = (1 - ip) * 2
            sx = (random.random() - 0.5) * sh
            sy = (random.random() - 0.5) * sh

        elif elapsed < TENSION + SNAP + IMPACT + SETTLE:
            setp = (elapsed - TENSION - SNAP - IMPACT) / SETTLE
            angle = start_angle + turn + math.sin(setp * math.pi * 2.5) * 0.006 * (1 - setp)
            glow = 0.85 * (1 - setp * 0.5)
            if setp > 0.15:
                showA = True
                aAlpha = min((setp - 0.15) / 0.3, 1)

        else:
            cp = (elapsed - TENSION - SNAP - IMPACT - SETTLE) / COOL
            angle = start_angle + turn
            glow = 0.42 * (1 - cp)
            if cp < 0.6:
                showA = True
                aAlpha = max(0, 1 - cp / 0.6)

        _draw_frame(angle, glow, sx, sy, showA, aAlpha, text)
        time.sleep(1 / 60)

    _current_angle = start_angle + turn

    # Final clean frame
    _draw_frame(_current_angle, 0, 0, 0, False, 0)


def draw_static(angle: float | None = None):
    """Draw a single static frame of the wheel (no animation)."""
    _build_model()
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = angle if angle is not None else _current_angle
    _draw_frame(a, 0, 0, 0, False, 0)


if __name__ == "__main__":
    try:
        _build_model()
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()
        while True:
            draw_static(_current_angle)
            input("\n[ Press Enter to Adapt ]")
            play_adaptation()
    except KeyboardInterrupt:
        sys.stdout.write('\033[2J\033[H')
        print("Done.")
