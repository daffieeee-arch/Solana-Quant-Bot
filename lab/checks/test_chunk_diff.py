"""checks.chunk_diff on synthetic mini chunk directories: equal chunks, a changed value, missing and extra rows,
duplicates, type-only drift, the fca8c80 normalisation, one-sided tables and columns, manifest checks, the --extra
root, footprints, buckets, and the refusals. Test names avoid the refused words: tmp_path carries the test name."""

import json
import os
import shutil

import duckdb
import pytest

from checks import chunk_diff as cd

S, T = 450_576_000, 450_792_000
NAME = f"{S}-{T}"
EV = [("slot", "UBIGINT"), ("tx_index", "UBIGINT"), ("signature", "VARCHAR"), ("outer_ix", "UBIGINT"), ("inner_ix", "UBIGINT"),
      ("event_seq", "UBIGINT"), ("decode_status", "VARCHAR"), ("parent_ix_args", "VARCHAR"), ("parent_ix_data", "VARCHAR"),
      ("signers", "VARCHAR"), ("mint", "VARCHAR"), ("sol_amount", "UBIGINT")]
FAILED = [("slot", "UBIGINT"), ("tx_index", "UBIGINT"), ("fee_payer", "VARCHAR"), ("signers", "VARCHAR"), ("ix_args", "VARCHAR"),
          ("ix_data", "VARCHAR")]
BLOCKS = [("slot", "UBIGINT"), ("parent_slot", "UBIGINT"), ("block_time", "BIGINT"), ("executed_tx_count", "UBIGINT"), ("skipped", "BOOLEAN")]
TXCTX = [("slot", "UBIGINT"), ("tx_index", "UBIGINT"), ("fee_payer", "VARCHAR"), ("programs", "VARCHAR")]
STATUS = ["ok", "prefix", "extra", "error", None]  # None: the parent is no pump/PumpSwap instruction, args null
OLD_SHA, NEW_SHA = "0cfc4db2" + "0" * 56, "f5dd11df" + "0" * 56


def _semantics(i, post, signer_bug=False, data_bug=False):
    """(signers, args, raw data) of row i as b10c5c8 (post=False) or fca8c80 and later (post=True) write them."""
    keys = [f"Payer{i}"] + ([f"Cosign{i}"] if i % 4 == 0 else [])
    status = STATUS[i % 5]
    args = None if status is None else f'{{"_decode":"{status}","amount":{i}}}'
    data = f"66063d1201daebea{i:04x}"
    if not post:
        return ",".join(keys), args, data
    signers = ",".join(keys) if len(keys) > 1 or signer_bug else None
    raw = None if status in ("ok", "prefix") or data_bug else data
    return signers, args, raw


def trades(post=True, n=24, row=None, **bug):
    """TradeEvent rows: (slot, tx_index) unique, every signer count and decode status mixed; `bug` on row `row`."""
    rows = []
    for i in range(n):
        signers, args, raw = _semantics(i, post, **(bug if i == row else {}))
        rows.append((S + i // 3, i % 3, f"sig{i}", 2, i % 2, 0, "prefix", args, raw, signers, f"mint{i % 5}pump", 1000 + i))
    return rows


def failed(post=True, n=10):
    out = []
    for i in range(n):
        signers, args, raw = _semantics(i, post)
        out.append((S + i, 7, f"Payer{i}", signers, args, raw))
    return out


def blocks(n=10):
    return [(S + i, S + i - 1, 1_790_000_000 + i, 5, False) for i in range(n)]


def write(chunk, table, cols, rows):
    d = os.path.join(chunk, table)
    os.makedirs(d, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({', '.join(f'{cd.q(c)} {typ}' for c, typ in cols)})")
    if rows:
        con.executemany(f"INSERT INTO t VALUES ({', '.join('?' * len(cols))})", rows)
    con.execute(f"COPY t TO '{d}/part-00000.parquet' (FORMAT parquet)")
    return len(rows)


def chunk(root, tabs, name=NAME, counters=None, **man):
    """A chunk directory with these {table: (columns, rows)} and a complete manifest whose rows match them."""
    c = os.path.join(str(root), name)
    os.makedirs(c, exist_ok=True)
    rows = {tb: write(c, tb, cols, r) for tb, (cols, r) in tabs.items()}
    s, t = (int(x) for x in name.split("-"))
    seen = sum(b[3] for b in tabs["blocks"][1]) if "blocks" in tabs else 0
    counters = {"blocks": 10, "skipped_markers": 0, "txs_seen": seen, "events": 24, "firehose_errors": 0, **(counters or {})}
    m = {"status": "complete", "slot_start": s, "slot_end_exclusive": t, "coverage": {"missing_slots": 0, "missing_ranges": []},
         "counters": {k: v for k, v in counters.items() if v is not None},  # None: the manifest lacks the counter
         "rows": rows, "idl_sha256": {"pump.json": "6a19bad7"}, "extractor_sha256": NEW_SHA, "threads": 1, **man}
    with open(os.path.join(c, "_manifest.json"), "w") as f:
        json.dump(m, f)
    return c


def base(post=True, trade_rows=None, extra_new=False):
    tabs = {"blocks": (BLOCKS, blocks()), "pump/TradeEvent": (EV, trade_rows if trade_rows is not None else trades(post)),
            "failed_txs": (FAILED, failed(post))}
    if extra_new:  # what a f5dd11df chunk adds: the ix_* columns and new tables
        ev = EV + [("ix_pool", "VARCHAR")]
        tabs["pump/TradeEvent"] = (ev, [r + (None,) for r in tabs["pump/TradeEvent"][1]])
        tabs["tx_ctx"] = (TXCTX, [(S + i, 1, f"Payer{i}", "1:pump") for i in range(4)])
    return tabs


def diff(tmp_path, old, new, *args, out="out"):
    o = str(tmp_path / out)
    rc = cd.main(["--old", old, "--new", new, "--out", o, "--spill", str(tmp_path / "spill"), "--memory", "300MB", "--threads", "1", *args])
    s = None
    if os.path.exists(os.path.join(o, "summary.json")):
        with open(os.path.join(o, "summary.json")) as f:
            s = json.load(f)
    return rc, s


def tab(s, table):
    return s["chunks"][NAME]["tables"][table]


def test_equal_chunks_green(tmp_path):
    old = chunk(tmp_path / "a", base(), extractor_sha256=OLD_SHA, threads=6)
    new = chunk(tmp_path / "b", base(extra_new=True))
    rc, s = diff(tmp_path, old, new)
    assert rc == 0 and s["verdict"] == "GREEN" and s["unexpected"] == 0
    tr = tab(s, "pump/TradeEvent")
    assert (tr["identical"], tr["expected"], tr["differing"], tr["only_old"], tr["only_new"]) == (24, 0, 0, 0, 0)
    assert tr["columns"]["new_only_ignored"] == ["ix_pool"] and tr["type_drift"] == []
    assert tr["decode_status"] == {"old": {"prefix": 24}, "new": {"prefix": 24}}
    assert tab(s, "blocks")["identical"] == 10 and tab(s, "failed_txs")["identical"] == 10
    assert tab(s, "tx_ctx")["ignored"] and tab(s, "tx_ctx")["unexpected"] == 0
    out = tmp_path / "out"
    assert {"summary.json", "report.md", "footprints.json", "SHA256SUMS"} <= set(os.listdir(out))
    sums = (out / "SHA256SUMS").read_text()
    assert "summary.json" in sums and "report.md" in sums and "footprints.json" in sums
    assert os.listdir(tmp_path / "spill") == []  # its own spill directory is removed
    assert "chunk_diff: GREEN" in (out / "report.md").read_text()


def test_changed_value_red(tmp_path):
    rows = trades()
    rows[7] = rows[7][:-1] + (rows[7][-1] + 1,)
    old = chunk(tmp_path / "a", base())
    new = chunk(tmp_path / "b", base(trade_rows=rows))
    rc, s = diff(tmp_path, old, new)
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1 and s["verdict"] == "RED"
    assert (tr["identical"], tr["differing"], tr["only_old"], tr["only_new"], tr["unexpected"]) == (23, 1, 0, 0, 1)
    assert tr["column_diffs"] == {"sol_amount": {"raw": 1, "unexpected": 1}}
    smp = duckdb.sql(f"SELECT slot, tx_index, \"column\", old, new FROM '{tmp_path}/out/diffs/{NAME}/pump__TradeEvent.differing.parquet'").fetchall()
    assert smp == [(S + 2, 1, "sol_amount", "1007", "1008")]
    assert s["unexpected"] == 1 and tab(s, "blocks")["unexpected"] == 0


def test_missing_and_extra_rows_red(tmp_path):
    rows = trades()
    gone = rows.pop(5)
    rows.append((S + 100_000, 0, "sigX", 2, 0, 0, "prefix", None, "ff", None, "mintXpump", 1))
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base()), chunk(tmp_path / "b", base(trade_rows=rows)))
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1 and (tr["only_old"], tr["only_new"], tr["identical"], tr["unexpected"]) == (1, 1, 23, 2)
    assert duckdb.sql(f"SELECT slot, tx_index FROM '{tmp_path}/out/diffs/{NAME}/pump__TradeEvent.only_old.parquet'").fetchall() == [gone[:2]]
    assert duckdb.sql(f"SELECT slot FROM '{tmp_path}/out/diffs/{NAME}/pump__TradeEvent.only_new.parquet'").fetchall() == [(S + 100_000,)]
    assert tr["only_by_slot"] == {str(S): [1, 0], str(S + 96_000): [0, 1]}


def test_duplicates_counted_apart(tmp_path):
    old_rows, new_rows = trades(), trades()
    old_rows.append(old_rows[12])                             # old: one identical duplicate
    new_rows.append(new_rows[3])                              # new: one identical duplicate (a firehose re-delivery)
    new_rows.append(new_rows[9][:-1] + (new_rows[9][-1] + 5,))  # new: one key, two different rows
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base(trade_rows=old_rows)), chunk(tmp_path / "b", base(trade_rows=new_rows)))
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1
    assert tr["duplicates"]["new"] == {"keys": 2, "identical_rows": 1, "conflict_keys": 1, "conflict_rows": 2}
    assert tr["duplicates"]["old"] == {"keys": 1, "identical_rows": 1, "conflict_keys": 0, "conflict_rows": 0}
    # compared on the de-duplicated rows, without the conflicting key: no other difference
    assert (tr["identical"], tr["differing"], tr["only_old"], tr["only_new"]) == (23, 0, 0, 0)
    assert tr["unexpected"] == 1 + 1 + 2
    dn = duckdb.sql(f"SELECT slot, tx_index, rows, distinct_rows FROM '{tmp_path}/out/diffs/{NAME}/pump__TradeEvent.duplicates_new.parquet' ORDER BY 1, 2").fetchall()
    assert dn == [(S + 1, 0, 2, 1), (S + 3, 0, 2, 2)]


def test_duplicate_only_in_new_red(tmp_path):
    rows = trades()
    rows.append(rows[0])
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base()), chunk(tmp_path / "b", base(trade_rows=rows)))
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1 and tr["unexpected"] == 1 and tr["duplicates"]["new"]["identical_rows"] == 1 and tr["identical"] == 24


def test_type_only_drift_reported_apart(tmp_path):
    new = base()
    new["pump/TradeEvent"] = ([(c, "BIGINT") if c == "sol_amount" else (c, t) for c, t in EV], trades())
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base()), chunk(tmp_path / "b", new))
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1
    assert tr["type_drift"] == [{"column": "sol_amount", "old": ["UBIGINT", ["INT64"]], "new": ["BIGINT", ["INT64"]]}]
    assert (tr["identical"], tr["differing"], tr["unexpected"]) == (24, 0, 1)  # equal values once both are HUGEINT


def test_fca8c80_normalisation(tmp_path):
    old = chunk(tmp_path / "a", base(post=False), extractor_sha256=OLD_SHA, threads=6)
    new = chunk(tmp_path / "b", base(extra_new=True))
    single = sum(1 for i in range(24) if i % 4)                          # one signer: null since fca8c80
    exact = sum(1 for i in range(24) if STATUS[i % 5] in ("ok", "prefix"))  # raw data null since fca8c80
    changed = sum(1 for i in range(24) if i % 4 or STATUS[i % 5] in ("ok", "prefix"))
    rc, s = diff(tmp_path, old, new, out="raw")
    assert rc == 1 and tab(s, "pump/TradeEvent")["differing"] == changed
    rc, s = diff(tmp_path, old, new, "--normalize", "fca8c80", "--expect-old", f"extractor_sha256={OLD_SHA}", out="norm")
    tr, ft = tab(s, "pump/TradeEvent"), tab(s, "failed_txs")
    assert rc == 0 and s["verdict"] == "GREEN"
    assert (tr["identical"], tr["expected"], tr["differing"]) == (24 - changed, changed, 0)
    assert tr["expected_cells"] == {"signers": single, "parent_ix_data": exact} and tr["normalised"] == ["parent_ix_data", "signers"]
    f_single = sum(1 for i in range(10) if i % 4)
    f_exact = sum(1 for i in range(10) if STATUS[i % 5] in ("ok", "prefix"))
    assert ft["expected_cells"] == {"signers": f_single, "ix_data": f_exact} and ft["differing"] == 0


@pytest.mark.parametrize("bug,column", [({"signer_bug": True, "row": 1}, "signers"), ({"data_bug": True, "row": 2}, "parent_ix_data")])
def test_fca8c80_rules_do_not_hide_new_bugs(tmp_path, bug, column):
    """Row 1 has one signer (new must write null), row 2 decodes 'extra' (new must keep the hex)."""
    old = chunk(tmp_path / "a", base(post=False))
    new = chunk(tmp_path / "b", base(trade_rows=trades(**bug)))
    rc, s = diff(tmp_path, old, new, "--normalize", "fca8c80")
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1 and tr["differing"] == 1 and tr["column_diffs"][column]["unexpected"] == 1
    assert [c for c, v in tr["column_diffs"].items() if v["unexpected"]] == [column]


def test_one_sided_tables_and_columns(tmp_path):
    old, new = base(), base()
    old["pump/CompleteEvent"] = (EV, trades()[:2])                    # an event table only in old
    new["pump_amm/SellEvent"] = (EV, trades()[:3])                    # an event table only in new
    new["token_deltas"] = ([("slot", "UBIGINT"), ("mint", "VARCHAR")], [(S, "m")])  # new in f5dd11df: ignored
    old["failed_txs"] = (FAILED + [("signature", "VARCHAR")], [r + ("sig",) for r in failed()])  # stripped later: expected
    old["blocks"] = (BLOCKS + [("legacy", "VARCHAR")], [r + ("x",) for r in blocks()])            # lost in new: unexpected
    rc, s = diff(tmp_path, chunk(tmp_path / "a", old), chunk(tmp_path / "b", new))
    assert rc == 1
    assert tab(s, "pump/CompleteEvent")["only_side"] == "old" and tab(s, "pump/CompleteEvent")["unexpected"] == 2
    assert tab(s, "pump_amm/SellEvent")["only_side"] == "new" and tab(s, "pump_amm/SellEvent")["unexpected"] == 3
    assert tab(s, "token_deltas")["unexpected"] == 0 and "ignored" in tab(s, "token_deltas")
    assert tab(s, "failed_txs")["unexpected"] == 0 and list(tab(s, "failed_txs")["columns"]["old_only_expected"]) == ["signature"]
    assert tab(s, "blocks")["findings"] == ["column legacy only in old"] and tab(s, "blocks")["unexpected"] == 1
    assert s["unexpected"] == 2 + 3 + 1


@pytest.mark.parametrize("case", range(10))
def test_manifest_findings(tmp_path, case):
    old_m, new_m, args, want = {}, {}, [], None
    if case == 0:    # both counters say 51 transactions, the blocks hold 50
        old_m = new_m = {"counters": {"txs_seen": 51}}
        want = "sum(blocks.executed_tx_count) 50 != counters.txs_seen 51"
    elif case == 1:
        new_m, want = {"counters": {"events": 25}}, "counter events: old 24 new 25"
    elif case == 2:
        new_m, want = {"status": "incomplete_coverage"}, "new: status 'incomplete_coverage'"
    elif case == 3:
        new_m, want = {"coverage": {"missing_slots": 3}}, "new: coverage.missing_slots 3"
    elif case == 4:
        new_m, want = {"idl_sha256": {"pump.json": "other"}}, "idl_sha256"
    elif case == 5:
        args, want = ["--expect-new", "threads=6"], "new manifest threads = 1, expected 6"
    elif case == 6:
        new_m, want = {"slot_start": S + 1}, "new: manifest range"
    elif case == 7:  # control: every expectation met
        args = ["--expect-new", "threads=1", "--expect-new", f"extractor_sha256={NEW_SHA}", "--expect-new", "coverage.missing_slots=0"]
    elif case == 8:  # the build under test must count its transactions
        new_m, want = {"counters": {"txs_seen": None}}, "new: no counters.txs_seen"
    elif case == 9:  # control: an old manifest may lack the counter
        old_m = {"counters": {"txs_seen": None}}
    counters = lambda m: m.pop("counters", None)  # noqa: E731
    old = chunk(tmp_path / "a", base(), counters=counters(dict(old_m)), **{k: v for k, v in old_m.items() if k != "counters"})
    new = chunk(tmp_path / "b", base(), counters=counters(dict(new_m)), **{k: v for k, v in new_m.items() if k != "counters"})
    rc, s = diff(tmp_path, old, new, *args)
    c = s["chunks"][NAME]
    assert all(t["unexpected"] == 0 for t in c["tables"].values())
    if want is None:
        assert rc == 0 and c["manifest_findings"] == []
    else:
        assert rc == 1 and any(want in f for f in c["manifest_findings"]), c["manifest_findings"]


def test_manifest_rows_must_match_parquet(tmp_path):
    new = chunk(tmp_path / "b", base())
    m = json.load(open(os.path.join(new, "_manifest.json")))
    m["rows"]["pump/TradeEvent"] += 1
    json.dump(m, open(os.path.join(new, "_manifest.json"), "w"))
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base()), new)
    assert rc == 1 and s["chunks"][NAME]["manifest_findings"] == ["new pump/TradeEvent: manifest rows 25, parquet 24"]


def test_rows_outside_the_chunk_range(tmp_path):
    rows = trades() + [(T + 5, 0, "late", 2, 0, 0, "prefix", None, "ff", None, "m", 1)]
    rc, s = diff(tmp_path, chunk(tmp_path / "a", base(trade_rows=rows)), chunk(tmp_path / "b", base(trade_rows=rows)))
    tr = tab(s, "pump/TradeEvent")
    assert rc == 1 and tr["outside_range"] == {"old": 1, "new": 1} and tr["identical"] == 25 and tr["unexpected"] == 2


def test_extra_root_for_tx_ctx(tmp_path):
    ctx = [(S + i, 1, f"Payer{i}", "1:pump") for i in range(4)]
    chunk(tmp_path / "c", {"tx_ctx": (TXCTX, ctx)}, counters={"txs_seen": None})
    old, new = chunk(tmp_path / "a", base()), chunk(tmp_path / "b", base(extra_new=True))
    rc, s = diff(tmp_path, old, new, "--extra", f"tx_ctx={tmp_path / 'c'}", out="same")
    assert rc == 0 and tab(s, "tx_ctx")["identical"] == 4 and "ignored" not in tab(s, "tx_ctx")
    shutil.rmtree(tmp_path / "c")
    chunk(tmp_path / "c", {"tx_ctx": (TXCTX, ctx[:3] + [(S + 3, 1, "Payer3", "1:pump;2:jupiter")])})
    rc, s = diff(tmp_path, old, new, "--extra", f"tx_ctx={tmp_path / 'c'}", out="other")
    assert rc == 1 and tab(s, "tx_ctx")["column_diffs"] == {"programs": {"raw": 1, "unexpected": 1}}


def test_footprint_reference(tmp_path, capsys):
    old, new = chunk(tmp_path / "a", base()), chunk(tmp_path / "b", base())
    assert cd.main(["footprint", old]) == 0
    ref = json.loads(capsys.readouterr().out)
    shutil.copytree(old, tmp_path / "copy" / NAME)
    assert cd.footprint(str(tmp_path / "copy" / NAME)) == ref[NAME]  # a byte copy has the same footprint
    (tmp_path / "ref.json").write_text(json.dumps(ref))
    rc, s = diff(tmp_path, old, new, "--old-footprint", str(tmp_path / "ref.json"), out="same")
    assert rc == 0
    # the same rows written with another codec: another footer, so another footprint
    f = os.path.join(old, "pump/TradeEvent/part-00000.parquet")
    duckdb.execute(f"COPY (SELECT * FROM '{f}') TO '{tmp_path}/x.parquet' (FORMAT parquet, COMPRESSION zstd)")
    shutil.move(f"{tmp_path}/x.parquet", f)
    rc, s = diff(tmp_path, old, new, "--old-footprint", str(tmp_path / "ref.json"), out="moved")
    assert rc == 1 and s["chunks"][NAME]["manifest_findings"] == [
        "old: 1 files differ from the reference footprint, e.g. ['pump/TradeEvent/part-00000.parquet']"]
    assert tab(s, "pump/TradeEvent")["identical"] == 24


def test_buckets_do_not_change_the_result(tmp_path):
    old_rows, new_rows = trades(), trades()
    new_rows[4] = new_rows[4][:-1] + (1,)
    new_rows.append(new_rows[10])
    del new_rows[20]
    old, new = chunk(tmp_path / "a", base(trade_rows=old_rows)), chunk(tmp_path / "b", base(trade_rows=new_rows))
    got = []
    for b in (1, 3, 8):
        rc, s = diff(tmp_path, old, new, "--buckets", str(b), out=f"b{b}")
        for c in s["chunks"].values():
            c.pop("seconds")
            c.pop("new"), c.pop("old")
        got.append((rc, s["chunks"], s["unexpected"]))
    assert got[0] == got[1] == got[2] and got[0][0] == 1
    tr = got[0][1][NAME]["tables"]["pump/TradeEvent"]
    assert (tr["identical"], tr["differing"], tr["only_old"], tr["duplicates"]["new"]["identical_rows"]) == (22, 1, 1, 1)


def test_refusals(tmp_path, capsys):
    good = chunk(tmp_path / "a", base())
    new = chunk(tmp_path / "b", base())

    def refused(old_dir, new_dir, out, *args):
        before = os.listdir(out) if os.path.isdir(out) else None
        rc = cd.main(["--old", old_dir, "--new", new_dir, "--out", out, "--spill", str(tmp_path / "spill"), *args])
        written = os.listdir(out) if os.path.isdir(out) else None
        return rc == 2 and "REFUSED" in capsys.readouterr().err and written == before

    # paths
    for sub in ("x-holdout", "hold-out", "events/clean-v1/chunks", "Clean"):
        bad = shutil.copytree(good, tmp_path / sub / NAME)
        assert refused(str(bad), new, str(tmp_path / "o1"))
    assert refused(good, new, str(tmp_path / "events" / "o2"))   # an output under events/
    assert refused(good, new, str(tmp_path / "store" / "o3"))    # or under store/
    os.makedirs(tmp_path / "exists")
    assert refused(good, new, str(tmp_path / "exists"))         # an existing output
    # chunk names and manifests reaching the hold-out
    late = f"{T}-{cd.HOLDOUT_START + 216_000}"
    assert refused(str(shutil.copytree(good, tmp_path / "c" / late)), str(shutil.copytree(new, tmp_path / "d" / late)), str(tmp_path / "o4"))
    for n, (lo, hi) in enumerate(((cd.HOLDOUT_START - 1, cd.HOLDOUT_START - 1), (cd.HOLDOUT_START, cd.HOLDOUT_START))):
        # a dev-named chunk whose rows (statistics) reach the slot just below / at the hold-out start
        rows = trades() + [(lo, 0, "z", 2, 0, 0, "prefix", None, "ff", None, "m", 1), (hi, 1, "z", 2, 0, 0, "prefix", None, "ff", None, "m", 1)]
        o = chunk(tmp_path / f"e{n}", base(trade_rows=rows))
        nn = chunk(tmp_path / f"f{n}", base(trade_rows=rows))
        out = str(tmp_path / f"o5-{n}")
        if n == 0:  # control: one slot below the hold-out is read (and outside this chunk's range: unexpected)
            assert cd.main(["--old", o, "--new", nn, "--out", out, "--spill", str(tmp_path / "spill")]) == 1
            capsys.readouterr()
        else:
            assert refused(o, nn, out)
    m = json.load(open(os.path.join(new, "_manifest.json")))
    m["slot_end_exclusive"] = cd.HOLDOUT_START + 1
    mf = shutil.copytree(new, tmp_path / "g" / NAME)
    json.dump(m, open(os.path.join(mf, "_manifest.json"), "w"))
    assert refused(good, str(mf), str(tmp_path / "o6"))
    assert cd.main(["footprint", str(tmp_path / "c" / late)]) == 2
    # the same call with neither trigger runs
    assert cd.main(["--old", good, "--new", new, "--out", str(tmp_path / "ok"), "--spill", str(tmp_path / "spill")]) == 0
