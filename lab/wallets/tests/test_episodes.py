"""Leader episodes: opening, top-ups, capped sells, exit share, time cap and the trigger window."""

from wallets.episodes import EXIT_SHARE, walk

SOL = 10**9


def leg(slot, tokens, sol, buy, trigger=None, create_slot=False):
    trigger = (buy and sol >= SOL) if trigger is None else trigger
    return ("w", "k", "curve", "curve", slot, slot << 20, tokens, sol, buy, trigger, create_slot, None)


def test_episode_opens_on_trigger_and_closes_at_exit_share():
    rows = [leg(10, 100, SOL // 2, True),  # small buy: no episode
            leg(20, 1000, 2 * SOL, True),  # trigger
            leg(30, 500, 1 * SOL, True),  # top-up (not a new episode, also >= 1 SOL)
            leg(40, 1400, 4 * SOL, False),  # sells 1400 of 1500: 100 left <= 10% of max -> closed
            leg(50, 100, SOL // 10, False),  # after the close: ignored (no open episode, not a trigger)
            leg(60, 2000, 3 * SOL, True)]  # new episode, still open at the end
    eps = walk(rows, 0, 100, episode_slots=1000)
    assert len(eps) == 2
    a, b = eps
    assert (a["t0"], a["cost_sol"], a["proceeds_sol"], a["remaining_tokens"], a["max_tokens"]) == (20, 3 * SOL, 4 * SOL, 100, 1500)
    assert a["closed"] and a["hold_slots"] == 20 and a["end_slot"] == 40 and a["n_buys"] == 2 and a["n_sells"] == 1
    assert b["t0"] == 60 and not b["closed"] and b["hold_slots"] is None and b["end_slot"] == 1060


def test_sell_capped_at_held_tokens_pro_rata():
    rows = [leg(20, 1000, 2 * SOL, True),
            leg(30, 4000, 8 * SOL, False)]  # sells 4000 but we count only 1000: 2 SOL of the 8
    (a,) = walk(rows, 0, 100, 1000)
    assert a["proceeds_sol"] == 2 * SOL and a["remaining_tokens"] == 0 and a["closed"]
    assert abs(EXIT_SHARE - 0.10) < 1e-9


def test_time_cap_and_trigger_window():
    rows = [leg(5, 1000, 2 * SOL, True),  # before the window: opens an episode that is not emitted...
            leg(50, 500, SOL, True),  # ...so this buy inside the window is a top-up, not a trigger
            leg(2000, 1000, 5 * SOL, True),  # after the cap: the first episode is emitted (censored) and
            leg(2100, 200, SOL // 2, False)]  # this >= 1 SOL buy opens a second one (slot 2000 >= hi: stop)
    eps = walk(rows, 10, 1000, episode_slots=1000)
    assert [e["t0"] for e in eps] == []  # the pre-window episode is dropped, slot 2000 is past hi
    rows = [leg(20, 1000, 2 * SOL, True), leg(1500, 100, SOL // 10, False)]
    (a,) = walk(rows, 10, 1000, 1000)
    assert not a["closed"] and a["end_slot"] == 1020 and a["proceeds_sol"] == 0 and a["n_sells"] == 0
