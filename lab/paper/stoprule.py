"""The stop rule's selection and count, shared by the daily report (paper/report.py) and the web app's Paper tab
(lab/ui/paper.py, whose server has no numpy), so the two can never count differently.

- Which exits count: the traded families (not N2); with variant groups (journal state 'groups') the primary
  group's variants only (K3); with a clock start (journal state 'clock_start', paper/clock.py) the trades entered
  on or after its UTC day.
- What a trade is: a filled entry. A failed entry costs its transaction but is no trade.
- The clock: its day, and when the decision falls due (AGENTS.md, owner 2026-10-10).
- The verdict from the mean and the interval's lower bound.

Stdlib only and no package imports: the web app loads this file by its path.
"""

import json
from collections import defaultdict
from datetime import date

# The stop rule's clock (AGENTS.md, owner 2026-10-10): the decision after 4 weeks with at least the minimum of
# trades, else at the minimum, at most 6 weeks; its start no later than CLOCK_LATEST, fixed in the journal state.
CLOCK_KEY, CLOCK_LATEST, CLOCK_DAYS, CLOCK_MAX_DAYS = "clock_start", "2026-11-02", 28, 42
GROUPS_KEY = "groups"


def parse_groups(raw):
    """(primary group's variants or None (every variant), {further group: [variants]}) from the state 'groups'."""
    if not raw:
        return None, {}
    gs = json.loads(raw)
    primary = [v for g in gs.values() if g.get("primary") for v in g["variants"]]
    return set(primary), {name: g["variants"] for name, g in gs.items() if not g.get("primary")}


def parse_clock(raw):
    """The clock start record ({date, reason, set_at[, previous]}) from the state 'clock_start', or None."""
    return json.loads(raw) if raw else None


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


def decide(mean, lo, n, min_trades):
    """GO when the interval's lower bound is above 0, ADJUST when only the mean is, else STOP; below
    min_trades filled trades: too few trades."""
    if mean is None or n < min_trades:
        return "too few trades"
    if lo is not None and lo > 0:
        return "GO"
    return "ADJUST" if mean > 0 else "STOP"


def clock_state(clock, day, n_filled, min_trades):
    """The clock on `day`: phase 'not_set' (a count of every paper day, no decision), 'not_started', 'running',
    'extended' (past CLOCK_DAYS below min_trades) or 'due', with its day number from 1."""
    if clock is None:
        return {"phase": "not_set", "clock_day": None}
    if day < clock["date"]:
        return {"phase": "not_started", "clock_day": None}
    n = (date.fromisoformat(day) - date.fromisoformat(clock["date"])).days + 1
    if n >= CLOCK_MAX_DAYS or (n >= CLOCK_DAYS and n_filled >= min_trades):
        phase = "due"
    elif n >= CLOCK_DAYS:
        phase = "extended"
    else:
        phase = "running"
    return {"phase": phase, "clock_day": n}
