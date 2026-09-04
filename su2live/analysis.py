"""Reading meaning out of a residual history.

Watching a number scroll past tells you less than it should. These helpers
answer the questions you actually ask while a case runs: is it converging, how
fast, when will it finish, and if it is not converging, what is it doing
instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Status labels
CONVERGING = "converging"
STALLED = "stalled"
DIVERGING = "diverging"
CYCLING = "limit cycle"
FROZEN = "frozen"
UNKNOWN = "too early"

STATUS_COLOR = {
    CONVERGING: "\033[38;5;46m",
    STALLED: "\033[38;5;214m",
    DIVERGING: "\033[38;5;203m",
    CYCLING: "\033[38;5;214m",
    FROZEN: "\033[38;5;203m",
    UNKNOWN: "\033[2m",
}
RESET = "\033[0m"


@dataclass
class Diagnosis:
    status: str
    rate: float           # decades per 1000 iterations (negative = improving)
    eta: int | None       # iterations to target, None if not applicable
    note: str = ""

    @property
    def color(self) -> str:
        return STATUS_COLOR.get(self.status, "")


def slope(xs, ys) -> float:
    """Least squares slope of ys against xs."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def analyse(iters, values, target=None, window=None) -> Diagnosis:
    """Classify recent behaviour of one residual series."""
    n = len(values)
    if n < 20:
        return Diagnosis(UNKNOWN, 0.0, None)

    window = window or max(20, min(n // 3, 2000))
    xs = iters[-window:]
    ys = values[-window:]

    span = max(ys) - min(ys)

    # Identical to floating point precision: the solution has stopped moving.
    if span < 1e-9:
        return Diagnosis(FROZEN, 0.0, None,
                         "residual identical over "
                         f"{len(ys)} iterations")

    m = slope(xs, ys) * 1000.0     # decades per 1000 iterations

    # An oscillation crosses its own mean repeatedly while the endpoints stay
    # at a similar level. Check that before trusting the sign of the slope:
    # a window landing on one limb of a cycle looks like a clean trend.
    mean_level = sum(ys) / len(ys)
    crossings = sum(
        1 for a, b in zip(ys, ys[1:])
        if (a - mean_level) * (b - mean_level) < 0
    )
    net = abs(ys[-1] - ys[0])
    if crossings >= 4 and span > 0.15 and net < span * 0.6:
        return Diagnosis(CYCLING, m, None,
                         f"oscillating +/-{span / 2:.2f} about {mean_level:.2f}")

    # A slow oscillation can fill the whole window with one limb, which looks
    # like a clean trend. Compare against the longer history: if the residual
    # has been wandering inside a band it has not entered before, and has made
    # no net progress over that band, it is cycling rather than trending.
    if n >= window * 2:
        long_ys = values[-window * 3:]
        long_span = max(long_ys) - min(long_ys)
        long_net = abs(long_ys[-1] - long_ys[0])
        if long_span > 0.3 and long_net < long_span * 0.35:
            level = sum(long_ys) / len(long_ys)
            return Diagnosis(
                CYCLING, m, None,
                f"oscillating +/-{long_span / 2:.2f} about {level:.2f}")

    if m > 0.01:
        return Diagnosis(DIVERGING, m, None, "residual increasing")

    if abs(m) < 0.005:
        return Diagnosis(STALLED, m, None, "no significant progress")

    eta = None
    if target is not None and m < 0:
        remaining = values[-1] - target
        if remaining > 0:
            eta = int(remaining / (-m) * 1000.0)

    return Diagnosis(CONVERGING, m, eta)


def summary_line(diag: Diagnosis, current: float, target=None) -> str:
    """One line of status suitable for printing under the plot."""
    bits = [f"{diag.color}{diag.status}{RESET}"]
    if diag.rate:
        bits.append(f"{diag.rate:+.3f} dec/1k")
    if diag.eta is not None:
        bits.append(f"~{diag.eta:,} iters to {target:g}")
    if diag.note:
        bits.append(f"\033[2m{diag.note}{RESET}")
    return "  ".join(bits)


def fmt_eta_seconds(iters_left: int, seconds_per_iter: float) -> str:
    if not seconds_per_iter or iters_left <= 0:
        return ""
    total = iters_left * seconds_per_iter
    if total < 90:
        return f"~{total:.0f}s"
    if total < 5400:
        return f"~{total / 60:.0f}m"
    return f"~{total / 3600:.1f}h"
