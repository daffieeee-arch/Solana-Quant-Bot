"""lab/ops/live-retention.sh on a temporary recorder root: old complete segments go (once the keeper
kept them), the newest, young, unfinished and non-segment entries stay; the disk guard's flags change
the rule; a failing keeper, a bad KEEP_DAYS or a failing du never delete what is not kept."""

import os
import stat
import subprocess
import sys
import time

OPS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(OPS, "live-retention.sh")
LAB_CODE = os.path.dirname(OPS)
DAY = 86_400
# A keeper that marks every segment it gets as kept (the real one needs real Parquet files).
STUB_KEEPER = """#!/usr/bin/env bash
out=""; segs=()
while (( $# )); do case $1 in --journal) shift 2;; --out) out=$2; shift 2;; *) segs+=("$1"); shift;; esac; done
for s in "${segs[@]}"; do mkdir -p "$out/$(basename "$s")"; echo '{}' > "$out/$(basename "$s")/DONE.json"; done
"""


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def make(root, name, age_days, complete=True):
    d = os.path.join(root, "recorder", "segments", name)
    os.makedirs(os.path.join(d, "pump_amm", "BuyEvent"), exist_ok=True)
    open(os.path.join(d, "pump_amm", "BuyEvent", "part-0.parquet"), "wb").write(b"x" * 2048)
    if complete:
        p = os.path.join(d, "_segment.json")
        open(p, "w").write("{}")
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))
    return d


def run(lab, check=True, **env):
    env.setdefault("KEEPER_CMD", executable(lab.parent / "stub-keeper.sh", STUB_KEEPER))
    e = dict(os.environ, LAB=str(lab), **{k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", SCRIPT], check=check, env=e)


def test_keeps_three_days_and_the_rest(tmp_path):
    lab = tmp_path / "lab"
    old = [make(lab, f"{i:09d}", 5) for i in (1, 2)]
    young = make(lab, "000000003", 1)
    unfinished = make(lab, "000000004", 0, complete=False)
    newest_old = make(lab, "000000005", 9)  # the newest segment is never deleted
    other = lab / "recorder" / "segments" / "notes"
    other.mkdir()
    (lab / "recorder" / "budget.json").write_text("{}")
    (lab / "journal").mkdir(parents=True)
    run(lab, DRY_RUN=1)
    assert all(os.path.exists(d) for d in old)  # a dry run deletes nothing
    run(lab)
    assert not any(os.path.exists(d) for d in old)
    assert all(os.path.exists(d) for d in (young, unfinished, newest_old)) and other.exists()
    assert (lab / "recorder" / "budget.json").exists() and (lab / "journal").exists()
    assert "deleted 2 segments" in (lab / "logs" / "live-retention.log").read_text()


def test_disk_guard_flags(tmp_path):
    lab = tmp_path / "lab"
    two_days = make(lab, "000000001", 2)
    make(lab, "000000002", 0)
    (lab / "locks").mkdir(parents=True)
    (lab / "locks" / "io-stall").write_text("x")
    run(lab)
    assert os.path.exists(two_days)  # skipped while the disk stalls
    (lab / "locks" / "io-stall").unlink()
    run(lab)
    assert os.path.exists(two_days)  # 2 days < 3
    (lab / "locks" / "disk-low").write_text("x")
    run(lab)
    assert not os.path.exists(two_days)  # disk low: 1 day


def test_unkept_segments_stay(tmp_path):
    lab = tmp_path / "lab"
    old = make(lab, "000000001", 5)
    make(lab, "000000002", 0)
    run(lab, KEEPER_CMD="false")  # the keeper fails: nothing is deleted, the round still ends cleanly
    assert os.path.exists(old)
    log = (lab / "logs" / "live-retention.log").read_text()
    assert "keeper exit 1" in log and "1 old segments not deleted" in log
    run(lab)
    assert not os.path.exists(old) and (lab / "journal" / "paper-k3" / "kept" / "000000001" / "DONE.json").exists()


def test_keep_days_must_be_whole_days(tmp_path):
    lab = tmp_path / "lab"
    old = make(lab, "000000001", 5)
    make(lab, "000000002", 0)
    for bad in ("3.5", "abc", "-1"):  # (empty means the default, 3)
        r = run(lab, check=False, KEEP_DAYS=bad)
        assert r.returncode == 2 and os.path.exists(old)
    assert "is not a whole number of days" in (lab / "logs" / "live-retention.log").read_text()


def test_a_failing_du_does_not_end_the_round(tmp_path):
    lab = tmp_path / "lab"
    old = make(lab, "000000001", 5)
    make(lab, "000000002", 0)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "du", "#!/bin/sh\nexit 1\n")
    run(lab, PATH=f"{bin_dir}:{os.environ['PATH']}")
    assert not os.path.exists(old)


def test_with_the_real_keeper(tmp_path):
    """A segment with real Parquet: paper.keeper keeps its candles, then the segment goes."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    lab = tmp_path / "lab"
    old = make(lab, "000000001", 5)
    os.remove(os.path.join(old, "pump_amm", "BuyEvent", "part-0.parquet"))
    pq.write_table(pa.table({"slot": [1], "tx_index": [0], "outer_ix": [0], "inner_ix": [1], "pool": ["P"],
                             "base_amount_out": [10], "quote_amount_in": [20]}),
                   os.path.join(old, "pump_amm", "BuyEvent", "part-0.parquet"))
    make(lab, "000000002", 0)
    run(lab, KEEPER_CMD=f"{sys.executable} -m paper.keeper", CODE=LAB_CODE)
    kept = lab / "journal" / "paper-k3" / "kept" / "000000001"
    assert not os.path.exists(old) and (kept / "DONE.json").exists() and (kept / "candles.parquet").exists()


def test_the_paper_engine_has_a_restart_timer():
    units = os.path.join(OPS, "systemd")
    timer = open(os.path.join(units, "lab-paper.timer")).read()
    assert "Unit=lab-paper.service" in timer and "OnCalendar=*:0/15" in timer
    readme = open(os.path.join(OPS, "README.md")).read()
    assert "enable --now lab-paper.timer" in readme and "disable --now lab-paper.timer" in readme


def test_live_units_stay_down_after_a_disk_guard_stop():
    """ops/disk-guard.sh writes locks/disk-stopped at its emergency stop; no timer or reboot may start
    the live units again until the coordinator removes it (coordinator decision 2026-10-13)."""
    units = os.path.join(OPS, "systemd")
    cond = "ConditionPathExists=!%h/Solana-project/data-old-faithful-one/lab/locks/disk-stopped"
    for u in ("lab-paper.service", "lab-recorder.service", "lab-token-meta-rpc.service"):
        lines = open(os.path.join(units, u)).read().splitlines()
        unit = lines[lines.index("[Unit]"):lines.index("[Service]")]
        assert cond in unit, u
    retention = open(os.path.join(units, "lab-live-retention.service")).read().splitlines()
    assert "MemoryMax=1500M" in retention
    # systemd takes no comment after a value on the same line
    for f in os.listdir(units):
        for line in open(os.path.join(units, f)):
            assert line.startswith(("#", ";")) or "#" not in line, (f, line)
