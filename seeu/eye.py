from math import hypot

from rich.text import Text

LID = "#22d3ee"
RING = "#67e8f9"
IRIS = "#0e7490"
GLINT = "#ecfeff"


def pixels(w=38, h=16, look=0.0, lid=0.0, glint=1.0):
    cx, cy = w / 2, h / 2
    a, full = w / 2 - 0.5, h / 2 - 0.6
    if lid >= 0.95:
        grid = []
        for py in range(h):
            row = []
            for px in range(w):
                t = (px + 0.5 - cx) / a
                row.append(LID if abs(t) <= 1 and abs(py + 0.5 - (cy + 0.9 * (1 - t * t))) < 0.9 else None)
            grid.append(row)
        return grid
    b = max(0.7, full * (1 - lid))
    r = (a * a + b * b) / (2 * b)
    up, lo = cy - b + r, cy + b - r
    ri, rp = full * 0.95, full * 0.45
    ix = cx + look
    gx, gy = ix + rp * 0.8, cy - rp * 0.8
    grid = []
    for py in range(h):
        row = []
        for px in range(w):
            x, y = px + 0.5, py + 0.5
            du, dl = hypot(x - cx, y - up), hypot(x - cx, y - lo)
            d = hypot(x - ix, y - cy)
            if du > r or dl > r:
                row.append(None)
            elif du > r - 1.6 or dl > r - 1.6:
                row.append(LID)
            elif hypot(x - gx, y - gy) < 1.2 * glint:
                row.append(GLINT)
            elif d < rp:
                row.append(None)
            elif d < ri - 1.1:
                row.append(IRIS)
            elif d < ri:
                row.append(RING)
            else:
                row.append(None)
        grid.append(row)
    return grid


def eye(w=38, h=16, **pose):
    g = pixels(w, h, **pose)
    text = Text()
    for y in range(0, h, 2):
        for x in range(w):
            top, bot = g[y][x], g[y + 1][x]
            if top is None and bot is None:
                text.append(" ")
            elif top == bot:
                text.append("█", style=top)
            elif bot is None:
                text.append("▀", style=top)
            elif top is None:
                text.append("▄", style=bot)
            else:
                text.append("▀", style=f"{top} on {bot}")
        if y + 2 < h:
            text.append("\n")
    return text


def blink():
    return [dict(lid=v) for v in (0.4, 0.85, 1.0, 0.85, 0.4)] + [{}], 0.04


def glance(side):
    move = [dict(look=side * v) for v in (0.7, 1.4, 2.0)]
    return move + [dict(look=side * 2.0)] * 12 + move[1::-1] + [{}], 0.08


def shimmer():
    return [dict(glint=v) for v in (1.4, 1.8, 2.1, 1.8, 1.4)] + [{}], 0.07


if __name__ == "__main__":
    for pose in ({}, dict(lid=1.0), dict(lid=0.5), dict(look=2.0), dict(glint=2.1)):
        print(pose)
        print(eye(**pose).plain)
