"""The insider share is undefined when the store cannot measure it, and the vetoes are fail-closed
(data-old-faithful-one/lab/research/vins-definition-2026-10-15.md, sha256 b7181aef): circ <= 0,
H_ins > circ (tokens moved out of the store's sight to sell wallets), H_ins < 0, or an unknown
insider set give no number but a reason, and both V_ins (0.90) and the O7 entry veto (25 %) fire."""

from backtest import overlay
from backtest import strategies as S
from backtest.replay import CurveLeg, Stream
from backtest.venues import Curve

C = Curve(1, 1, 1, 1, 95, 30)
W = 37  # 10 s at 267 ms


def ins(*legs, create=100):
    """Insiders over curve legs (slot, trader, signed tokens): the creator is 'creator'."""
    cl = [CurveLeg(s, 0, (s, 0, 0, 0), d > 0, abs(d), 1, 0, "buy" if d > 0 else "sell", C, C, w) for s, w, d in legs]
    return overlay.Insiders(Stream("M", create, "creator", cl), W)


def both_veto(share):
    return S.m1x_vetoed("V_ins", share, 10), overlay.vetoes(share, overlay.VETO_SHARE)


def test_circulation_at_or_below_zero_vetoes():
    # An outsider sells 200 tokens it never bought here (a transfer in): everybody's net is -100.
    i = ins((100, "creator", 100), (500, "x", -200))
    assert i.share_reason(600) == (None, "circ_le_0") and both_veto(i.share(600)) == (True, True)


def test_an_unknown_insider_set_vetoes():
    # No curve leg within c + 10 s: the insiders are not known.
    i = ins((100 + W + 5, "y", 100))
    assert not i.known and i.share_reason(500) == (None, "insiders_unknown") and both_veto(i.share(500)) == (True, True)


def test_a_share_above_one_vetoes_with_its_reason():
    # The bundle pattern: insiders buy 1000, 500 of it leaves by transfer and is sold by an outsider.
    i = ins((100, "creator", 1_000), (500, "x", -500))
    assert i.share_reason(600) == (None, "h_ins_gt_circ") and both_veto(i.share(600)) == (True, True)


def test_insiders_net_sellers_vetoes():
    i = ins((100, "creator", 100), (300, "creator", -300), (400, "y", 1_000))
    assert i.share_reason(500) == (None, "h_ins_lt_0") and both_veto(i.share(500)) == (True, True)


def test_a_defined_share_vetoes_only_above_the_threshold():
    i = ins((100, "creator", 500), (400, "y", 500))
    assert i.share_reason(500) == (0.5, None)
    assert not S.m1x_vetoed("V_ins", i.share(500), 10)  # 0.5: no V_ins veto (0.90) ...
    assert overlay.vetoes(i.share(500), overlay.VETO_SHARE)  # ... but above O7's 25 %
    j = ins((100, "creator", 200), (400, "y", 800))
    assert j.share(500) == 0.2 and not overlay.vetoes(j.share(500), overlay.VETO_SHARE)


def test_the_o7_table_counts_the_undefined_vetoes(tmp_path):
    """O7 rows carry ins_reason: the paired table counts the vetoes for an undefined share apart
    (vetoed 100 %, of which undefined 50 %), and a run without the column counts none."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from backtest.report import overlay_effects
    from backtest.store import load_defaults

    sol = 1_000_000_000
    rows = []  # A: share 0.4 > 25 %; B: share undefined (h_ins_gt_circ). Both vetoed at O7V1.
    for mint, t, share, why in (("A", 100, 0.4, None), ("B", 200, None, "h_ins_gt_circ")):
        for ov, reason in (("none", "time"), ("O7V1", "o7_veto")):
            rows.append(dict(family="F7", variant="tp4_T600", size_sol=0.5, d=1, tau="empirical", q=0.5, mint=mint, t=t,
                             pool=None, overlay=ov, day="2026-09-01", skipped=False, entry_failed=False, cost=int(0.5 * sol),
                             proceeds=int(0.4 * sol), exit_attempts=1, exit_reason=reason, exit_slot=t + 50,
                             ins_share=share if ov != "none" else None, ins_reason=why if ov != "none" else None))
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "r.parquet")
    (o,) = overlay_effects(str(tmp_path / "r.parquet"), load_defaults())
    assert (o["n"], o["veto_share"], o["undefined_share"]) == (2, 1.0, 0.5)
    pq.write_table(pa.Table.from_pylist([{k: v for k, v in r.items() if k not in ("ins_share", "ins_reason")} for r in rows]),
                   tmp_path / "old.parquet")
    (o,) = overlay_effects(str(tmp_path / "old.parquet"), load_defaults())
    assert o["undefined_share"] == 0.0


def test_the_v_ins_delta_as_it_runs_and_over_the_defined_triggers():
    """analysis._m1x_delta: the rule as it runs pairs every trigger (an undefined share vetoed), the
    diagnosis only those with a defined share (vins-definition-2026-10-15.md, point 3)."""
    import duckdb

    from backtest import analysis

    con = duckdb.connect()
    con.execute("""CREATE TABLE m (family VARCHAR, variant VARCHAR, size_sol DOUBLE, d INTEGER, tau VARCHAR, q DOUBLE, mint VARCHAR, t BIGINT,
                   pool VARCHAR, overlay VARCHAR, day VARCHAR, period VARCHAR, skipped BOOLEAN, exit_reason VARCHAR, pnl BIGINT,
                   ins_reason VARCHAR)""")
    # Three triggers (base pnl -1, -2, +1 SOL): A vetoed for 0.95, B vetoed as undefined, C kept.
    for mint, pnl, vetoed, why in (("A", -1, True, None), ("B", -2, True, "circ_le_0"), ("C", 1, False, None)):
        for ov in ("none", "V_ins"):
            veto = ov == "V_ins" and vetoed
            con.execute("INSERT INTO m VALUES ('M1P', 'v', 0.5, 1, 'empirical', 0.5, ?, 1, NULL, ?, '2026-09-25', 'days 10-18', ?, ?, ?, ?)",
                        [mint, ov, veto, "veto" if veto else "time", pnl * 10**9, why])
    run = analysis._m1x_delta(con, "M1P", "V_ins", 0.5, 1, "days 10-18")
    diag = analysis._m1x_delta(con, "M1P", "V_ins", 0.5, 1, "days 10-18", defined_only=True)
    assert (run["pairs"], round(run["veto_share"], 3), round(run["delta_sol"], 6)) == (3, 0.667, 1.0)  # (+1 + 2 + 0) / 3
    assert (diag["pairs"], diag["veto_share"], diag["delta_sol"]) == (2, 0.5, 0.5)  # (+1 + 0) / 2
