"""Watchlist rules on small fixtures: entry, hysteresis, farming hour, idle exit, M1 age, snapshot."""

from wallets.watchlist import PoolInfo, Rules, Watchlist


def rev(pool="R", mayhem=False):
    return PoolInfo(pool, "mint" + pool, "reversed", "reversed", mayhem, None, False)


def normal(pool="N", create_minute=100, grad_in_create=False):
    return PoolInfo(pool, "mint" + pool, "sol", "normal", False, create_minute, grad_in_create)


def test_f7_reversed_enters_at_150_and_leaves_below_120():
    wl = Watchlist(Rules())
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
    assert snap["counts"] == {"pools": 2, "f7_rev": 1, "m1": 1, "wallets": 2}
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
