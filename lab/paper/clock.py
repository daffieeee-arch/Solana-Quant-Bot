"""K3's stop-rule clock (AGENTS.md, owner 2026-10-10): its start date, fixed in the journal state with a reason.

The daily report's stop-rule counter and capital simulation count the primary group's trades entered on or after
this UTC day (paper/report.py). The start is set before it counts anything:
- not in the past;
- not after 2026-11-02, the owner's latest clock start;
- not on or before a day the journal already has a primary entry for.

The engine may be running: the write waits for its commit. Until its date comes, a start can be moved with
--replace, and the earlier values stay in the record. From its date on it stays.

  python -m paper.clock --journal <journal dir> --start 2026-10-26 --reason "<why this date>"
  python -m paper.clock --journal <journal dir>                                   # show it
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone

from .journal import Journal
from .report import CLOCK_KEY, CLOCK_LATEST, clock_of, groups_of


def primary_entries_from(journal, day):
    """The primary group's entries (orders, failed ones included) on or after `day`; N2 shadows are no trades."""
    primary, _ = groups_of(journal)
    return [o for o in journal.rows("orders", "day >= ?", (day,))
            if not o["family"].startswith("N2_") and (primary is None or o["variant"] in primary)]


def set_clock(journal, day, reason, now=None, replace=False):
    """Fix the clock start `day` (YYYY-MM-DD, UTC) with `reason` in the journal state; returns the record."""
    now = now or datetime.now(timezone.utc)
    try:
        ok = date.fromisoformat(day).isoformat() == day
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"start {day!r}: give a date as YYYY-MM-DD")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValueError("give a reason")
    today = now.date().isoformat()
    if day > CLOCK_LATEST:
        raise ValueError(f"start {day} is after {CLOCK_LATEST}, the owner's latest clock start")
    if day < today:
        raise ValueError(f"start {day} is in the past (today is {today}): the clock does not start retroactively")
    old = clock_of(journal)
    if old is not None:
        if not replace:
            raise ValueError(f"the clock start is set already ({old['date']}): --replace moves it, until that day comes")
        if old["date"] <= today:
            raise ValueError(f"the clock started on {old['date']}: it stays")
    n = len(primary_entries_from(journal, day))
    if n:
        raise ValueError(f"the journal has {n} primary entries on or after {day} already: pick a later day")
    rec = {"date": day, "reason": reason, "set_at": now.isoformat(timespec="seconds")}
    if old is not None:
        rec["previous"] = [*old.pop("previous", []), old]
    journal.state(CLOCK_KEY, json.dumps(rec, sort_keys=True))
    journal.commit()
    return rec


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", required=True, help="the paper journal directory (with journal.sqlite)")
    ap.add_argument("--start", help="the clock start, a UTC day YYYY-MM-DD; without it: show the clock")
    ap.add_argument("--reason", help="why this day (required with --start)")
    ap.add_argument("--replace", action="store_true", help="move a start whose day has not come yet")
    a = ap.parse_args(argv)
    if not os.path.exists(os.path.join(a.journal, "journal.sqlite")):
        raise SystemExit(f"no journal.sqlite in {a.journal}")
    j = Journal(a.journal)
    j.db.execute("PRAGMA busy_timeout = 120000")  # a running engine holds the write lock until its minute's commit
    try:
        if a.start is None:
            rec = clock_of(j)
            print(json.dumps(rec, indent=1, sort_keys=True) if rec else "no clock start set")
            return
        try:
            rec = set_clock(j, a.start, a.reason, now=now, replace=a.replace)
        except ValueError as e:
            raise SystemExit(f"refused: {e}")
        print(json.dumps(rec, indent=1, sort_keys=True))
    finally:
        j.close()


if __name__ == "__main__":
    sys.exit(main())
