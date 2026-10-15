"""The rt_tx split renders spec-B statistics: every row counts (cut rows included), dead = net <= -99 %, trimmed
mean with a day-bootstrap CI, the median beside it, and the difference of the trimmed means."""

import os

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from wallets import markout_rt_tx


def test_render_counts_cut_rows_and_dead_share(tmp_path):
    rows = []
    days = ["2026-09-11", "2026-09-12", "2026-09-13"]
    for i in range(60):
        pool, t, day = f"P{i}", 1000 + i, days[i % 3]
        rt = i % 4 == 0  # 15 rt_tx triggers
        for h, h_ms in (("1s", 1000), ("6h", 21_600_000)):
            if h == "1s":
                net, cut = (-0.012 if rt else 0.002), False
            else:  # at 6 h every third signal is dead (cut, last known dust state, net -100 %); the rest +10 % / +50 %
                dead = i % 3 == 0
                net, cut = (-1.0 if dead else (0.10 if rt else 0.50)), dead
            rows.append({"family": "F7", "segment": "pool_reversed", "venue": "pool", "orientation": "reversed", "mint": f"M{i}", "pool": pool,
                         "t": t, "day": day, "farm": "organic", "d": 1, "h": h, "h_ms": h_ms, "target": t + 1 + h_ms // 400, "cut": cut,
                         "mid": 1.0, "gross": 50.0 if cut else net, "net": net, "impact": 0.005})
        rows.append({**rows[-1], "h": "", "h_ms": 0, "cut": False, "gross": None, "net": None})
    path = str(tmp_path / "markout-f7rev.parquet")
    pq.write_table(pa.Table.from_pylist(rows), path)
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE f_rt AS SELECT 'P' || i::VARCHAR AS pool, 1000 + i AS t, i % 4 = 0 AS rt_tx FROM range(60) r(i)")
    md = markout_rt_tx.render(con, path)
    lines = [ln for ln in md.splitlines() if ln.startswith("| 1s") or ln.startswith("| 6h")]
    assert len(lines) == 2
    l1, l6 = lines
    # 1 s: 15 rt_tx at -1.20 %, 45 others at +0.20 %, nothing dead, difference +1.40 %
    assert "| 15 | 0 % | -1.20 % (med -1.20 %)" in l1 and "| 45 | 0 % | +0.20 % (med +0.20 %)" in l1 and "+1.40 %" in l1
    # 6 h: the cut rows count (n stays 15 / 45), dead share 5/15 and 15/45; trimmed means (-5 + 1) / 15 and (-15 + 15) / 45
    assert "| 15 | 33 % | -26.67 % (med +10.00 %)" in l6 and "| 45 | 33 % | +0.00 % (med +50.00 %)" in l6 and "+26.67 %" in l6
    assert "every row counts" in md and "dead = net ≤ −99 %" in md
    assert os.path.exists(path)
