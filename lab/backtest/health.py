"""Disk health gate for heavy jobs (coordinator, 2026-10-11): checked before each store-build chunk
and each tournament batch.

The gate is open when:
- the disk guard flags lab/locks/io-stall and disk-low are absent;
- /proc/pressure/io 'some' avg60 is below 10;
- an 8 MB direct write (dd oflag=direct) reaches 50 MB/s.

The write test runs at most every 10 minutes across processes. While the gate is closed, the job
waits in 10-minute steps, and every wait is logged with its reason.
"""

import os
import re
import subprocess
import time

LAB = "/home/chupa/Solana-project/data-old-faithful-one/lab"
FLAGS = (os.path.join(LAB, "locks", "io-stall"), os.path.join(LAB, "locks", "disk-low"))
PRESSURE_MAX = 10.0
DD_MIN_MBS = 50.0
DD_BYTES = 8 * 1024 * 1024
DD_EVERY_S = 600
DD_STAMP = "/dev/shm/lab-health-dd-ok"  # last passed write test, shared by all processes
WAIT_S = 600


def io_pressure():
    """avg60 of 'some' in /proc/pressure/io (0 when unavailable)."""
    try:
        with open("/proc/pressure/io") as f:
            for line in f:
                if line.startswith("some"):
                    return float(re.search(r"avg60=([\d.]+)", line).group(1))
    except (OSError, AttributeError):
        pass
    return 0.0


def write_speed(directory):
    """MB/s of one 8 MB O_DIRECT write into directory (the file is removed); 0 on failure."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f".health-dd-{os.getpid()}")
    try:
        p = subprocess.run(["dd", "if=/dev/zero", f"of={path}", f"bs={DD_BYTES}", "count=1", "oflag=direct"],
                           capture_output=True, text=True, timeout=120, env={**os.environ, "LC_ALL": "C"})
        m = re.search(r"copied, ([\d.e+-]+) s", p.stderr)
        return DD_BYTES / 1e6 / max(float(m.group(1)), 1e-6) if (p.returncode == 0 and m) else 0.0
    except subprocess.TimeoutExpired:
        return 0.0
    finally:
        if os.path.exists(path):
            os.remove(path)


def problems(directory=os.path.join(LAB, "tmp-health"), dd=True):
    """Reasons the gate is closed (empty: open)."""
    out = [f"{f} exists" for f in FLAGS if os.path.exists(f)]
    p = io_pressure()
    if p >= PRESSURE_MAX:
        out.append(f"io pressure avg60 {p:.1f} >= {PRESSURE_MAX:g}")
    fresh = os.path.exists(DD_STAMP) and time.time() - os.path.getmtime(DD_STAMP) < DD_EVERY_S
    if dd and not out and not fresh:
        mbs = write_speed(directory)
        if mbs < DD_MIN_MBS:
            out.append(f"8 MB direct write {mbs:.0f} MB/s < {DD_MIN_MBS:g}")
        else:
            with open(DD_STAMP, "w") as f:
                f.write(f"{mbs:.0f}\n")
    return out


def wait_ok(log=print, what="", **kw):
    """Block until the gate is open; log each closed check. Returns the seconds waited."""
    t0 = time.time()
    while True:
        why = problems(**kw)
        if not why:
            return time.time() - t0
        log(f"health gate closed{' before ' + what if what else ''}: {'; '.join(why)}; retry in {WAIT_S // 60} min")
        time.sleep(WAIT_S)
