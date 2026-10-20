"""lab/live/m3_11_accept.py: M3-11's acceptance (spec v0.3 §5) on hand-made journals and summaries."""

import importlib.util
import json
import os

from paper.journal import Journal

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("m3_11_accept", os.path.join(HERE, "..", "..", "live", "m3_11_accept.py"))
acc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acc)

SEED = {"complete_from": 30, "first_live_slot": 10, "h24": 20, "hi": 10, "lo": 10}
LAT = {"block_time": 100, "received_unix_ns": 100_400_000_000, "created_unix_ns": 100_300_000_000, "latency_ms": 400,
       "segment_written_unix": 102, "segment_lag_ms": 2_000, "d_ms": 536}


def journal(path, store, rows=None, states=None, learned=True):
    """A journal of a live run: a K3 row entered (farm_complete 0) and H's skipped 'farm history incomplete', its order,
    fills and exit, a learned and a usage row, and the live states (the seed naming `store`)."""
    j = Journal(str(path))
    for k, v in {"groups": "G", "cost_scenario": "C", "feed": '{"kind": "live", "from": null, "to": null}',
                 "farmer_seed": json.dumps({**SEED, "store": store}, sort_keys=True),
                 "farmer_seeds": json.dumps([{**SEED, "store": store}], sort_keys=True), "farm_flag_fixes": '{"2026-09-22": [1, 0]}',
                 **(states or {})}.items():
        if v is not None:
            j.state(k, v)
    sig = dict(variant="tp4_T600", key="P", t=10, day="2026-09-22", venue="pool", orientation="reversed", farm="organic", farm_complete=0)
    j.put("signals", family="F7", status="entered", **sig, **LAT)
    j.put("signals", family="F7", **{**sig, "variant": "flow300_H6"}, status="skipped", reason="farm history incomplete", **LAT)
    for r in rows or ():
        j.put("signals", **r)
    j.put("orders", family="F7", variant="tp4_T600", key="P", t=10, entry_slot=12, size=5, status="filled")
    j.put("fills", family="F7", variant="tp4_T600", key="P", t=10, side="buy", slot=12, tokens=7, quote=5)
    j.put("exits", family="F7", variant="tp4_T600", key="P", t=10, entry_slot=12, exit_slot=40, pnl=1)
    if learned:
        j.put("learned", kind="pool", key="PN", seq=1, slot=11, info="[]")
    j.put("usage", minute=7, day="1970-01-01", stream="a", bytes=5, seq=0)
    j.commit()


def summaries(out, prefix="", **change):
    sp = {"stats": {"segments": 4, "tokens_dropped": 2, "farmer_flags_fixed": 1, "seeded_pools": 3}, "farm_seed": SEED}
    rs = {"events": 10, "follow": {"stats": {"segments": 4, "tokens_dropped": 2, "farmer_flags_fixed": 1, "seeded_pools": 3}, "token_forgets": 2}}
    for name, v in (("py", sp), ("rs1", rs), ("rs2", rs)):
        v = json.loads(json.dumps(v))
        for k, f in change.items():
            if k.startswith(name + "_"):
                f(v)
        with open(os.path.join(out, prefix + name, "summary.json"), "w") as fh:
            json.dump(v, fh)


def make(tmp_path, prefix="", **kw):
    for name, store in (("py", "/abs/fwd.duckdb"), ("rs1", "/x/snap-store"), ("rs2", "/x/snap-store")):
        journal(tmp_path / (prefix + name), store, **kw.get(name, {}))
    summaries(str(tmp_path), prefix, **kw.get("summaries", {}))
    return str(tmp_path)


def test_the_strict_rules(tmp_path):
    out = make(tmp_path / "ok")
    red, info = acc.strict(out, "", 4, 10, incomplete=True, seeded=True)
    assert red == [], red
    assert any("flag fixes counted: 1" in i for i in info) and any("tokens forgotten: 2" in i for i in info)
    # each rule on its own
    cases = {
        "journal_diff": {"rs1": {"rows": [dict(family="F7", variant="tp4_T600", key="Q", t=20, status="skipped", reason="busy")]}},
        "learned:": {"rs1": {"learned": False}},
        "state feed": {"rs1": {"states": {"feed": '{"kind": "replay", "from": null, "to": null}'}}},
        "state farmer_seed ": {"rs1": {"states": {"farmer_seed": json.dumps({**SEED, "lo": 9, "store": "s"}, sort_keys=True)}}},
        "state farm_flag_fixes": {"rs1": {"states": {"farm_flag_fixes": None}}},
        "the two lab-live journals differ": {"rs2": {"rows": [dict(family="F7", variant="tp4_T600", key="Q", t=20)]}},
        "not 10": {"summaries": {"rs2_": lambda v: v.update(events=11)}},
        "segments read": {"summaries": {"py_": lambda v: v["stats"].update(segments=5)}},
        "tokens:": {"summaries": {"rs1_": lambda v: v["follow"].update(token_forgets=1)}},
        "farmer_flags_fixed": {"summaries": {"py_": lambda v: v["stats"].update(farmer_flags_fixed=0)}},
        "seeded_pools": {"summaries": {"rs1_": lambda v: v["follow"]["stats"].update(seeded_pools=0)}},
    }
    for i, (why, kw) in enumerate(cases.items()):
        out = make(tmp_path / f"c{i}", **kw)
        red, _ = acc.strict(out, "", 4, 10, incomplete=True, seeded=True)
        assert any(why in r for r in red), (why, red)
    # no flag fix and no dropped token: green, the log names what rests on the golden cases
    nothing = {k: {"states": {"farm_flag_fixes": None}} for k in ("py", "rs1", "rs2")}
    nothing["summaries"] = {f"{n}_": (lambda v: (v.get("follow", v)["stats"].update(tokens_dropped=0), v.get("follow", {}).update(token_forgets=0)))
                            for n in ("py", "rs1", "rs2")}
    red, info = acc.strict(make(tmp_path / "none", **nothing), "", 4, 10)
    assert red == [] and any("rests on the golden case alone" in i and "fix" in i for i in info), (red, info)
    assert any("the forget rests on the golden case alone" in i for i in info)


def test_an_empty_table_or_no_first_day_is_red(tmp_path):
    import sqlite3

    out = make(tmp_path / "e")
    for name in ("py", "rs1", "rs2"):
        db = sqlite3.connect(str(tmp_path / "e" / name / "journal.sqlite"))
        db.execute("DELETE FROM exits")
        db.execute("UPDATE signals SET farm_complete = 1, reason = NULL")
        db.commit()
        db.close()
    red, _ = acc.strict(out, "", 4, 10, incomplete=True)
    assert any(r.startswith("exits:") for r in red) and any("first day is not exercised" in r for r in red), red


def test_the_arrivals_rules_need_the_three_kinds_of_rows(tmp_path):
    fixture = {"no_created": 20, "no_block_time": 30}
    nulls = {k: None for k in LAT}
    extra = [dict(family="F7", variant="tp4_T600", key="B", t=20, **{**LAT, "created_unix_ns": None}),
             dict(family="F7", variant="tp4_T600", key="C", t=30, **nulls)]
    out = make(tmp_path / "a", "arr-", **{n: {"rows": extra} for n in ("py", "rs1", "rs2")})
    red, info = acc.arrivals(out, "arr-", 4, fixture)
    assert red == [], red
    assert any("all seven set 2, no created_unix_ns 1, all NULL (no block time) 1" in i for i in info), info
    # a measured column differs (journal_diff leaves it out, the arrivals rule does not); a kind of row missing
    other = [dict(extra[0], latency_ms=401), extra[1]]
    out = make(tmp_path / "b", "arr-", py={"rows": extra}, rs1={"rows": other}, rs2={"rows": other})
    red, _ = acc.arrivals(out, "arr-", 4, fixture)
    assert any("seven live columns differ on 1 rows" in r for r in red), red
    out = make(tmp_path / "c", "arr-", **{n: {"rows": extra[:1]} for n in ("py", "rs1", "rs2")})
    red, _ = acc.arrivals(out, "arr-", 4, fixture)
    assert red == ["no row with all NULL at the no_block_time slot: the fixture does not exercise it"], red


def test_main_prints_the_verdict(tmp_path, capsys):
    out = make(tmp_path / "m")
    assert acc.main(["strict", out, "", "4", "10", "--incomplete", "--seeded"]) == 0
    assert "M3-11 job 91 strict: GREEN" in capsys.readouterr().out
    assert acc.main(["strict", out, "", "5", "-"]) == 1
    assert "M3-11 job 91 strict: RED" in capsys.readouterr().out
