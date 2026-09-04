"""Braille-cell plotting on a terminal.

A terminal cell can hold one braille glyph, which is a 4x2 grid of dots, so a
canvas of W x H characters gives 2W x 4H addressable points. That is enough
resolution for residual curves without any graphics library.
"""

from __future__ import annotations

import math

# Dot bit values for a braille cell, indexed [row][col] over 4 rows x 2 cols.
BRAILLE = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
]

BRAILLE_BASE = 0x2800

# ANSI colours cycled through for successive series.
COLORS = [
    "\033[38;5;51m",   # cyan
    "\033[38;5;214m",  # orange
    "\033[38;5;46m",   # green
    "\033[38;5;177m",  # violet
    "\033[38;5;203m",  # red
    "\033[38;5;227m",  # yellow
]
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


class Canvas:
    """A braille drawing surface of `width` x `height` terminal cells."""

    def __init__(self, width: int, height: int):
        self.width = max(1, width)
        self.height = max(1, height)
        self.dots_w = self.width * 2
        self.dots_h = self.height * 4
        # each cell holds [dot bitmask, index of last series to touch it]
        self.cells = [[[0, -1] for _ in range(self.width)]
                      for _ in range(self.height)]

    def set(self, dx: int, dy: int, series: int = 0) -> None:
        if not (0 <= dx < self.dots_w and 0 <= dy < self.dots_h):
            return
        cy, cx = dy // 4, dx // 2
        self.cells[cy][cx][0] |= BRAILLE[dy % 4][dx % 2]
        self.cells[cy][cx][1] = series

    def vline(self, dx: int, dy0: int, dy1: int, series: int = 0) -> None:
        """Vertical run of dots, used to join consecutive samples."""
        if dy0 > dy1:
            dy0, dy1 = dy1, dy0
        for dy in range(dy0, dy1 + 1):
            self.set(dx, dy, series)

    def rows(self) -> list[list[tuple[str, int]]]:
        out = []
        for row in self.cells:
            out.append([
                (chr(BRAILLE_BASE + mask) if mask else " ", si)
                for mask, si in row
            ])
        return out


def nice_bounds(lo: float, hi: float, pad_frac: float = 0.05):
    """Pad a range slightly and guard against a zero-width span."""
    if not math.isfinite(lo) or not math.isfinite(hi):
        return 0.0, 1.0
    if hi - lo < 1e-12:
        hi = lo + 1.0
    pad = (hi - lo) * pad_frac
    return lo - pad, hi + pad


def plot_series(series, width, height, ylim=None):
    """Draw named y-series onto a canvas.

    series: sequence of (label, values). All share an implicit x index.
    ylim:   optional (lo, hi) to pin the vertical range.
    Returns (canvas, ymin, ymax).
    """
    canvas = Canvas(width, height)

    finite = [v for _, ys in series for v in ys if math.isfinite(v)]
    if not finite:
        return canvas, 0.0, 1.0

    if ylim is not None:
        ymin, ymax = ylim
    else:
        ymin, ymax = nice_bounds(min(finite), max(finite))
    span = ymax - ymin or 1.0

    for si, (_, ys) in enumerate(series):
        n = len(ys)
        if n == 0:
            continue
        prev = None
        for dx in range(canvas.dots_w):
            idx = int(round(dx * (n - 1) / max(canvas.dots_w - 1, 1)))
            y = ys[idx]
            if not math.isfinite(y):
                prev = None
                continue
            frac = (y - ymin) / span
            dy = int(round((1.0 - frac) * (canvas.dots_h - 1)))
            dy = max(0, min(canvas.dots_h - 1, dy))
            if prev is not None and abs(dy - prev) > 1:
                canvas.vline(dx, prev, dy, si)
            else:
                canvas.set(dx, dy, si)
            prev = dy

    return canvas, ymin, ymax


def frame(series, x_values, width, height, title="", ylim=None,
          annotations=()):
    """Compose a full plot into a list of printable lines."""
    canvas, ymin, ymax = plot_series(series, width, height, ylim)
    labw = 9
    lines = []

    if title:
        lines.append(f"{DIM}{title}{RESET}")

    rows = canvas.rows()
    mid = len(rows) // 2
    for i, row in enumerate(rows):
        if i == 0:
            lab = f"{ymax:8.2f} "
        elif i == len(rows) - 1:
            lab = f"{ymin:8.2f} "
        elif i == mid:
            lab = f"{(ymin + ymax) / 2:8.2f} "
        else:
            lab = " " * labw
        body = "".join(
            (COLORS[si % len(COLORS)] + ch + RESET) if si >= 0 else ch
            for ch, si in row
        )
        lines.append(f"{DIM}{lab}{RESET}\u2502{body}")

    lines.append(" " * labw + "\u2514" + "\u2500" * canvas.width)

    lo = x_values[0] if x_values else 0
    hi = x_values[-1] if x_values else 0
    axis = str(lo)
    gap = max(1, canvas.width - len(axis) - len(str(hi)))
    axis += " " * gap + str(hi)
    lines.append(" " * (labw + 1) + f"{DIM}{axis[:canvas.width]}{RESET}")

    legend = "  ".join(
        f"{COLORS[i % len(COLORS)]}\u2588{RESET} {lab} {ys[-1]:+.3f}"
        for i, (lab, ys) in enumerate(series) if ys
    )
    lines.append(" " * (labw + 1) + legend)

    for note in annotations:
        lines.append(" " * (labw + 1) + note)

    return lines
