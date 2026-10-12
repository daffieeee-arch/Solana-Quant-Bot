"""Watchlist rules on small fixtures: entry, hysteresis, farming hour, idle exit, M1 age, snapshot."""

from wallets.watchlist import PoolInfo, Rules, Watchlist


def rev(pool="R", mayhem=False):
    return PoolInfo(pool, "mint" + pool, "reversed", "reversed", mayhem, None, False)


def normal(pool="N", create_minute=100, grad_in_create=False):
    return PoolInfo(pool, "mint" + pool, "sol", "normal", False, create_minute, grad_in_create)


def test_default_thresholds_are_120_in_100_out():
    assert Rules().f7_depth_enter_sol == 120.0 and Rules().f7_depth_exit_sol == 100.0   # coordinator decision 2026-10-12
    wl = Watchlist(Rules())
    wl.add_pool(rev())
    assert wl.step(0, [("R", 5, 10.0, 0.0, 119.0)]) == {}
    assert wl.step(1, [("R", 5, 10.0, 0.0, 120.0)]) == {"R": ("f7_rev",)}
    assert wl.step(2, [("R", 5, 10.0, 0.0, 100.0)]) == {"R": ("f7_rev",)}
    assert wl.step(3, [("R", 5, 10.0, 0.0, 99.0)]) == {}


def test_f7_reversed_enters_at_150_and_leaves_below_120():
    wl = Watchlist(Rules(f7_depth_enter_sol=150.0, f7_depth_exit_sol=120.0))
    wl.add_pool(rev())
    assert wl.step(0, [("R", 5, 10.0, 0.0, 140.0)]) == {}                    # too shallow
    assert wl.step(1, [("R", 5, 10.0, 0.0, 151.0)]) == {"R": ("f7_rev",)}    # deep and organic (0 % farmers)
    assert wl.step(2, [("R", 5, 10.0, 0.0, 130.0)]) == {"R": ("f7_rev",)}    # hysteresis: stays above 120
    assert wl.step(3, [("R", 5, 10.0, 0.0, 119.0)]) == {}                    # out
    assert wl.step(4, [("R", 5, 10.0, 0.0, 149.0)]) == {}                    # needs 150 again to re-enter
    assert wl.step(5, [("R", 5, 10.0, 0.0, 150.0)]) == {"R": ("f7_rev",)}


def test_f7_reversed_farming_hour_excludes_and_recovers():
    wl = Watchlist(Rules())
    wl.add_pool(rev())
    assert wl.step(0, [("R", 10, 100.0, 80.0, 500.0)]) == {}                 # 80 % farmer volume: flagged
    for m in range(1, 30):
        wl.step(m, [])
    assert wl.step(30, [("R", 2, 20.0, 0.0, 500.0)]) == {}                   # hour now 80 / 120 = 67 %: still flagged
    for m in range(31, 60):
        wl.step(m, [])
    # the window is (m - 60, m]: at minute 60 the minute-0 row has left, the hour is the 20 SOL of minute 30 at 0 %
    assert wl.step(60, []) == {"R": ("f7_rev",)}
    assert wl.state["R"].win_sol == 20.0 and wl.state["R"].win_farmer == 0.0


def test_f7_organic_verdict_needs_volume_and_persists():
    wl = Watchlist(Rules(organic_min_sol=5.0))
    wl.add_pool(rev())
    assert wl.step(0, [("R", 1, 1.0, 0.0, 200.0)]) == {}                     # < 5 SOL in the hour: no verdict yet
    assert wl.step(1, [("R", 1, 6.0, 0.0, 200.0)]) == {"R": ("f7_rev",)}     # enough volume, organic
    for m in range(2, 70):
        wl.step(m, [])
    assert wl.step(70, [("R", 1, 0.5, 0.0, 200.0)]) == {"R": ("f7_rev",)}    # tiny volume: previous verdict stands


def test_f7_idle_exit_after_six_hours():
    wl = Watchlist(Rules())
    wl.add_pool(rev())
    assert wl.step(0, [("R", 3, 20.0, 0.0, 300.0)]) == {"R": ("f7_rev",)}
    members = None
    for m in range(1, 361):
        members = wl.step(m, [])
    assert members == {}                                                      # 360 min without a trade
    assert wl.step(361, [("R", 1, 2.0, 0.0, 300.0)]) == {"R": ("f7_rev",)}   # a trade brings it back


def test_mayhem_and_normal_pools_never_f7():
    wl = Watchlist(Rules())
    wl.add_pool(rev("X", mayhem=True))
    wl.add_pool(normal("N", create_minute=None))
    assert wl.step(0, [("X", 3, 20.0, 0.0, 999.0), ("N", 3, 20.0, 0.0, 999.0)]) == {}


def test_m1_pool_from_creation_to_six_hours_with_idle_gap():
    wl = Watchlist(Rules())
    wl.add_pool(normal("N", create_minute=100))
    wl.add_pool(normal("B", create_minute=100, grad_in_create=True))         # dev bundle: never
    assert wl.step(98, []) == {}                                              # not created yet
    assert wl.step(99, []) == {"N": ("m1",)}                                  # valid for minute 100 = creation
    for m in range(100, 160):
        wl.step(m, [])
    assert wl.step(160, []) == {}                                             # 61 min without a trade: stale
    assert wl.step(161, [("N", 1, 0.2, 0.0, 30.0)]) == {"N": ("m1",)}        # back on a trade
    for m in range(162, 220):
        wl.step(m, [("N", 1, 0.2, 0.0, 30.0)])
    members = None
    for m in range(220, 470):
        members = wl.step(m, [("N", 1, 0.2, 0.0, 30.0)])
    assert members == {}                                                      # age > 360 min


def test_snapshot_shape_and_counts():
    wl = Watchlist(Rules(), wallets=["w1", "w2", "w1"])
    wl.add_pool(rev("R"))
    wl.add_pool(normal("N", create_minute=5))
    wl.step(4, [("R", 3, 20.0, 0.0, 300.0)])
    snap = wl.snapshot(valid_from_slot=1000, generated_slot=999)
    assert snap["version"] == 1 and snap["valid_for_minute"] == 5
    assert [p["pool"] for p in snap["pools"]] == ["N", "R"]
    assert snap["pools"][1]["rules"] == ["f7_rev"] and snap["pools"][0]["rules"] == ["m1"]
    assert {k: snap["counts"][k] for k in ("pools", "f7_rev", "m1", "wallets")} == {"pools": 2, "f7_rev": 1, "m1": 1, "wallets": 2}
    assert [w["wallet"] for w in snap["wallets"]] == ["w1", "w2"]


def test_report_renders_whole_days_and_totals(tmp_path):
    import json

    from wallets import watchlist as W

    day = {"minutes": 1440, "events_all": 10_000_000, "events_on_list": 1_000_000, "events_f7_rev": 800_000, "events_m1": 200_000,
           "events_reversed_all": 6_000_000, "events_normal_all": 4_000_000, "f7_cand": 100, "f7_cand_covered": 90,
           "f7_trig": 50, "f7_trig_covered": 45, "f7_trig_organic": 20, "f7_trig_organic_covered": 20,
           "m1_points": 300, "m1_points_covered": 299, "m1_events_young": 5000, "m1_events_young_covered": 4990,
           "members": {"median": 40, "p95": 55, "max": 60}, "f7_rev": {"median": 30, "p95": 40, "max": 45}, "m1": {"median": 10, "p95": 15, "max": 20}}
    partial = dict(day, minutes=30)
    json.dump({"days": {"2026-09-11": day, "2026-09-12": partial}}, open(tmp_path / "watchlist-stats-1.json", "w"))
    md = W.report(str(tmp_path))
    assert "| 2026-09-11 | 40 / 55 / 60 | 30 / 45 | 10 / 20 | 1,000,000 | 10.0% | 90.0% (100) | 90.0% (50), 100.0% (20) | 99.7% (300) | 99.8% (5000) | 3.07 |" in md
    assert "Totaal over 1 dagen" in md and "3.07 GB/dag als volledige transacties" in md and "1.02 GB/dag als ruwe" in md
    assert "Gedeeltelijke dagen weggelaten: 2026-09-12" in md


def test_universe_with_refresh_lag():
    from wallets import watchlist as W

    day = W.MINUTES_PER_DAY
    m = 10 * day + 100                                   # an event on day 10, 01:40 UTC
    # lag 1: the universe knows data through the end of day 9
    assert W.in_universe(m, 1, 9 * day + 1439) and not W.in_universe(m, 1, 10 * day)
    # lag 2: through the end of day 8
    assert W.in_universe(m, 2, 8 * day + 1439) and not W.in_universe(m, 2, 9 * day)
    assert W.in_universe(m, 1, None, unknown_is_in=True) and not W.in_universe(m, 1, None)
    hist = {"old": {0: None, 50: 8 * day, 150: 9 * day + 5},        # created before the store, deep since day 9
            "new": {0: 10 * day + 1, 50: 10 * day + 2, 150: None}}   # created on day 10 itself
    u = W.universe_membership(hist, "old", m)
    assert u[(1, 0)] and u[(1, 50)] and u[(1, 150)] and u[(2, 0)] and u[(2, 50)] and not u[(2, 150)]
    assert not any(W.universe_membership(hist, "new", m).values())
    assert not any(W.universe_membership(hist, "unknown", m).values())


def test_report_universe_section_and_store_start_bias(tmp_path):
    import json

    from wallets import watchlist as W

    def counters(cand_in, trig_in):
        c = {}
        for lag in W.UNIVERSE_LAGS:
            c[f"d-{lag}"] = {}
            for depth in W.UNIVERSE_DEPTHS:
                c[f"d-{lag}"][str(depth)] = {"cand": 10, "cand_in": cand_in, "cand_in_on_list": cand_in - 1,
                                             "trig": 4, "trig_in": trig_in, "trig_in_on_list": trig_in,
                                             "trig_organic": 2, "trig_organic_in": 1, "trig_organic_in_on_list": 1,
                                             "f7_minutes": 1000, "f7_minutes_in": 900, "f7_minutes_in_on_list": 900}
        return c

    base = {"minutes": 1440, "events_all": 100, "events_on_list": 10, "events_f7_rev": 8, "events_m1": 2,
            "events_reversed_all": 60, "events_normal_all": 40, "f7_cand": 10, "f7_cand_covered": 9,
            "f7_trig": 4, "f7_trig_covered": 4, "f7_trig_organic": 2, "f7_trig_organic_covered": 2,
            "m1_points": 3, "m1_points_covered": 3, "m1_events_young": 50, "m1_events_young_covered": 50,
            "members": {"median": 4, "p95": 5, "max": 6}, "f7_rev": {"median": 3, "p95": 4, "max": 4}, "m1": {"median": 1, "p95": 1, "max": 2}}
    json.dump({"store_first_day": "2026-09-11",
               "days": {"2026-09-11": dict(base, universe=counters(5, 2)),      # store start: depth criteria biased
                        "2026-09-13": dict(base, universe=counters(8, 4),
                                           first_age_minutes={"cand": [3, 10, 400], "trig": [5, 7], "trig_organic": []},
                                           origin={"cand": {"at_create": 6, "later": 3, "prestore": 1}, "trig": {"at_create": 4}, "trig_organic": {}},
                                           deep_later_minutes={"cand": [2, 9, 30], "trig": [], "trig_organic": []})}},
              open(tmp_path / "watchlist-stats-1.json", "w"))
    md = W.report(str(tmp_path))
    assert "## Universum met ververs-vertraging" in md
    assert "| 2026-09-11 * | 50.0% (10) / 50.0% (4) (50.0% (2)) |" in md
    assert "| 2026-09-13 | 80.0% (10) / 100.0% (4) (50.0% (2)) |" in md
    # totals: 'bestond' over both days, the depth criteria over 2026-09-13 only
    assert "| D−1 bestond | 65.0% (20) | 55.0% (20) | 75.0% (8) | 75.0% (8) | 50.0% (4) | 50.0% (4) | 90.0% (2,000) |" in md
    assert "| D−2 ≥ 150 SOL | 80.0% (10) | 70.0% (10) | 100.0% (4) | 100.0% (4) | 50.0% (2) | 50.0% (2) | 90.0% (1,000) |" in md
    assert "Totaal over 1 dagen" in md
    assert "Op de dag van aanmaak zelf (niet in het D−1-universum 'bestond'): 25.0% van de toernooi-triggers, 50.0% van de organische, 35.0% van de kandidaatsells." in md
    assert "eerste toernooi-trigger (pools aangemaakt binnen de store, n=2): p10 5, p25 5, mediaan 6, p75 7, p90 7." in md
    assert "eerste organische trigger" not in md
    assert ("Hoe de pool diep werd, kandidaatsells (n=10): aangemaakt met ≥ 150 SOL 60.0%, later diep geworden 30.0%, pool van vóór de store 10.0%. "
            "Later-diepe pools: minuten van aanmaak tot ≥ 150 SOL (n=3) p10 2, mediaan 9, p90 30.") in md
    assert "Hoe de pool diep werd, toernooi-triggers (n=4): aangemaakt met ≥ 150 SOL 100.0%, later diep geworden 0.0%, pool van vóór de store 0.0%." in md


def test_pool_origin():
    from wallets.watchlist import pool_origin

    assert pool_origin(None) == "prestore" and pool_origin({0: None, 150: 5, "sol_create": 200.0}) == "prestore"
    assert pool_origin({0: 10, 150: 10, "sol_create": 168.0}) == "at_create"
    assert pool_origin({0: 10, 150: 40, "sol_create": 84.99}) == "later"
    assert pool_origin({0: 10, 150: None, "sol_create": 84.99}) == "unknown"
    assert pool_origin({0: 10, 150: 12, "sol_create": None}) == "later"


# ----------------------------------------------------------------------------- pruning (#165 gate point, 2026-10-13)

def _trade(pool, sol=10.0, farmer=0.0, depth=130.0):
    return (pool, 1, sol, farmer, depth)


def test_quiet_pools_are_pruned_and_come_back_with_their_verdict():
    wl = Watchlist(Rules(prune_idle_minutes=24 * 60, archive_minutes=7 * 24 * 60))
    wl.add_pool(rev("R"))
    wl.step(0, [_trade("R")])                      # organic (no farmer volume), deep: enters F7
    assert wl.members == {"R": ("f7_rev",)} and wl.state["R"].organic is True
    for m in range(1, 24 * 60):
        wl.step(m, [])
    assert "R" in wl.pools                          # 23 h 59 min quiet: still tracked (and off the list since 6 h)
    wl.step(24 * 60, [])
    assert "R" not in wl.pools and "R" not in wl.state and wl.archive["R"][1] is True and wl.pruned_total == 1
    snap = wl.snapshot()
    assert snap["counts"]["tracked"] == 0 and snap["counts"]["archived"] == 1 and snap["counts"]["pruned_total"] == 1
    # a trade brings it back with the organic verdict intact: F7 re-enters at once (the empty hour keeps the verdict)
    members = wl.step(24 * 60 + 5, [_trade("R", sol=0.5)])
    assert "R" in wl.pools and wl.state["R"].organic is True and members == {"R": ("f7_rev",)} and wl.restored_total == 1
    assert "R" not in wl.archive


def test_archived_verdict_expires_only_with_an_explicit_expiry():
    assert Rules().archive_minutes == 0  # never expires by default (review of #211)
    wl = Watchlist(Rules(prune_idle_minutes=60, archive_minutes=120))
    wl.add_pool(rev("R"))
    wl.step(0, [_trade("R", farmer=9.0)])            # farming hour: organic False
    assert wl.state["R"].organic is False
    wl.step(60, [])
    assert "R" in wl.archive and wl.archive["R"][1] is False
    wl.step(179, [])
    assert "R" in wl.archive
    wl.step(180, [])
    assert "R" not in wl.archive                      # expired after archive_minutes
    assert wl.step(181, [_trade("R")]) == {} and "R" not in wl.pools  # unknown again until add_pool
    wl.add_pool(rev("R"))
    assert wl.state["R"].organic is None              # a fresh state, no verdict


def test_never_traded_pools_are_pruned_from_their_creation_or_first_sight_and_future_pools_are_kept():
    wl = Watchlist(Rules(prune_idle_minutes=60))
    wl.add_pool(normal("N_old", create_minute=0))     # created at 0, never trades
    wl.add_pool(normal("N_future", create_minute=500))
    wl.add_pool(rev("R_unknown"))                      # create_minute None: counts from the minute it was added (step 10)
    wl.step(10, [])
    assert set(wl.pools) == {"N_old", "N_future", "R_unknown"}
    wl.step(60, [])
    assert "N_old" not in wl.pools and "N_future" in wl.pools and "R_unknown" in wl.pools
    wl.step(70, [])
    assert "R_unknown" not in wl.pools and "N_future" in wl.pools  # first seen at step 10 -> pruned at 70
    wl.step(559, [])
    assert "N_future" in wl.pools
    wl.step(560, [])
    assert "N_future" not in wl.pools


def test_members_are_never_pruned():
    wl = Watchlist(Rules(prune_idle_minutes=5, f7_idle_minutes=100))
    wl.add_pool(rev("R"))
    wl.step(0, [_trade("R")])
    for m in range(1, 50):
        assert wl.step(m, []) == {"R": ("f7_rev",)} and "R" in wl.pools


def test_archive_never_expires_by_default():
    wl = Watchlist(Rules(prune_idle_minutes=60, f7_idle_minutes=30))
    wl.add_pool(rev("R"))
    wl.step(0, [_trade("R")])
    wl.step(60, [])
    assert "R" in wl.archive
    wl.step(10**6, [])
    assert "R" in wl.archive and wl.step(10**6 + 1, [_trade("R")]) == {"R": ("f7_rev",)}


def test_pruned_machine_is_identical_to_the_unpruned_one():
    """The same row stream through Rules(prune_idle_minutes=10**9) and through the default: members per minute and
    the organic verdict per pool must agree, with pools returning after 25 h and after 8 days."""
    import random
    rng = random.Random(5)
    pools = [rev(f"R{i}") for i in range(6)] + [normal(f"N{i}", create_minute=i * 30) for i in range(4)]
    stream = {}
    minutes = 9 * 24 * 60
    for m in range(minutes):
        rows = []
        for i, info in enumerate(pools):
            p = info.pool
            active = (m // 180 + i) % 3 == 0          # 3 h on, 6 h off
            if p == "R5":
                active = m < 60 or 25 * 60 <= m < 25 * 60 + 60 or m >= 8 * 24 * 60   # returns after 25 h and after 8 days
            if p == "N3":
                active = 90 <= m < 120 or m >= 8 * 24 * 60
            if active and rng.random() < 0.7:
                farmer = rng.choice((0.0, 0.0, 8.0))
                rows.append((p, 1, 10.0, farmer, rng.choice((90.0, 130.0, 160.0))))
        stream[m] = rows
    a, b = Watchlist(Rules(prune_idle_minutes=10**9)), Watchlist(Rules())
    for info in pools:
        a.add_pool(info)
        b.add_pool(info)
    smallest = len(pools)
    for m in range(minutes):
        ma, mb = a.step(m, stream[m]), b.step(m, stream[m])
        assert ma == mb, (m, ma, mb)
        for p in a.pools:
            vb = b.state[p].organic if p in b.state else b.archive[p][1]
            assert a.state[p].organic == vb, (m, p)
        smallest = min(smallest, len(b.pools))
    assert b.pruned_total >= 3 and b.restored_total >= 2 and smallest < len(a.pools) == len(pools)
