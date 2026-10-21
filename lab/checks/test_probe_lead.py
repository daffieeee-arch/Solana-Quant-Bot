"""checks.probe_lead on two synthetic lab-probe signature files: the lead per transaction, the common slot range with
its margin, transactions only one stream saw, a signature that moved slot and one received in two banks."""

import json

import pytest

from checks import probe_lead as pl

MS = 1_000_000
T0 = 1_792_600_000 * 1_000_000_000
HEADER = "signature,slot,bank_id,filters,received_unix_ns\n"


def _file(path, rows):
    path.write_text(HEADER + "".join(f"{s},{slot},{bank},pump,{t}\n" for s, slot, bank, t in rows))
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
    r = pl.lead(pl.read(files[0]), pl.read(files[1]), margin_slots=5)
    assert r["slot_range"] == [115, 195]
    L = r["lead_ms"]
    # s0..s7 and fork (first processed receive at T0 in slot 170, confirmed at T0 + 50 ms): 9 in both
    assert r["both"] == L["n"] == 9
    vals = sorted([10, 20, 30, 40, 60, 80, -5, 100, 50])
    assert L["p50"] == pytest.approx(pl.quantile(vals, 0.5)) == pytest.approx(40)
    assert L["p90"] == pytest.approx(80 + 0.2 * 20)  # rank 7.2 of 0..8
    assert (L["min"], L["max"]) == (-5, 100)
    assert L["share_gt_50ms"] == pytest.approx(3 / 9) and L["share_lt_0"] == pytest.approx(1 / 9)  # exactly 50 is not above
    assert r["only_processed"]["n"] == 3 and r["only_processed"]["slots"] == 2
    assert tuple(r["only_processed"]["top_slots"][0]) == (150, 2)
    assert r["only_confirmed"]["n"] == 1  # 'miss'; 'edge_c', 'late_c' and 'c_end' (196) are outside 115..195
    assert (r["moved"], r["multi_bank"]) == (1, 1)


def test_quantiles_and_inputs(tmp_path, files, capsys):
    assert pl.quantile([], 0.5) is None
    assert pl.quantile([1.0, 2.0, 3.0, 4.0], 0.9) == pytest.approx(3.7)  # as lab-probe's Rust quantile
    bad = tmp_path / "bad.csv"
    bad.write_text("sig,slot\nx,1\n")
    with pytest.raises(ValueError, match="lab-probe"):
        pl.read(str(bad))
    empty = _file(tmp_path / "e.csv", [])
    assert pl.lead(pl.read(empty), pl.read(files[1]))["error"] == "an empty file"
    out = tmp_path / "lead.json"
    assert pl.main(["--processed", files[0], "--confirmed", files[1], "--margin-slots", "5", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["both"] == 9 and json.loads(capsys.readouterr().out)["both"] == 9
