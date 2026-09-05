"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

from . import canvas as cv
from . import parser as ps
from . import analysis as an

ALT_ON = "\033[?1049h\033[?25l"
ALT_OFF = "\033[?25h\033[?1049l"
HOME = "\033[H"
CLEAR_EOL = "\033[K"


class Screen:
    """In place terminal output that leaves scrollback alone.

    The frame is drawn where the cursor already is and redrawn by moving the
    cursor back up over it, so everything above stays in the scrollback buffer
    and can still be scrolled through while a run is going.
    """

    def __init__(self, inline=True):
        self.inline = inline
        self.active = False
        self.prev_lines = 0
        self.prev_size = None

    def __enter__(self):
        if self.inline:
            sys.stdout.write("\033[?25l")          # hide cursor only
        else:
            sys.stdout.write(ALT_ON)
        sys.stdout.flush()
        self.active = True
        return self

    def __exit__(self, *exc):
        if self.active:
            if self.inline:
                sys.stdout.write("\033[?25h")      # show cursor
            else:
                sys.stdout.write(ALT_OFF)
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.active = False
        return False

    def draw(self, lines):
        size = shutil.get_terminal_size((100, 30))

        if not self.inline:
            if size != self.prev_size:
                sys.stdout.write("\033[2J")
                self.prev_size = size
            out = [HOME]
            for line in lines[:size.lines]:
                out.append(line + CLEAR_EOL + "\n")
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            return

        # Inline: step back over the previous frame, then rewrite it.
        lines = lines[:max(1, size.lines - 1)]
        out = []
        if self.prev_lines:
            out.append(f"\033[{self.prev_lines}A")
        out.append("\r")
        for line in lines:
            out.append(line + CLEAR_EOL + "\n")
        # If this frame is shorter than the last, wipe the leftover rows.
        extra = self.prev_lines - len(lines)
        if extra > 0:
            out.append((CLEAR_EOL + "\n") * extra)
            out.append(f"\033[{extra}A")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self.prev_lines = len(lines)


class Pace:
    """Measures solver speed from rows seen since monitoring began.

    Attaching to an existing history file must not charge its old rows to the
    time since attach, so the count starts at whatever was already there.
    """

    def __init__(self):
        self.t0 = None
        self.n0 = None
        self.last_n = 0

    def update(self, n_rows: int) -> None:
        if self.n0 is None:
            self.n0 = n_rows
            self.t0 = time.time()
        elif n_rows > self.last_n and self.t0 is None:
            self.t0 = time.time()
        self.last_n = n_rows

    def per_iter(self):
        if self.t0 is None or self.n0 is None:
            return None
        new_rows = self.last_n - self.n0
        elapsed = time.time() - self.t0
        if new_rows < 5 or elapsed < 1.0:
            return None
        return elapsed / new_rows


def build_frame(hist, title, target, tail, window, started, size,
                max_height=None, only=None):
    series, xs = hist.window(window, only)
    if not series:
        return [f"{cv.DIM}{title}{cv.RESET}", "", "  waiting for output..."]

    width = max(20, min(size.columns - 12, 200))
    overhead = 6 + len(tail)
    cap = max_height or 26
    height = max(5, min(size.lines - overhead, cap))

    notes = []
    primary = series[0]
    diag = an.analyse(xs, primary[1], target)
    line = an.summary_line(diag, primary[1][-1], target)

    per_iter = started.per_iter() if isinstance(started, Pace) else None
    if per_iter:
        bits = [f"{1 / per_iter:.0f} it/s"]
        if diag.eta:
            eta_t = an.fmt_eta_seconds(diag.eta, per_iter)
            if eta_t:
                bits.append(eta_t)
        line = f"{line}   {cv.DIM}{'   '.join(bits)}{cv.RESET}"
    notes.append(line)

    head = f"{title}    iter {hist.iters[-1]:,}"
    if target is not None:
        head += f"    target {target:g}"

    lines = cv.frame(series, xs, width, height, head,
                     annotations=notes)
    if tail:
        lines.append("")
        for t in tail:
            lines.append(f"{cv.DIM}{t[:size.columns - 1]}{cv.RESET}")
    return lines


def run_follow(path, interval, window, target, inline=True, height=None,
               only=None):
    hist = ps.History(path)
    pace = Pace()
    max_height = height or (14 if inline else 26)
    with Screen(inline=inline) as screen:
        try:
            last = 0.0
            while True:
                hist.poll()
                pace.update(len(hist.iters))
                now = time.time()
                if now - last >= interval:
                    size = shutil.get_terminal_size((100, 30))
                    screen.draw(build_frame(
                        hist, os.path.basename(path), target, [], window,
                        pace, size, max_height, only))
                    last = now
                time.sleep(min(interval / 3, 0.15))
        except KeyboardInterrupt:
            pass
    report(hist, path, target, only)


def run_wrap(exe, cfg, interval, window, tail_lines, inline=True,
             height=None, only=None):
    workdir = os.path.dirname(os.path.abspath(cfg)) or "."
    hist_file = ps.history_path(cfg, workdir)
    target = ps.convergence_target(cfg)

    # Move any previous history aside so we plot only this run.
    if os.path.exists(hist_file):
        try:
            os.replace(hist_file, hist_file + ".prev")
        except OSError:
            pass

    proc = subprocess.Popen(
        [exe, os.path.basename(cfg)],
        cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    hist = ps.History(hist_file)
    tail: list[str] = []
    pace = Pace()
    max_height = height or (14 if inline else 26)
    title = os.path.basename(cfg)

    with Screen(inline=inline) as screen:
        try:
            last = 0.0
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.strip():
                    tail.append(line)
                    if tail_lines and len(tail) > tail_lines:
                        del tail[:-tail_lines]
                hist.poll()
                pace.update(len(hist.iters))
                now = time.time()
                if now - last >= interval:
                    size = shutil.get_terminal_size((100, 30))
                    screen.draw(build_frame(
                        hist, title, target, tail[-tail_lines:] if tail_lines
                        else [], window, pace, size, max_height, only))
                    last = now
            proc.wait()
        except KeyboardInterrupt:
            proc.send_signal(signal.SIGINT)
            proc.wait()

    hist.poll()
    report(hist, hist_file, target, only)
    return proc.returncode


def report(hist, path, target, only=None):
    """Static summary printed after the live view is torn down."""
    if not hist.iters:
        print(f"no residual data read from {path}")
        return
    print(f"{len(hist.iters):,} rows from {os.path.basename(path)}, "
          f"last iteration {hist.iters[-1]:,}")
    series = hist.select(only)
    for label, ys in series:
        print(f"  {label:<10} {ys[-1]:>12.6f}   min {min(ys):>12.6f}")
    if series:
        diag = an.analyse(hist.iters, series[0][1], target)
        print()
        print("  " + an.summary_line(diag, series[0][1][-1], target))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="su2live",
        description="Live terminal residual plots for SU2.")
    ap.add_argument("config", nargs="?",
                    help="SU2 config to run and monitor")
    ap.add_argument("--file", "-f",
                    help="follow an existing history csv instead of running")
    ap.add_argument("--exec", "-e", dest="exe", default="SU2_CFD",
                    help="solver executable (default: SU2_CFD)")
    ap.add_argument("--interval", "-i", type=float, default=0.25,
                    help="seconds between redraws (default: 0.25)")
    ap.add_argument("--window", "-w", type=int, default=0,
                    help="plot only the last N iterations (default: all)")
    ap.add_argument("--tail", "-t", type=int, default=5,
                    help="lines of solver output to show (default: 5)")
    ap.add_argument("--target", type=float, default=None,
                    help="convergence target, overrides CONV_RESIDUAL_MINVAL")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--inline", dest="inline", action="store_true",
                      default=True,
                      help="draw in place, keeping scrollback (default)")
    mode.add_argument("--fullscreen", "-F", dest="inline",
                      action="store_false",
                      help="take over the terminal, restoring it on exit")
    ap.add_argument("--height", "-H", type=int, default=None,
                    help="plot height in rows (default: 14 inline, 26 full)")
    ap.add_argument("--only", "-o", default=None,
                    help="comma separated residuals to plot, e.g. Rho,RhoE. "
                         "Use --list to see what a run offers")
    ap.add_argument("--list", "-l", action="store_true",
                    help="list the residual columns in a history file and exit")
    args = ap.parse_args(argv)

    only = [c for c in args.only.split(",")] if args.only else None

    if args.list:
        path = args.file or (ps.history_path(args.config)
                             if args.config else None)
        if not path or not os.path.exists(path):
            print("give --file, or a config whose history file exists",
                  file=sys.stderr)
            return 1
        hist = ps.History(path)
        hist.poll()
        cols = hist.available()
        if not cols:
            print(f"no residual columns found in {path}")
            return 1
        print(f"{path}:")
        for c in cols:
            print(f"  {c}")
        return 0

    if args.file:
        run_follow(args.file, args.interval, args.window, args.target,
                   inline=args.inline, height=args.height, only=only)
        return 0

    if not args.config:
        ap.error("give a config to run, or --file to follow one")
    if not os.path.exists(args.config):
        print(f"no such config: {args.config}", file=sys.stderr)
        return 1
    if shutil.which(args.exe) is None:
        print(f"{args.exe} not found on PATH", file=sys.stderr)
        return 1

    return run_wrap(args.exe, args.config, args.interval, args.window,
                    args.tail, inline=args.inline, height=args.height,
                    only=only)


if __name__ == "__main__":
    sys.exit(main())
