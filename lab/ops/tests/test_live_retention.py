"""lab/ops/live-retention.sh on a temporary recorder root: old complete segments go, the newest,
young, unfinished and non-segment entries stay; the disk guard's flags change the rule."""

import os
import subprocess
import time

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live-retention.sh")
DAY = 86_400


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


def run(lab, **env):
    e = dict(os.environ, LAB=str(lab), **{k: str(v) for k, v in env.items()})
    subprocess.run(["bash", SCRIPT], check=True, env=e)


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
