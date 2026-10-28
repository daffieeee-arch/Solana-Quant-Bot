"""Per-exit rule 2, including a restart of an unmodified historical rule-1 journal (synthetic data only)."""

import sqlite3

import pytest

from paper import stoprule
from paper.engine import Engine
from paper.tests import c2_position_cases as C2
from paper.tests.test_writeoff import CAP, D, T0, crash, drained, live_engine, ticker


def migration_case():
    c = C2.Case("settlement_migration", "old write-off before crash, a new one after restart", n2_k=0)
    old = drained(c, "OLD", T0 + 100)
    new = drained(c, "NEW", T0 + 2_600)
    ticker(c, list(range(T0 + 250, T0 + 5_001, 250)))
    return c.build(), old, new


def test_old_rows_survive_restart_and_new_writeoffs_pay_every_failure(tmp_path):
    case, old, new = migration_case()
    directory = str(tmp_path / "journal")
    first = live_engine(case, directory, crash_at=T0 + 2_500)
    first.settle_rule = 1  # reproduce the previous writer on the fixture
    hwm = crash(first)
    with sqlite3.connect(str(tmp_path / "journal" / "journal.sqlite")) as db:
        # Genuine legacy schema: no row-level rule and no journal-wide rule metadata.
        db.execute("ALTER TABLE exits DROP COLUMN settle_rule")
        before = db.execute("SELECT * FROM exits WHERE key = ?", [old]).fetchone()
        assert before is not None
    resumed = live_engine(case, directory)
    assert resumed.replaying and resumed.settle_rule == 2
    resumed.run()
    rows = {x["key"]: x for x in resumed.journal.rows("exits")}
    assert set(rows) == {old, new}
    assert tuple(v for k, v in rows[old].items() if k != "settle_rule") == before
    assert rows[old]["settle_rule"] is None and rows[new]["settle_rule"] == 2
    assert resumed.journal.state("settle_rule") is None  # historical fallback never upgraded in place
    x = rows[new]
    assert x["exit_slot"] == T0 + 2_600 + D + CAP + D > hwm["slot"]
    assert x["exit_reason"] == "written_off" and x["exit_attempts"] == 2
    scn = resumed.scn
    assert x["pnl"] == scn.exit_net(0) - 2 * scn.failed_tx - scn.entry_total(x["cost"])
    resumed.journal.close()
    again = live_engine(case, directory)
    again.run()
    assert {x["key"]: x for x in again.journal.rows("exits")} == rows
    again.journal.close()


def test_cost_breakdown_preserves_each_rows_rule():
    from dataclasses import asdict
    from backtest.costs import Scenario
    from backtest.store import load_defaults

    scn = asdict(Scenario.from_cfg("base", load_defaults()))
    row = dict(tokens=10, cost=500_000_000, proceeds=0, exit_attempts=2,
               exit_slot=30, exit_reason="written_off")
    old, new = row.copy(), dict(row, settle_rule=2)
    got = stoprule.costs([old, new], scn, rule=1)
    assert got["failed_exit_attempts"] == 3 * scn["failed_tx"]
    assert stoprule.row_settle_rule(old) == 1 and stoprule.row_settle_rule(new) == 2
    assert stoprule.row_settle_rule(dict(row, settle_rule=None), 2) == 2


@pytest.mark.parametrize("bad", [0, 3, "unknown", False])
def test_unknown_rule_cannot_silently_use_the_current_rule(bad):
    with pytest.raises(ValueError, match="settlement rule"):
        stoprule.row_settle_rule({"settle_rule": bad})
    with pytest.raises(ValueError, match="settlement rule"):
        Engine(None, None, None, settle_rule=bad)
