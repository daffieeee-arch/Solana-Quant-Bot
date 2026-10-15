"""Exit-grid report on mini parts: the paired Δ against tp4_T600 only on the common triggers (busy_until drops later
triggers of a pool for a longer hold), the dropped counts per side, the empty share and the bar."""

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


def _row(variant, i, day, ret, d=1, farm="all"):
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
    rows = [ln for ln in md.splitlines() if ln.startswith("| hold60 | 1 | all |")]
    cells = [c.strip() for c in rows[0].split("|")[1:-1]]
    # days 10-18 block first: 30 positions, common 30, dropped v / b = 0 / 10 (the baseline's ten busy triggers)
    assert cells[3] == "30" and cells[13] == "30" and cells[18] == "0 / 10"
    # the paired Δ on the common set is +2.9 pt (29 x +3 pt, 0 on the drained trigger); empty 1 of 30 on both sides
    assert cells[14] == "**+2.9%**" and cells[17] == "3.3% / 3.3%"
    # tp4_T600's own row: no pairing columns
    base_row = next(ln for ln in md.splitlines() if ln.startswith("| tp4_T600 | 1 | all |"))
    assert base_row.rstrip().endswith("| – | – | – | – | – | – |")
    # the bar: Δ CI > 0 at d = 1 and d = 2 and the empty share within 2 points -> clearly better on dev
    assert "| hold60 | yes | yes | **clearly better on dev** |" in md and info["passed"] == ["hold60"]
    assert "| hold60 | 30 | 0% | 100% |" in md    # exit reasons: all 'time'
