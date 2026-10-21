"""checks.probe_lead on synthetic lab-probe signature files: the lead per transaction, the common slot range with its
margin, transactions only one stream saw, a signature that moved slot and one received in two banks; then the three
streams (deshred, processed, confirmed) pairwise, with a transaction only deshred saw."""

import json

import pytest

from checks import probe_lead as pl

MS = 1_000_000
T0 = 1_792_600_000 * 1_000_000_000
HEADER = "signature,slot,bank_id,filters,received_unix_ns\n"


def _file(path, rows):
    path.write_text(HEADER + "".join(f"{s},{slot},{'' if bank is None else bank},pump,{t}\n" for s, slot, bank, t in rows))
    return str(path)


@pytest.fixture
def files(tmp_path):
    # slots 100..200 on processed, 110..210 on confirmed: the common range 110..200, with margin 5: 115..195
    proc = [("edge_p", 100, 1, T0), ("edge_p2", 112, 1, T0)]  # before the common range (+ margin): left out
    conf = [("first_c", 110, 0, T0), ("edge_c", 205, 0, T0), ("late_c", 210, 0, T0)]
    leads = [10, 20, 30, 40, 60, 80, -5, 100]  # ms; 3 above 50, 1 below 0
    for i, lead_ms in enumerate(leads):
        slot = 120 + i
        proc.append((f"s{i}", slot, 1, T0 + i * 1_000 * MS))
        conf.append((f"s{i}", slot, 0, T0 + i * 1_000 * MS + lead_ms * MS))
    proc += [("dead1", 150, 3, T0), ("dead2", 150, 3, T0), ("dead3", 151, 4, T0)]  # slots that died: never confirmed
    conf += [("miss", 160, 0, T0)]                                                  # dropped on processed
    proc += [("fork", 170, 5, T0), ("fork", 171, 6, T0 + 2 * MS)]                   # forked: seen twice, then confirmed in 171
    conf += [("fork", 171, 0, T0 + 50 * MS)]
    proc += [("ends", 200, 1, T0), ("ends2", 199, 1, T0)]                           # the processed run's last slots
    conf += [("c_end", 196, 0, T0)]
    proc += [("cross", 195, 1, T0)]  # confirmed one slot later, past the range end: not "only processed"
    conf += [("cross", 196, 0, T0 + 5 * MS)]
    return _file(tmp_path / "p.csv", proc), _file(tmp_path / "c.csv", conf)


def test_the_lead_and_the_streams_differences(files):
    r = pl.compare({"processed": pl.read(files[0]), "confirmed": pl.read(files[1])}, margin_slots=5)
    assert r["slot_range"] == [115, 195] and r["streams"] == ["processed", "confirmed"]
    pc = r["pairs"]["processed_vs_confirmed"]
    L = pc["lead_ms"]
    # s0..s7 and fork (first processed receive at T0 in slot 170, confirmed at T0 + 50 ms): 9 in both
    assert pc["both"] == L["n"] == 9
    vals = sorted([10, 20, 30, 40, 60, 80, -5, 100, 50])
    assert L["p50"] == pytest.approx(pl.quantile(vals, 0.5)) == pytest.approx(40)
    assert L["p90"] == pytest.approx(80 + 0.2 * 20)  # rank 7.2 of 0..8
    assert (L["min"], L["max"]) == (-5, 100)
    assert L["share_gt_50ms"] == pytest.approx(3 / 9) and L["share_lt_0"] == pytest.approx(1 / 9)  # exactly 50 is not above
    assert pc["only_processed"]["n"] == 3 and pc["only_processed"]["slots"] == 2
    assert tuple(pc["only_processed"]["top_slots"][0]) == (150, 2)
    assert pc["only_confirmed"]["n"] == 1  # 'miss'; 'edge_c', 'late_c' and 'c_end' (196) are outside 115..195
    assert pc["moved"] == 1 and r["multi_bank"] == {"processed": 1, "confirmed": 0}
    assert r["only_in_one"] == {"processed": 3, "confirmed": 1}


def test_three_streams_pairwise_with_what_only_deshred_saw(tmp_path, files):
    # deshred sees s0..s7 30 ms before processed, the dead slots' transactions, and 'failed' that never lands anywhere
    proc, conf = pl.read(files[0]), pl.read(files[1])
    rows = [("first_d", 100, None, T0), ("last_d", 210, None, T0)]
    rows += [(s, v[0][1], None, v[0][0] - 30 * MS) for s, v in proc.items() if s.startswith("s")]
    rows += [("dead1", 150, None, T0 - MS), ("dead2", 150, None, T0 - MS), ("failed", 140, None, T0)]
    des = pl.read(_file(tmp_path / "d.csv", rows))
    assert des["failed"] == [(T0, 140, None)]  # an empty bank_id reads as None
    r = pl.compare({"confirmed": conf, "deshred": des, "processed": proc}, margin_slots=5)
    assert r["streams"] == ["deshred", "processed", "confirmed"] and r["slot_range"] == [115, 195]
    dp, dc = r["pairs"]["deshred_vs_processed"], r["pairs"]["deshred_vs_confirmed"]
    assert dp["both"] == 10 and dp["lead_ms"]["p50"] == pytest.approx(30)  # s0..s7 and the two dead ones
    assert dp["only_deshred"]["n"] == 1 and dp["only_processed"]["n"] == 3  # 'failed'; dead3, fork and cross
    assert dc["both"] == 8 and dc["lead_ms"]["min"] == pytest.approx(25)  # s6: -5 ms behind processed, +30 over it
    assert dc["only_deshred"]["n"] == 3  # failed, dead1, dead2
    assert r["only_in_one"] == {"deshred": 1, "processed": 1, "confirmed": 1}  # 'failed'; dead3; 'miss'
    assert "fail or never execute" in r["note"]
    assert r["pairs"]["processed_vs_confirmed"]["both"] == 9  # the two-stream answer is unchanged


def test_quantiles_and_inputs(tmp_path, files, capsys):
    assert pl.quantile([], 0.5) is None
    assert pl.quantile([1.0, 2.0, 3.0, 4.0], 0.9) == pytest.approx(3.7)  # as lab-probe's Rust quantile
    bad = tmp_path / "bad.csv"
    bad.write_text("sig,slot\nx,1\n")
    with pytest.raises(ValueError, match="lab-probe"):
        pl.read(str(bad))
    empty = _file(tmp_path / "e.csv", [])
    assert pl.compare({"processed": pl.read(empty), "confirmed": pl.read(files[1])})["error"] == "an empty file"
    with pytest.raises(ValueError, match="at least two"):
        pl.compare({"confirmed": pl.read(files[1])})
    assert pl.main(["--confirmed", files[1]]) == 2
    out = tmp_path / "lead.json"
    assert pl.main(["--processed", files[0], "--confirmed", files[1], "--margin-slots", "5", "--out", str(out)]) == 0
    both = lambda t: json.loads(t)["pairs"]["processed_vs_confirmed"]["both"]  # noqa: E731
    assert both(out.read_text()) == 9 and both(capsys.readouterr().out) == 9
