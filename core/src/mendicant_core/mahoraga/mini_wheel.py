"""
Mahoraga Mini Wheel — compact Braille wheel for inline display.

Renders a small dharma wheel (~12 lines x ~20 chars) that fits
inside an MCP tool result or conversation output. No animation,
just a static frame with optional text.
"""

import math

PW, PH = 40, 40
CW, CH = PW // 2, PH // 4
PCX, PCY = PW / 2.0, PH / 2.0
BRAILLE_BASE = 0x2800
DOT_MAP = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]


def _build_mini_model() -> list[tuple[int, int]]:
    """Build a smaller wheel geometry."""
    pixels = []

    # Circles
    for y in range(-20, 20):
        for x in range(-20, 20):
            dist = math.sqrt((x + 0.5) ** 2 + (y + 0.5) ** 2)
            if (2.5 <= dist <= 4.5) or (10 <= dist <= 12):
                pixels.append((x, y))

    # Spokes
    for r in range(4, 16):
        pixels.append((-1, -r)); pixels.append((0, -r))
        pixels.append((-1, r - 1)); pixels.append((0, r - 1))
        pixels.append((r - 1, -1)); pixels.append((r - 1, 0))
        pixels.append((-r, -1)); pixels.append((-r, 0))

    o_s = int(4 * 0.7071)
    o_e = int(16 * 0.7071)
    for o in range(o_s, o_e + 1):
        for sx, sy in [(1, -1), (1, 1), (-1, -1), (-1, 1)]:
            pixels.append((sx * o, sy * o))
            pixels.append((sx * o - 1, sy * o))

    # Knobs
    for i in range(8):
        a = i * math.pi / 4
        cx, cy = 16 * math.cos(a), 16 * math.sin(a)
        for y in range(-20, 20):
            for x in range(-20, 20):
                if math.sqrt((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) <= 2:
                    pixels.append((x, y))

    return pixels


_MINI_PIXELS: list[tuple[int, int]] | None = None


def render_mini_wheel(angle: float = 0.0) -> str:
    """Render a small static Braille wheel. Returns multi-line string."""
    global _MINI_PIXELS
    if _MINI_PIXELS is None:
        _MINI_PIXELS = _build_mini_model()

    grid = [0.0] * (PW * PH)
    cosA, sinA = math.cos(angle), math.sin(angle)

    for px, py in _MINI_PIXELS:
        rx = int(PCX + px * cosA - py * sinA)
        ry = int(PCY + px * sinA + py * cosA)
        if 0 <= rx < PW and 0 <= ry < PH:
            grid[ry * PW + rx] = 1.0

    lines = []
    for cy in range(CH):
        row = []
        for cx in range(CW):
            braille = 0
            for dy in range(4):
                for dx in range(2):
                    px = cx * 2 + dx
                    py = cy * 4 + dy
                    if px < PW and py < PH and grid[py * PW + px] > 0.25:
                        braille |= DOT_MAP[dy][dx]
            row.append(chr(BRAILLE_BASE + braille) if braille else ' ')
        lines.append(''.join(row).rstrip())

    # Trim empty lines from top and bottom
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return '\n'.join(lines)


def render_adaptation_card(action: str, confidence: float = 0.95, angle: float = 0.0) -> str:
    """
    Render a compact adaptation card with wheel + text.

    Returns something like:
        ☸ MAHORAGA ─── ADAPTED
        ⠀⠀⠀⣠⣴⣶⣶⣦⣄⠀⠀⠀
        ⠀⣴⣿⡿⠋⠁⠙⢿⣿⣦⠀
        ⠀⣿⡟⠀⠀⠀⠀⠀⢻⣿⠀
        ⠀⠻⣿⣶⡤⢤⣶⣿⠟⠀
        ⠀⠀⠈⠛⠿⠿⠛⠁⠀⠀⠀
        "always use pytest"
        confidence: 95%
    """
    wheel = render_mini_wheel(angle)
    wheel_lines = wheel.split('\n')

    # Find the widest wheel line
    max_w = max(len(line) for line in wheel_lines) if wheel_lines else 0

    header = f"\u2638 MAHORAGA \u2500\u2500\u2500 ADAPTED"
    footer_action = f'"{action}"'
    footer_conf = f"confidence: {confidence:.0%}"

    parts = [header, wheel, footer_action, footer_conf]
    return '\n'.join(parts)


if __name__ == "__main__":
    print(render_adaptation_card("always use pytest", 0.95))
