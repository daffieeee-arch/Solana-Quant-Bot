"""Exit-grid report on mini parts that carry only farm = 'flagged' and 'organic' (as K3's parts do): farm 'all' is
their union; the paired Δ against tp4_T600 only on the common triggers (busy_until drops later triggers of a pool for
a longer hold), the dropped counts per side, the empty share, the Σ SOL per day and the bar."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from wallets import exits_report

SOL = 10**9
CFG = {"costs": {"ata_rent_lamports": 1_513_840, "scenarios": {"base": {"land_prob": 0.97, "fee_per_tx_sol": 0.000605, "tip_sol": 0.0001, "failed_tx_sol": 0.000505,
                                                                      "ata_rent": "refunded_on_full_exit", "adverse_exec_per_side": 0.0}}}}
DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]


def _row(variant, i, day, ret, d=1, farm=None):
    farm = farm or ("flagged" if i % 2 else "organic")
    cost = SOL // 2
    return {"family": "F7", "variant": variant, "segment": "pool_reversed", "mint": f"M{i}", "pool": f"P{i}", "t": 449_300_000 + 1000 * i, "d": d, "d_ms": 267.3 * d,
            "day": day, "regime": "270ms", "farm": farm, "overlay": "none", "size_sol": 0.5, "q": 0.5, "skipped": False, "entry_failed": False,
            "cost": cost, "proceeds": int(cost * (1 + ret)), "exit_attempts": 1, "entry_slot": 449_300_001 + 1000 * i, "exit_slot": 449_300_001 + 1000 * i + (20 if variant == "tp4_T600" else 225),
            "exit_reason": "take_profit" if variant == "tp4_T600" else "time"}


def _parts(path):
    rows = []
    for d in (1, 2):
        for i in range(40):
            day = DAYS[i % 4]
            # the baseline fills every trigger; hold60 skips every fourth (busy) and does +3 pt better on the common ones;
            # on one common trigger both sell into a drained pool (empty on both sides, Δ 0 there)
            base = -1.0 if i == 5 else (0.02 if i % 3 else -0.03)
            rows.append(_row("tp4_T600", i, day, base, d))
            if i % 4:
                rows.append(_row("hold60", i, day, -1.0 if i == 5 else base + 0.03, d))
    rows.append({**_row("hold60", 99, DAYS[0], 0.5), "skipped": True})  # a skipped position never counts
    assert not any(r["farm"] == "all" for r in rows)
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def test_paired_on_common_triggers_and_bar(tmp_path):
    _parts(str(tmp_path / "parts"))
    with open(tmp_path / "cfg.yaml", "w") as f:
        yaml.safe_dump(CFG, f)
    out = tmp_path / "exits.md"
    exits_report.main(["--parts", str(tmp_path / "parts"), "--cfg", str(tmp_path / "cfg.yaml"), "--boot", "200", "--out", str(out)])
    md = out.read_text()
    info = json.load(open(tmp_path / "exits.json"))
    assert info["positions"] == 140 and info["variants"] == 2   # 40 + 30 per d, the skipped one excluded
    def cells(prefix):
        return [c.strip() for c in next(ln for ln in md.splitlines() if ln.startswith(prefix)).split("|")[1:-1]]
    c = cells("| hold60 | 1 | all |")
    # days 10-18 block first: 30 positions (flagged 20 + organic 10: the busy triggers i % 4 == 0 are even, so organic), common 30, dropped v / b = 0 / 10
    assert c[3] == "30" and c[14] == "30" and c[19] == "0 / 10"
    assert cells("| hold60 | 1 | flagged |")[3] == "20" and cells("| hold60 | 1 | organic |")[3] == "10"
    # the paired Δ on the common set is +2.9 pt (29 x +3 pt, 0 on the drained trigger); empty 1 of 30 on both sides
    assert c[15] == "**+2.9%**" and c[18] == "3.3% / 3.3%"
    # Σ SOL / day: the union's total is the sum of the two groups' (4 days in the block)
    sol = lambda cc: float(cc[7])
    assert abs(sol(c) - sol(cells("| hold60 | 1 | flagged |")) - sol(cells("| hold60 | 1 | organic |"))) < 2e-3
    assert sol(c) < 0 and sol(cells("| tp4_T600 | 1 | all |")) < sol(c)   # the drained trigger dominates; the baseline fills ten more losers
    # tp4_T600's own row: no pairing columns
    base_row = next(ln for ln in md.splitlines() if ln.startswith("| tp4_T600 | 1 | all |"))
    assert base_row.rstrip().endswith("| – | – | – | – | – | – |")
    assert "| 1 | all |" in md and "| 2 | all |" in md
    # the bar: Δ CI > 0 at d = 1 and d = 2 and the empty share within 2 points -> clearly better on dev
    assert "| hold60 | yes | yes | **clearly better on dev** |" in md and info["passed"] == ["hold60"]
    assert "| hold60 | 30 | 0% | 100% |" in md    # exit reasons: all 'time'


def _row_long(family, variant, i, day, ret, peak_mult, d=1, segment="pool_normal"):
    """A long-grid position (engine fc4173c+): peak_mark = the gross mark at the peak; here cost * peak_mult."""
    r = _row(variant, i, day, ret, d, farm="organic")
    r.update({"family": family, "segment": segment, "peak_mark": int(r["cost"] * peak_mult), "peak_slot": r["entry_slot"] + 10,
              "exit_reason": "trailing_stop" if variant.endswith("trail20") else "time"})
    return r


def _parts_long(path):
    rows = []
    for i in range(40):
        day = DAYS[i % 4]
        # M1 k2: the reference k2_trail20 exits early on every trigger; k2_trail30 holds longer: two x5 runners captured
        # (+400 %), one x10 runner missed (sold at -20 % after a x10 peak), one drain, the rest +1 pt vs the reference
        rows.append(_row_long("M1", "k2_trail20", i, day, 0.02, 1.5))
        if i == 0 or i == 1:
            rows.append(_row_long("M1", "k2_trail30", i, day, 4.0, 6.0))
        elif i == 2:
            rows.append(_row_long("M1", "k2_trail30", i, day, -0.2, 12.0))
        elif i == 3:
            rows.append(_row_long("M1", "k2_trail30", i, day, -1.0, 1.1))
        else:
            rows.append(_row_long("M1", "k2_trail30", i, day, 0.03, 1.6))
        # F7 long: trail30_H6 against tp4_T600 (the F7 reference), reversed pools
        rows.append(_row_long("F7", "tp4_T600", i, day, 0.02, 1.2, segment="pool_reversed"))
        rows.append(_row_long("F7", "trail30_H6", i, day, 0.05 if i % 5 else -1.0, 2.0, segment="pool_reversed"))
    os.makedirs(path)
    pq.write_table(pa.Table.from_pylist(rows), os.path.join(path, "part-0.parquet"))


def test_long_grid_families_and_runners(tmp_path):
    _parts_long(str(tmp_path / "parts"))
    with open(tmp_path / "cfg.yaml", "w") as f:
        yaml.safe_dump(CFG, f)
    out = tmp_path / "exits.md"
    exits_report.main(["--parts", str(tmp_path / "parts"), "--families", "F7,M1", "--cfg", str(tmp_path / "cfg.yaml"), "--boot", "200", "--out", str(out)])
    md = out.read_text()
    info = json.load(open(tmp_path / "exits.json"))
    assert info["positions"] == 160 and info["variants"] == 4 and info["families"] == ["F7", "M1"]
    def cells(prefix, block):
        blk = md.split(block)[1]
        return [c.strip() for c in next(ln for ln in blk.splitlines() if ln.startswith(prefix)).split("|")[1:-1]]
    # M1: k2_trail30 is paired against k2_trail20 (its prefix + _trail20): 40 common triggers, Δ on them
    c = cells("| k2_trail30 | 1 | all |", "### days 10-18 (M1 pool_normal)")
    assert c[14] == "40" and c[19] == "0 / 0"
    # runners: 3 of 40 reached x5 (two captured, the x10 one not: -20 % < 50 % of its net peak); 1 of 40 reached x10, captured 0 %
    assert c[20] == "7.5% / 67%" and c[21] == "2.5% / 0%"
    # Σ SOL / day split: runners / drains / collapses / rest; the drain is one position of -0.5 SOL over 4 days
    r, dr, co, rest = [float(x) for x in c[22].split(" / ")]
    assert abs(dr - (-0.5 / 4)) < 0.01 and co == 0 and r > 0 and rest > 0
    # the reference row carries no pairing cells but the runner cells
    ref = cells("| k2_trail20 | 1 | all |", "### days 10-18 (M1 pool_normal)")
    assert ref[14] == "–" and ref[20] == "0.0% / –"
    # F7 keeps tp4_T600 as the reference; trail30_H6 has 8 drains (empty 20 %) so it fails the spec E bar at d = 1
    f = cells("| trail30_H6 | 1 | all |", "### days 10-18 (F7 pool_reversed)")
    assert f[14] == "40" and f[8] == "20.0%"
    assert "| trail30_H6 | no | no | no |" in md and info["passed"] == []
    assert "### Exit reasons (days 10-18, farm all, d = 1) (M1 pool_normal)" in md
