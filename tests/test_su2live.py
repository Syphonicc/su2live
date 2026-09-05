"""Regression tests. Run with `python -m pytest` or `python tests/test_su2live.py`.

The classification cases are drawn from real behaviour seen on SU2 test cases:
a clean convergence, a residual frozen to ten significant figures at too high a
CFL, an adaptive CFL limit cycle, a diverging preconditioner, and a case that
converges honestly but very slowly.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from su2live.analysis import analyse, slope  # noqa: E402
from su2live.parser import History  # noqa: E402

STEADY = list(range(3000))


def exp_decay(n=3000, tau=1800, floor=-12.0, start=-2.0):
    return [start + (floor - start) * (1 - math.exp(-i / tau))
            for i in range(n)]


# ------------------------------------------------------------- classification

def test_converging():
    assert analyse(STEADY, exp_decay(), -12).status == "converging"


def test_slow_but_converging():
    ys = [-6 - 4 * (1 - math.exp(-i / 28000)) for i in STEADY]
    d = analyse(STEADY, ys, -12)
    assert d.status == "converging"
    assert d.eta and d.eta > 10000       # honest about how far off it is


def test_frozen():
    ys = [-7.580046571 if i > 800 else -2 - 6 * (1 - math.exp(-i / 300))
          for i in STEADY]
    assert analyse(STEADY, ys, -12).status == "frozen"


def test_slow_limit_cycle():
    ys = [-9 + 1.4 * math.sin(i / 260) for i in STEADY]
    assert analyse(STEADY, ys, -12).status == "limit cycle"


def test_fast_limit_cycle():
    ys = [-9 + 0.5 * math.sin(i / 40) for i in STEADY]
    assert analyse(STEADY, ys, -12).status == "limit cycle"


def test_diverging():
    ys = [-5 + 0.004 * i if i > 500 else -5 for i in STEADY]
    assert analyse(STEADY, ys, -12).status == "diverging"


def test_stalled():
    ys = [-8.0 + 0.001 * math.sin(i / 50) for i in STEADY]
    assert analyse(STEADY, ys, -12).status == "stalled"


def test_nan_is_not_converging():
    """A blown up run writes nan; float('nan') parses fine and must not
    fall through the numeric comparisons into a converging verdict."""
    ys = exp_decay(1200, tau=300, floor=-5) + [float("nan")] * 1800
    assert analyse(STEADY, ys, -12).status == "diverging"


def test_nan_early_then_finite():
    ys = [float("nan")] * 50 + exp_decay(2950)
    assert analyse(STEADY, ys, -12).status == "converging"


# --------------------------------------------------------------- unsteady x

def test_constant_iteration_column():
    """Unsteady runs writing one row per time step leave Inner_Iter constant,
    which gives a zero denominator in the slope fit."""
    ys = [-2 - 6 * (1 - math.exp(-i / 400)) for i in range(2000)]
    assert analyse([0] * 2000, ys, -12).status == "converging"


def test_sawtooth_iteration_column():
    """Inner iterations reset each time step, so x is a sawtooth."""
    xs, ys = [], []
    for t in range(200):
        for k in range(20):
            xs.append(k)
            ys.append(-2 - 6 * (1 - math.exp(-(t * 20 + k) / 900)))
    d = analyse(xs, ys, -12)
    assert d.status == "converging"
    assert d.rate < 0


def test_slope_handles_degenerate_x():
    assert slope([1, 1, 1], [1, 2, 3]) == 0.0


# ------------------------------------------------------------------- parsing

HEADER = ('"Time_Iter","Outer_Iter","Inner_Iter",    "rms[Rho]"    ,'
          '    "rms[RhoU]"   ,    "rms[RhoV]"   ,    "rms[RhoE]"    ')


def write_history(rows, unsteady=False):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as f:
        f.write(HEADER + "\n")
        for i in range(rows):
            t, k = (i // 15, i % 15) if unsteady else (0, i)
            b = -2 - 7 * (1 - math.exp(-i / 500))
            f.write("%12d,           0,%12d,%18.9f,%18.9f,%18.9f,%18.9f\n"
                    % (t, k, b, b + 2, b + 1.5, b + 3.5))
    return path


def test_reads_columns():
    path = write_history(100)
    try:
        h = History(path)
        h.poll()
        assert h.available() == ["Rho", "RhoU", "RhoV", "RhoE"]
        assert len(h.iters) == 100
    finally:
        os.unlink(path)


def test_unsteady_axis_is_monotonic():
    path = write_history(300, unsteady=True)
    try:
        h = History(path)
        h.poll()
        assert all(b >= a for a, b in zip(h.iters, h.iters[1:])), \
            "x axis must not go backwards on unsteady runs"
        assert len(set(h.iters)) > 1, "x axis must not be constant"
    finally:
        os.unlink(path)


def test_incremental_read():
    """Rows appended after the first poll are picked up, and not re-read."""
    path = write_history(50)
    try:
        h = History(path)
        h.poll()
        first = len(h.iters)
        with open(path, "a") as f:
            f.write("           0,           0,          99,"
                    "       -9.000000000,       -7.000000000,"
                    "       -7.500000000,       -5.500000000\n")
        h.poll()
        assert len(h.iters) == first + 1
        h.poll()
        assert len(h.iters) == first + 1, "second poll must not duplicate"
    finally:
        os.unlink(path)


def test_selection():
    path = write_history(50)
    try:
        h = History(path)
        h.poll()
        picked = h.select(["Rho", "RhoE"])
        assert [lab for lab, _ in picked] == ["Rho", "RhoE"]
        assert [lab for lab, _ in h.select(["nonsense"])] == h.available()
    finally:
        os.unlink(path)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print()
    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
