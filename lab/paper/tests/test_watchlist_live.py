"""The live watchlist (paper.watchlist_live): #180's state machine fed by the engine.

On complete data (the replay of the dev store) its list per minute equals the batch measurement's
(wallets.watchlist over the store's per-(pool, minute) aggregates, cold start at the same slot),
except that the batch lists a new M1 pool already for its own creation minute.
Synthetic: entry at 120 SOL when organic, exit below 100, a late backfill counts in the organic
window, and the file the recorder reads."""

import json
import os
from types import SimpleNamespace as NS

import pytest

from paper.feed import Feed, PoolInfo
from paper.watchlist_live import LiveWatchlist

SOL = 1_000_000_000
STORE = "/home/chupa/Solana-project/data-old-faithful-one/lab/store/dev2.duckdb"


class Pools(Feed):
    def __init__(self):
        self.pools = {"P": PoolInfo("P", "M", "reversed", "reversed", False, None, 10)}
        self.new_pools = []

    def pool(self, p):
        return self.pools.get(p)

    def token(self, m):
        return None


def leg(slot, sol, depth, farmer=False):
    # a reversed pool: SOL is the base side (sol_side: kind == 'sell' is a token buy, base, pre.b)
    return NS(kind="sell", slot=slot, order=(slot, 0, 0, 0), farmer=farmer, base=int(sol * SOL), pre=NS(b=int(depth * SOL)))


def test_entry_exit_late_history_and_the_file(tmp_path):
    w = LiveWatchlist(Pools(), str(tmp_path))
    w.slot(100, 1000)
    w.trades("P", 1000, [leg(100, 3, 130)])  # deep and organic (3 SOL, no farmer)
    assert w.minute(1001, 300) == {"P": ("f7_rev",)}
    with open(tmp_path / "watchlist-current.json") as f:
        snap = json.load(f)
    assert snap["valid_for_minute"] == 1001 and snap["pools"] == [{"pool": "P", "mint": "M", "rules": ["f7_rev"]}]
    assert snap["source"]["received_pools_only"] and snap["counts"]["f7_rev"] == 1
    # Silent minutes are stepped too; still on the list at 101 SOL (exit below 100).
    w.slot(900, 1004)
    w.trades("P", 1004, [leg(900, 1, 101)])
    assert w.minute(1005, 1100) == {"P": ("f7_rev",)}
    w.slot(1200, 1005)
    w.trades("P", 1005, [leg(1200, 1, 99)])
    assert w.minute(1006, 1300) == {}
    # A backfill of farming trades in an earlier minute turns the trailing hour farming.
    w.slot(1400, 1006)
    w.trades("P", 1006, [leg(1400, 1, 130)])
    w.trades("P", 1003, [leg(800, 20, 120, farmer=True)], late=True)
    assert w.minute(1007, 1500) == {}  # deep again, but farmers made most of the hour's volume
    lines = (tmp_path / "watchlist-log.jsonl").read_text().splitlines()
    assert [json.loads(x)["minute"] for x in lines] == [1001, 1005, 1006, 1007]


def test_equal_to_the_batch_measurement_on_complete_data(tmp_path):
    """Engine on the dev replay with the live watchlist = wallets.watchlist over the store, per minute."""
    if not os.path.exists(STORE):
        pytest.skip("dev store not built")
    from backtest import streams
    from backtest.strategies import Clock
    from paper.engine import Engine
    from paper.feed import ReplayFeed
    from paper.journal import Journal
    from wallets import watchlist as W

    lo, hi = 450_700_000, 450_706_000
    con = streams.open_store(STORE, threads=2, memory="1GB")
    feed = ReplayFeed(STORE, lo, hi, con=con)
    watch = LiveWatchlist(feed, str(tmp_path))
    Engine(feed, Journal(str(tmp_path / "j")), None, variants=[(0.04, 600)], ignore_risk=True, n2_k=0,
           clock=Clock.from_store(con), backup=False, watchlist=watch).run()
    live = {}
    for line in (tmp_path / "watchlist-log.jsonl").read_text().splitlines():
        x = json.loads(line)
        live[x["minute"]] = set(x["pools"])
    # The batch: the same rules over the store's aggregates, cold start at lo (as the engine).
    pools = con.execute(W.pools_sql(lo, hi)).fetchall()
    cmin = W.minute_of_slot(con, {r[5] for r in pools if r[5] is not None and r[5] >= lo})
    wl = W.Watchlist(W.Rules())
    for pool, mint, qc, orient, mayhem, cslot, gic in pools:
        wl.add_pool(W.PoolInfo(pool, mint, qc, orient, bool(mayhem), cmin.get(cslot), bool(gic)))
    agg = con.execute(W.aggregates_sql(lo, hi)).fetchall()
    by = {}
    for pool, m, trades, sol, farmer_sol, depth, _ in agg:
        by.setdefault(m, []).append((pool, trades, sol, farmer_sol, depth))
    batch = {}
    for m in range(min(by), max(by) + 1):
        batch[m + 1] = dict(wl.step(m, by.get(m, [])))
    common = sorted(set(live) & set(batch))
    assert len(common) >= 20
    # The batch puts an M1 pool on the list for the minute it is created in (it knows every pool up
    # front); live, the pool exists only from its creation slot, so from the next minute on.
    creation_minute = {(m, p) for m in common for p, tags in batch[m].items()
                       if tags == ("m1",) and wl.pools[p].create_minute == m and p not in live[m]}
    diffs = {m: d for m in common if (d := {p for p in live[m] ^ set(batch[m]) if (m, p) not in creation_minute})}
    assert not diffs, {m: sorted(d)[:3] for m, d in list(diffs.items())[:3]}
    assert all(p in live[m + 1] for m, p in creation_minute if m + 1 in live and p in batch.get(m + 1, {}))
    assert sum(len(batch[m]) for m in common) > 100  # the lists are not empty
    assert sum(1 for m in common for t in batch[m].values() if "f7_rev" in t) > 0


def test_backfill_from_before_the_first_slot_gets_its_own_minute(tmp_path):
    """A probe's backfill from before the first slot seen has no known block time: its minute is
    estimated back with the clock, never the open minute (it used to count as this minute's trades)."""
    from backtest.strategies import Clock

    w = LiveWatchlist(Pools(), str(tmp_path), clock=Clock({0: 400.0}))
    w.slot(100_000, 5_000)  # the first slot seen: minute 5000
    w.history("P", [leg(100_000 - 9_000, 20, 120, farmer=True)])  # 9,000 slots x 400 ms: an hour before
    assert ("P", 5_000) not in w.rows and w.rows[("P", 4_940)][0] == 1
    w.history("P", [leg(100_000, 1, 130)])  # a slot that was seen: its own minute, the open one
    assert w.rows[("P", 5_000)][0] == 1
    # Without a clock the default slot time estimates it; still not the open minute.
    w2 = LiveWatchlist(Pools(), str(tmp_path / "b"))
    w2.slot(100_000, 5_000)
    w2.history("P", [leg(100_000 - 9_000, 20, 120)])
    assert ("P", 5_000) not in w2.rows and len(w2.rows) == 1


def test_a_pruned_pool_whose_archive_expired_is_added_again(tmp_path):
    """wallets.watchlist (#211) prunes quiet pools and archives their verdict; once the archive entry
    expired the pool is unknown there. Its next trade must add it again (as the batch re-add does),
    not be ignored because the live watchlist added it once."""
    w = LiveWatchlist(Pools(), str(tmp_path))
    w.slot(100, 1000)
    w.trades("P", 1000, [leg(100, 3, 130)])
    assert "P" in w.wl.pools
    w.wl.pools.pop("P")  # what pruning plus an expired archive leave behind
    w.wl.state.pop("P")
    getattr(w.wl, "archive", {}).pop("P", None)
    w.trades("P", 1001, [leg(400, 3, 130)])
    assert "P" in w.wl.pools and w.rows[("P", 1001)][0] == 1


def test_a_trade_on_a_pruned_archived_pool_restores_it(tmp_path):
    """The crash of shadow-1052-a8e69ed (KeyError in trades): a pool pruned to the archive (#211) gets a
    trade. The trade path takes its orientation from the archive entry, the minute's rows carry the
    trade, and step() restores the pool with its organic verdict (the batch's route). A late
    (backfill) trade on it is ignored like the batch ignores history of an untracked pool."""
    from wallets import watchlist as W

    w = LiveWatchlist(Pools(), str(tmp_path), rules=W.Rules(prune_idle_minutes=5))
    w.slot(100, 1000)
    w.trades("P", 1000, [leg(100, 3, 50)])  # organic but shallow: no member (members are never pruned)
    assert w.minute(1001, 300) == {}
    w.slot(5_000, 1010)
    w.minute(1011, 5_000)  # ten quiet minutes: pruned into the archive with its organic verdict
    assert "P" not in w.wl.pools and w.wl.archive["P"][1] is True
    w.trades("P", 1011, [leg(5_100, 2, 50)])  # was: KeyError
    w.trades("P", 1004, [leg(1_000, 1, 50)], late=True)  # history of an archived pool: ignored, no error
    assert w.rows[("P", 1011)][0] == 1
    w.slot(5_400, 1012)
    w.minute(1012, 5_400)
    assert "P" in w.wl.pools and w.wl.restored_total == 1 and w.wl.state["P"].organic is True



class TwoPools(Pools):
    """P (reversed, deep and organic: f7_rev) and S (sol/normal, created live at slot 150: M1 for its first hours)."""

    def __init__(self):
        super().__init__()
        self.pools["S"] = PoolInfo("S", "MS", "normal", "sol", False, None, 150)
        self.new_pools = ["S"]


def test_without_m1_the_list_keeps_its_f7_rev_pools_only(tmp_path):
    """--watchlist-no-m1 (paper.engine watchlist_rules): no pool is tagged m1, and the f7_rev pools are the default's;
    the default rules are unchanged (None: wallets.watchlist.Rules())."""
    from paper.engine import watchlist_rules
    from wallets import watchlist as W

    assert watchlist_rules() is None and watchlist_rules(False) is None
    assert watchlist_rules(True) == W.Rules(m1_max_age_minutes=-1)
    assert {k: v for k, v in vars(watchlist_rules(True)).items() if k != "m1_max_age_minutes"} == \
        {k: v for k, v in vars(W.Rules()).items() if k != "m1_max_age_minutes"}
    out = {}
    for name, rules in (("default", watchlist_rules(False)), ("no_m1", watchlist_rules(True))):
        (tmp_path / name).mkdir()
        w = LiveWatchlist(TwoPools(), str(tmp_path / name), rules=rules)
        w.slot(100, 1000)
        w.trades("P", 1000, [leg(100, 3, 130)])
        w.slot(150, 1000)
        out[name] = [w.minute(1001, 300)]
        w.slot(400, 1001)
        w.trades("P", 1001, [leg(400, 1, 125)])
        out[name].append(w.minute(1002, 500))
    assert out["default"] == [{"P": ("f7_rev",), "S": ("m1",)}] * 2
    assert out["no_m1"] == [{"P": ("f7_rev",)}] * 2


@pytest.mark.parametrize("argv", [[], ["--live", "/x", "--no-watchlist"]])
def test_the_no_m1_flag_needs_the_live_watchlist(tmp_path, argv):
    from paper.engine import main

    with pytest.raises(SystemExit) as e:
        main(["--journal", str(tmp_path / "j"), "--watchlist-no-m1"] + argv)
    assert e.value.code == 2


def test_every_start_that_changes_the_watchlist_setting_journals_it(tmp_path, monkeypatch):
    """LAB review on #489: journal state 'watchlist_rules' (paper.engine record_watchlist_rules), written before the feed.
    A first start with the default rules, a restart with --watchlist-no-m1 (from its HWM), the same again (no entry) and
    one with --no-watchlist."""
    from dataclasses import asdict

    from paper import engine as E
    from paper import live
    from paper import restart as RS
    from paper.journal import Journal
    from paper.tests.test_livefeed_state import fake_store, segment
    from wallets import watchlist as W

    root, jdir = tmp_path / "rec", str(tmp_path / "j")
    for i in range(3):
        segment(root, i, 1_000 + 100 * i, 1_099 + 100 * i, 1_700_000_000 + 60 * i, [])
    store = fake_store(tmp_path / "s.duckdb", range(0, 10, 10))

    class Stop(Exception):
        pass

    def feed(*a, **k):
        raise Stop

    monkeypatch.setattr(live, "LiveFeed", feed)

    def start(*extra):
        with pytest.raises(Stop):
            E.main(["--live", str(root), "--catch-up", "--journal", jdir, "--store", store, "--no-backup", *extra])
        j = Journal(jdir)
        try:
            return json.loads(j.state("watchlist_rules"))
        finally:
            j.close()

    default = {"from_hwm_slot": None, "watchlist": True, "m1": True, "rules": asdict(W.Rules())}
    assert start() == [default]
    assert start() == [default]  # unchanged: no entry
    j = Journal(jdir)
    j.state(RS.HWM_KEY, json.dumps({"slot": 1_250, "minute": None, "day": None}))
    j.state(RS.HWM_STATE_KEY, json.dumps({"groups": {}, "busy": []}))
    j.commit()
    j.close()
    no_m1 = {"from_hwm_slot": 1_250, "watchlist": True, "m1": False, "rules": asdict(W.Rules(m1_max_age_minutes=-1))}
    assert start("--watchlist-no-m1") == [default, no_m1]
    assert start("--watchlist-no-m1") == [default, no_m1]
    off = {"from_hwm_slot": 1_250, "watchlist": False, "m1": False, "rules": None}
    assert start("--no-watchlist") == [default, no_m1, off]
