"""The stop rule, shared by the daily report (paper/report.py) and the web app's Paper tab (lab/ui/paper.py, whose
server has no numpy): the same functions, so the two can never count or decide differently.

- Which exits count: the traded families (not N2); with variant groups (journal state 'groups') the primary
  group's variants only (K3); with a clock start (journal state 'clock_start', paper/clock.py) the trades entered
  on or after its UTC day.
- What a trade is: a filled entry. A failed entry costs its transaction but is no trade.
- The verdict (AGENTS.md; owner 2026-10-10, amended 2026-10-16): net P&L per filled trade after all costs, with a
  95 % interval from a bootstrap over trading days (whole days, DRAWS times, seed SEED). It is computed and shown
  once there are MIN_TRADES filled trades and MIN_DAYS_CI trading days; an early verdict is no error. The clock
  runs at most CLOCK_MAX_DAYS days.
- What a verdict rests on and what stays uncertain (`assessment`), and that GO is no permission for live money.

Stdlib only and no package imports: the web app loads this file by its path.
"""

import json
import random
from collections import defaultdict
from datetime import date

MIN_TRADES = 200  # filled trades before a verdict (backtest.report.MIN_TRADES)
MIN_DAYS_CI = 5  # trading days before the interval
DRAWS, SEED = 10_000, 7  # the day bootstrap (AGENTS.md: 10,000 times)
FEW_DAYS = 14  # below this many trading days the day bootstrap is coarse
# The clock (AGENTS.md): its start no later than CLOCK_LATEST, fixed in the journal state; at most CLOCK_MAX_DAYS
# days. CLOCK_DAYS (4 weeks) is the planned length; since 2026-10-16 it no longer holds a verdict back.
CLOCK_KEY, CLOCK_LATEST, CLOCK_DAYS, CLOCK_MAX_DAYS = "clock_start", "2026-11-02", 28, 42
GROUPS_KEY, FEED_KEY, COSTS_KEY = "groups", "feed", "cost_scenario"
SETTLE_RULE_KEY = "settle_rule"  # the journal's settle rule (backtest.costs; absent: 1, the engine's until the deploy step)


def settle_rule_of(value):
    """The settle rule a journal's state value names (Codex review #6): 1 when the journal has none."""
    return int(value or 1)
ALPENGLOW_SLOT = 1064 * 432_000  # the consensus switch (epoch 1064, about 2026-10-21): a new regime
DECISIONS = ("GO", "ADJUST", "STOP")
# Cost items that are expectations from the cost scenario, not counted from the journal (LAB review P2, #309.3).
COSTS_EXPECTED = ("landing_retries",)
NOT_PERMISSION = "GO is geen toestemming voor live geld; dat is een aparte eigenaarsbeslissing."


def parse_groups(raw):
    """(primary group's variants or None (every variant), {further group: [variants]}) from the state 'groups'."""
    if not raw:
        return None, {}
    gs = json.loads(raw)
    primary = [v for g in gs.values() if g.get("primary") for v in g["variants"]]
    return set(primary), {name: g["variants"] for name, g in gs.items() if not g.get("primary")}


def primary_group(raw):
    """The primary group's record from the state 'groups' (d_ms, q, ...), or None."""
    return next((g for g in json.loads(raw).values() if g.get("primary")), None) if raw else None


def parse_clock(raw):
    """The clock start record ({date, reason, set_at[, previous]}) from the state 'clock_start', or None."""
    return json.loads(raw) if raw else None


def feed_of(raw, live_rows):
    """'live' or 'replay': the engine's state 'feed', or for a journal from before it, whether any signal carries a
    live arrival time (`live_rows`)."""
    if raw:
        return json.loads(raw).get("kind", "replay")
    return "live" if live_rows else "replay"


def counted(exits, orders, day, primary, clock):
    """The exits the stop rule counts up to `day`: traded families only, the primary group's variants (None: all),
    and with a clock the trades entered on or after its day (the order's day; an exit without one, its own)."""
    entered = {(o["family"], o["variant"], o["key"], o["t"]): o["day"] for o in orders}
    return [x for x in exits if not x["family"].startswith("N2_") and x["day"] <= day
            and (primary is None or x["variant"] in primary)
            and (clock is None or entered.get((x["family"], x["variant"], x["key"], x["t"]), x["day"]) >= clock["date"])]


def stop_rule_inputs(exits):
    """(P&L per day of every exit, filled trades per day, filled trades): a failed entry (no tokens) costs
    its transaction but is no trade."""
    by_day, filled_by_day = defaultdict(list), defaultdict(int)
    for x in exits:
        by_day[x["day"]].append(x["pnl"])
        filled_by_day[x["day"]] += bool(x["tokens"])
    return by_day, filled_by_day, sum(filled_by_day.values())


def percentile(xs, p):
    """numpy's default (linear) percentile of a non-empty list."""
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def boot_ci(sums, cnts, draws=DRAWS, seed=SEED):
    """Day-clustered bootstrap of the mean per trade from per-day sums and counts: (lo, hi) 95 %. Each draw resamples
    the days with replacement; a draw without a trade has no mean and is left out."""
    if sum(cnts) < 2 or len(cnts) < 2:
        return None, None
    rng, idx, means = random.Random(seed), range(len(cnts)), []
    for _ in range(draws):
        pick = rng.choices(idx, k=len(idx))
        c = sum(cnts[i] for i in pick)
        if c:
            means.append(sum(sums[i] for i in pick) / c)
    return (percentile(means, 2.5), percentile(means, 97.5)) if means else (None, None)


def decide(mean, lo, n, min_trades=MIN_TRADES):
    """GO when the interval's lower bound is above 0, ADJUST when only the mean is, else STOP; below min_trades
    filled trades 'too few trades', and without the interval (fewer than MIN_DAYS_CI trading days) 'too few days'."""
    if mean is None or n < min_trades:
        return "too few trades"
    if lo is None:
        return "too few days"
    if lo > 0:
        return "GO"
    return "ADJUST" if mean > 0 else "STOP"


def verdict(pnl_by_day, n, filled_by_day=None):
    """(mean, lo, hi, verdict) in lamports per filled trade. pnl_by_day holds every exit's P&L, failed entries' costs
    included; n and filled_by_day (per day) count the filled trades only (default: every exit)."""
    days = sorted(pnl_by_day)
    counts = filled_by_day if filled_by_day is not None else {d: len(v) for d, v in pnl_by_day.items()}
    mean = sum(sum(v) for v in pnl_by_day.values()) / n if n else None
    lo = hi = None
    if len(days) >= MIN_DAYS_CI:
        lo, hi = boot_ci([sum(pnl_by_day[d]) for d in days], [counts.get(d, 0) for d in days])
    return mean, lo, hi, decide(mean, lo, n)


def clock_state(clock, day, verdict_):
    """The clock on `day`: phase 'not_set' (a count of every paper day, no decision), 'not_started', 'running' (no
    verdict yet) or 'due' (the verdict is there, or the clock reached CLOCK_MAX_DAYS: `limit`); its day from 1."""
    if clock is None:
        return {"phase": "not_set", "clock_day": None, "limit": False}
    if day < clock["date"]:
        return {"phase": "not_started", "clock_day": None, "limit": False}
    n = (date.fromisoformat(day) - date.fromisoformat(clock["date"])).days + 1
    limit = n >= CLOCK_MAX_DAYS
    return {"phase": "due" if limit or verdict_ in DECISIONS else "running", "clock_day": n, "limit": limit}


def costs(traded, scn, rule=1):
    """The stop rule's costs beyond the fill prices, in lamports, as backtest.costs.settle books them under the cost
    scenario `scn` (the engine's, state 'cost_scenario') and the journal's settle rule (state 'settle_rule'; 1 for a
    journal without it): per filled trade an entry and an exit transaction (fee, tip and the *expected* failed attempts
    before one lands: int((1 / land_prob - 1) * failed_tx) per transaction, a rounded expectation, not counted
    attempts), the exit attempts that failed (rule 1: all but one; rule 2: all but a filling one, so an unsold or
    written-off position pays every attempt), the failed entries, ATA rent when it is lost, and adverse execution.
    Venue fees are in the fill prices (each pool's and curve's own fee rates from the data) and are not split out in
    the journal."""
    filled = [x for x in traded if x["tokens"]]
    n = len(filled)
    retries = int((1 / scn["land_prob"] - 1) * scn["failed_tx"])
    adverse = scn.get("adverse_per_side") or 0.0
    return {
        "network": 2 * n * scn["fee_per_tx"],
        "tips": 2 * n * scn["tip"],
        "landing_retries": 2 * n * retries,  # an expectation (COSTS_EXPECTED), not counted attempts
        "failed_exit_attempts": sum(max(0, (x["exit_attempts"] or 0) - (1 if rule == 1 else int(
            x.get("exit_slot") is not None and x.get("exit_reason") != "written_off"))) for x in filled) * scn["failed_tx"],
        "failed_entries": sum(1 for x in traded if not x["tokens"]) * scn["failed_tx"],
        "ata_rent_lost": n * scn["ata_rent"] if scn.get("rent_mode") == "lost" else 0,
        "adverse_execution": sum(int(x["cost"] * (1 + adverse)) - x["cost"] + x["proceeds"] - int(x["proceeds"] * (1 - adverse))
                                 for x in filled) if adverse else 0,
    }


def assessment(traded, mean, lo, hi, verdict_, clock_st, scn=None, group=None, feed="live", settle_rule=1):
    """What a verdict rests on and what stays uncertain (owner, 2026-10-16), for the daily report and the Paper tab.

    - basis: filled trades, failed entries, trading days, the clock day, mean and interval (lamports per filled
      trade), the bootstrap, and the costs (`costs`; None without the engine's cost scenario);
    - assumptions and uncertainty: keyed items with a text (English, the report's), so the Paper tab can word them;
    - applies: whether the stop rule decides on this journal (live paper trading, a clock start, a verdict);
    - not_permission: GO is no permission for live money."""
    by_day, _, n = stop_rule_inputs(traded)
    days = len(by_day)
    after = sum(1 for x in traded if (x.get("entry_slot") or 0) >= ALPENGLOW_SLOT)
    d_ms, q = (group or {}).get("d_ms"), (group or {}).get("q")
    land = scn.get("land_prob") if scn else None
    assumptions = [
        {"key": "paper", "text": "paper trading: every fill is simulated against the pool or curve reserves, our own "
                                 "price impact included; no transaction was sent"},
        {"key": "landing", "land_prob": land,
         "text": "landing is simulated" + (f": each transaction lands with probability {land:g}, failed attempts "
                                           "cost the failed-transaction fee" if land else "")},
        {"key": "delay", "d_ms": d_ms, "q": q,
         "text": f"the entry is {d_ms} ms after the trigger" + (f", at position {q:g} in its slot" if q is not None else "")
                 if d_ms is not None else "the entry delay is the engine's configuration"},
        {"key": "regime", "after_switch": after, "trades": len(traded),
         "text": f"{after} of {len(traded)} trades are after the Alpenglow switch (epoch 1064)"},
    ]
    uncertainty = []
    if days < FEW_DAYS:
        uncertainty.append({"key": "few_days", "days": days,
                            "text": f"only {days} trading days: below {FEW_DAYS} the day bootstrap is coarse"})
    uncertainty.append({"key": "regime", "after_switch": after,
                        "text": "a new regime: the backtests are all from before the Alpenglow switch" if after
                                else "every trade is before the Alpenglow switch; the regime after it is not measured"})
    uncertainty.append({"key": "simulated_landing",
                        "text": "landing, priority fees and competition for the same pools are simulated, not measured"})
    uncertainty.append({"key": "many_trials",
                        "text": "the candidate was chosen among many variants in the lab; this is its first unseen "
                                "test, and a lucky draw among many trials can look like an edge"})
    if feed != "live":
        uncertainty.insert(0, {"key": "not_paper_trading", "feed": feed,
                               "text": "this journal is a replay (a test), not live paper trading: no decision"})
    return {
        "verdict": verdict_,
        "applies": feed == "live" and clock_st.get("phase") == "due" and verdict_ in DECISIONS,
        "feed": feed,
        "basis": {"filled_trades": n, "failed_entries": len(traded) - n, "trading_days": days,
                  "clock_day": clock_st.get("clock_day"), "mean_lamports": mean,
                  "ci95_lamports": [lo, hi] if lo is not None else None, "draws": DRAWS, "seed": SEED,
                  "costs_lamports": costs(traded, scn, settle_rule) if scn else None, "costs_expected": list(COSTS_EXPECTED),
                  "settle_rule": settle_rule},
        "assumptions": assumptions,
        "uncertainty": uncertainty,
        "not_permission": NOT_PERMISSION,
    }
