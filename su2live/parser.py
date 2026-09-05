"""Reading SU2 history files and configs.

SU2 writes a CSV history file while it runs. The header is quoted and space
padded, and which columns exist depends on the solver: Euler gives four rms
columns, SA five, SST six, and CFL columns appear only when requested through
HISTORY_OUTPUT. So the column set is discovered rather than assumed.
"""

from __future__ import annotations

import os
import re


class History:
    """Incrementally reads a history CSV, keeping only what it needs."""

    def __init__(self, path: str):
        self.path = path
        self.pos = 0
        self.leftover = ""
        self.header: list[str] | None = None
        self.keep: list[tuple[int, str]] = []
        self.iters: list[int] = []
        self.cols: dict[str, list[float]] = {}
        self.iter_index = 2       # Inner_Iter, corrected once a header is seen
        self.iter_name = "Inner_Iter"
        self.candidates: dict[str, int] = {}
        self.alt: dict[str, list[int]] = {}
        self._x_checked = False

    # -------------------------------------------------------------- header

    @staticmethod
    def _split_header(line: str) -> list[str]:
        return [c.strip().strip('"').strip() for c in line.split(",")]

    def _adopt_header(self, fields: list[str]) -> None:
        self.header = fields
        self.keep = [
            (i, name) for i, name in enumerate(fields)
            if name.startswith("rms[") or name.startswith("max[")
        ]
        for _, name in self.keep:
            self.cols.setdefault(name, [])
        # Inner_Iter restarts every physical time step, so for unsteady runs
        # it is not a usable x axis. Prefer Time_Iter when the run is unsteady;
        # detected below once we have seen enough rows to tell.
        self.candidates = {
            name: fields.index(name)
            for name in ("Inner_Iter", "Time_Iter", "Outer_Iter")
            if name in fields
        }
        for cand in ("Inner_Iter", "Time_Iter", "Outer_Iter"):
            if cand in self.candidates:
                self.iter_index = self.candidates[cand]
                self.iter_name = cand
                break
        self._x_checked = False

    # ---------------------------------------------------------------- read

    def poll(self) -> int:
        """Read any new rows. Returns how many were added."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return 0

        if size < self.pos:            # file replaced or truncated
            self.reset()

        try:
            with open(self.path, "r") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return 0

        if not chunk:
            return 0

        lines = (self.leftover + chunk).split("\n")
        self.leftover = lines.pop()    # possibly partial trailing line

        added = 0
        for line in lines:
            if not line.strip():
                continue
            if '"' in line and ("rms[" in line or "Inner_Iter" in line):
                self._adopt_header(self._split_header(line))
                continue
            if self.header is None:
                continue
            if self._append_row(line):
                added += 1
        if added:
            self._maybe_switch_axis()
        return added

    def _append_row(self, line: str) -> bool:
        parts = [p.strip() for p in line.split(",")]
        try:
            it = int(float(parts[self.iter_index]))
        except (IndexError, ValueError):
            return False
        values = {}
        for idx, name in self.keep:
            try:
                values[name] = float(parts[idx])
            except (IndexError, ValueError):
                return False
        self.iters.append(len(self.iters) if self.iter_name == "row" else it)
        # Track the other counter columns until we know the axis is sound,
        # so a switch does not need to reread the file.
        if not self._x_checked:
            for name, idx in self.candidates.items():
                try:
                    self.alt.setdefault(name, []).append(int(float(parts[idx])))
                except (IndexError, ValueError):
                    pass
        for name, v in values.items():
            self.cols[name].append(v)
        return True

    def _maybe_switch_axis(self) -> None:
        """Swap to a monotonic counter if the current one repeats.

        Steady runs increment Inner_Iter throughout. Unsteady runs reset it
        each time step, giving a constant column or a sawtooth, so switch to
        Time_Iter, or to the row number if that is no better.
        """
        if self._x_checked or len(self.iters) < 30:
            return
        self._x_checked = True
        if all(b >= a for a, b in zip(self.iters, self.iters[1:])) \
                and len(set(self.iters)) > 1:
            self.alt.clear()
            return                      # already monotonic, nothing to do
        # Rebuild the axis from data already held rather than rereading the
        # file: the read position has moved on and resetting would drop
        # everything collected so far.
        for name in ("Time_Iter", "Outer_Iter"):
            idx = self.candidates.get(name)
            if idx is not None and name != self.iter_name and self.alt:
                col = self.alt.get(name)
                if col and len(set(col)) > 1:
                    self.iter_name = name
                    self.iter_index = idx
                    self.iters[:] = col
                    self.alt.clear()
                    return
        self.iter_name = "row"          # fall back to row number
        self.iters[:] = list(range(len(self.iters)))
        self.alt.clear()

    def reset(self) -> None:
        self.pos = 0
        self.leftover = ""
        self.header = None
        self.keep = []
        self.iters.clear()
        self.cols.clear()
        self.alt.clear()
        self._x_checked = False

    # ------------------------------------------------------------- helpers

    @staticmethod
    def short(name: str) -> str:
        """rms[RhoU] -> RhoU"""
        return name.replace("rms[", "").replace("max[", "").rstrip("]")

    @property
    def residuals(self) -> list[tuple[str, list[float]]]:
        return [(self.short(name), ys) for name, ys in self.cols.items() if ys]

    def available(self) -> list[str]:
        """Short names of every plottable column seen so far."""
        return [self.short(n) for n in self.cols]

    def select(self, wanted: list[str] | None):
        """Filter series by short name, case insensitively.

        Unknown names are ignored rather than raising, because a column may
        simply not have appeared yet when the run is just starting.
        """
        series = self.residuals
        if not wanted:
            return series
        keys = [w.strip().lower() for w in wanted if w.strip()]
        chosen = [(lab, ys) for lab, ys in series if lab.lower() in keys]
        # preserve the order the user asked for
        order = {k: i for i, k in enumerate(keys)}
        chosen.sort(key=lambda p: order.get(p[0].lower(), 99))
        return chosen or series

    def window(self, n: int | None, wanted: list[str] | None = None):
        """Series and x values, optionally filtered and limited to n samples."""
        series = self.select(wanted)
        if not n or n <= 0 or n >= len(self.iters):
            return series, self.iters
        return ([(lab, ys[-n:]) for lab, ys in series], self.iters[-n:])


# ------------------------------------------------------------------ config

def read_option(cfg_path: str, option: str, default=None):
    """Pull a single option value out of an SU2 config file."""
    pattern = re.compile(rf"^\s*{re.escape(option)}\s*=\s*(.+?)\s*$")
    value = default
    try:
        with open(cfg_path) as f:
            for line in f:
                if line.lstrip().startswith("%"):
                    continue
                m = pattern.match(line)
                if m:
                    value = m.group(1).split("%")[0].strip()
    except OSError:
        pass
    return value


def history_path(cfg_path: str, workdir: str | None = None) -> str:
    """Where a config will write its history file."""
    workdir = workdir or os.path.dirname(os.path.abspath(cfg_path)) or "."
    name = read_option(cfg_path, "CONV_FILENAME", "history")
    if not name.endswith(".csv"):
        name += ".csv"
    return os.path.join(workdir, name)


def convergence_target(cfg_path: str):
    raw = read_option(cfg_path, "CONV_RESIDUAL_MINVAL")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
