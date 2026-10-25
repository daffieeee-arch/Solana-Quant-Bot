"""Deployment from ab806e1: rule 2 for new exits, stored settlements across old and mixed restarts."""

import json
import sqlite3
from dataclasses import asdict

import pytest

from backtest.costs import ENGINE_SETTLE_RULE
from backtest.replay import Result
from paper import restart as RS
from paper.engine import Open, Pending
from paper.stoprule import costs, row_settle_rule
from paper.tests.test_restart import CRASH, TOKENS, Later, content, crash, engine, wallet


def test_old_schema_restart_preserves_history_then_writes_rule2(tmp_path):
    old = engine(tmp_path, "journal", Later(TOKENS, crash_after=CRASH), settle_rule=1)
    hwm = crash(old)
    old.journal.close()
    # A genuine pre-migration schema, not merely a new schema with a chosen rule.
    with sqlite3.connect(tmp_path / "journal" / "journal.sqlite") as db:
        db.execute("ALTER TABLE exits DROP COLUMN settle_rule")
        previous = db.execute("SELECT * FROM exits").fetchall()
    new = engine(tmp_path, "journal", Later(TOKENS))
    assert new.settle_rule == ENGINE_SETTLE_RULE == 2 and new.replaying
    new.run()
    rows = new.journal.rows("exits")
    assert [(x["key"], x["settle_rule"]) for x in rows] == [("A", None), ("B", 2)]
    assert [tuple(v for k, v in rows[0].items() if k != "settle_rule")] == previous
    assert new.journal.state("settle_rule") is None
    rec = json.loads(new.journal.state(RS.RESTARTS_KEY))[-1]
    assert rec["hwm"] == hwm["slot"]
    assert {k: v for k, v in rec["counts"].items() if k not in RS.BEFORE} == {}
    def balances(j):
        # A wallet export's observation slot/time can refresh on resume; its money and transactions cannot.
        return {k: v for k, v in wallet(j).items() if k not in ("slot", "time_unix")}

    before = content(new.journal), balances(new.journal), new.journal.state(RS.HWM_STATE_KEY)
    new.journal.close()
    again = engine(tmp_path, "journal", Later(TOKENS))
    again.run()
    assert (content(again.journal), balances(again.journal), again.journal.state(RS.HWM_STATE_KEY)) == before
    assert again.journal.state("settle_rule") is None
    again.journal.close()


@pytest.mark.parametrize("rule,failed", [(1, 1), (2, 2)])
def test_written_off_exit_writer_books_its_recorded_rule(tmp_path, rule, failed):
    e = engine(tmp_path, "journal", Later({}), settle_rule=rule)
    e.day = "2026-10-25"
    p = Pending("F7", "tp4_T600", "drained", "pool", "reversed", 100, 101, (.04, 600), 0)
    r = Result("drained", 100, entry_slot=101, cost=500_000_000, tokens=1_000,
               exit_decision_slot=701, exit_slot=702, exit_reason="written_off", exit_attempts=2)
    e._settle_if_done(Open(p, r, None, 101), 702, force=True)
    x, = e.journal.rows("exits")
    expected = e.scn.exit_net(0) - e.scn.entry_total(r.cost) - failed * e.scn.failed_tx
    assert (x["settle_rule"], x["pnl"], e.day_loss, e.wallet["realized"]) == (rule, expected, expected, expected)
    assert costs([x], asdict(e.scn))["failed_exit_attempts"] == failed * e.scn.failed_tx
    e.journal.close()


@pytest.mark.parametrize("rule", [0, 3, "2", True, None])
def test_engine_rejects_unsupported_settlement_rule(tmp_path, rule):
    with pytest.raises(ValueError, match="unsupported paper settlement rule"):
        engine(tmp_path, "journal", Later({}), settle_rule=rule)


def test_historical_fallback_and_mixed_rows_keep_their_original_costs():
    old = dict(tokens=1000, cost=500_000_000, proceeds=0, exit_attempts=2, exit_slot=702,
               exit_reason="written_off", settle_rule=None)
    new = dict(old, settle_rule=2)
    scn = dict(land_prob=1, failed_tx=505_000, fee_per_tx=605_000, tip=100_000, ata_rent=1_513_840,
               rent_mode="refunded_on_full_exit")
    assert row_settle_rule(old) == 1 and row_settle_rule(new) == 2
    assert costs([old, new], scn)["failed_exit_attempts"] == 3 * scn["failed_tx"]
    assert costs([old, new], scn, rule=2)["failed_exit_attempts"] == 4 * scn["failed_tx"]
