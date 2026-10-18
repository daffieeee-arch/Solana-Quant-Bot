"""Live restarts with a high-water mark (HWM): up to it, the first run's journal leads.

Every minute's commit of a live run (`engine --live`) journals its HWM: state 'hwm' (the slot and minute of the
commit) and 'hwm_state' (the small risk state at that point per group: the daily loss, the pool cooldowns still
running, the paper wallet's realized P&L and last transactions, and the busy entries that can still block a signal).
Every row the run journaled before that point is committed with it, and none after it.

A start on a journal with a HWM replays the recorder segments of the last WINDOW_H (36) hours before it, not every
retained one (a replay runs at a few times real time). Up to the HWM:
- It writes no journal rows (heartbeat, usage, signals, orders, fills, exits, positions), no daily exports or reports
  and no wallet files. The first run's rows stay as they are.
- It takes the decisions from the journal instead of making them again:
  - a signal enters exactly when the journal has it entered, the N2 matches included;
  - an entry fills with the journal's size, unless the journal has it skipped or failed;
  - an exit the journal has closes the position with the journal's values.
  What the replay computes itself only counts as a difference.
- So the open positions at the HWM are the first run's, each rebuilt over the same events from its entry on: its
  marks, peak and flow window as they were.
At the HWM the engine checks the replay against the journal and takes the journal's state (Engine._resume):
- Open positions are the journal's `positions`.
  - Some position the replay cannot carry is closed at the HWM: exit_reason 'restart_lost', at its last journaled
    mark (mark_pnl), with the journal's tokens and cost, on the HWM's day.
  - That happens when the entry lies before the retained segments, when the entry does not fill again or fills
    differently (slot, tokens or cost: the replay's position rests on its own fill and the pool state after it), or
    when the replay exits before the HWM where the journal has the position still open.
- Pending entries are the journal's entered signals without an order.
- Busy, cooldowns, the daily loss and the wallets come from 'hwm_state'.
Then it writes again. The counts go to state 'restarts' and into the daily report (restart_lines).

The decisions of the last 6 hours before the HWM are compared with the journal's (what the replay would have
decided itself), but only when that window starts after this start's farmer history is complete (complete_from):
otherwise the farm labels differ by design and the comparison would raise false alarms.
"""

import json
from collections import Counter

HWM_KEY, HWM_STATE_KEY, RESTARTS_KEY = "hwm", "hwm_state", "restarts"
# The replay window of a restart, back from the HWM. Up to the HWM the journal decides, so the replay only rebuilds
# the state at it: the positions open then (an H position is held at most 6 h), with the 24 h farmer history behind
# their legs (the flow exits count organic legs only), and the live watchlist's rules (at most 24 h, Rules). 30 h
# would do; 36 h leaves 6 h, so this start's complete_from (its first slot + 24 h) lies 12 h before the HWM and the 6 h
# comparison runs. A position entered before the window would be closed at the HWM (restart_lost).
WINDOW_H = 36
# Counts that are no difference to the journal: signals and positions of the journal from before the replay's first
# slot (the keeper removed their segments), whose exits only count for the risk state; and signals of the replay's
# first hour, where its own trigger history (200 slots), N2 hour index and farm windows are still filling.
BEFORE = ("journal_entries_before_the_replay", "journal_exits_before_the_replay",
          "journal_signals_in_the_replays_first_hour", "replay_signals_in_its_first_hour_not_in_journal",
          "day_loss_from_before_the_replay")
# Journal rows from this many slots before the replay's first slot: entries due inside the replay of signals
# just before it.
MARGIN_SLOTS = 2_000
COMPARE_MS = 6 * 3_600_000


def key_of(row):
    return (row["family"], row["variant"], row["key"], row["t"])


class Muted:
    """The journal while a restart replays up to the HWM: reads pass, and so do the state and the learned pools and
    tokens (a cache, idempotent per key), and the recorder's usage of minutes after the HWM's (segments the first run
    read after its last commit: their rows would be lost). Every other row write is dropped, so the first run's rows
    up to the HWM stay as they were."""

    PASS = ("state", "learned")

    def __init__(self, journal, hwm_minute=None):
        self.journal, self.dir, self.db, self.hwm_minute = journal, journal.dir, journal.db, hwm_minute

    def put(self, table, **row):
        if table in self.PASS or (table == "usage" and self.hwm_minute is not None and (row.get("minute") or 0) > self.hwm_minute):
            self.journal.put(table, **row)

    def delete(self, table, **key):
        pass

    def state(self, k, v=None):
        return self.journal.state(k, v)

    def commit(self):
        self.journal.commit()

    def rows(self, *a, **k):
        return self.journal.rows(*a, **k)


def load(journal):
    """(hwm, hwm_state) of a journal, or None when it has no HWM (a first start)."""
    raw, st = journal.state(HWM_KEY), journal.state(HWM_STATE_KEY)
    if not raw or not st:
        return None
    return json.loads(raw), json.loads(st)


class Restart:
    """The journal's rows of the replayed window for a restarted live run, and what the replay found."""

    def __init__(self, journal, hwm, state):
        self.journal, self.hwm, self.state = journal, hwm, state
        self.slot = hwm["slot"]
        self.counts = Counter()
        self.sig, self.orders, self.buys, self.exits = {}, {}, {}, {}
        self.entered, self.next_entered = [], 0  # [(t, key)] of the journal's entered signals, in order
        self.exit_order, self.next_exit = [], 0  # [(slot, key)] of the journal's exits, in order
        self.applied, self.closed = set(), set()  # signal keys decided in the replay; exits applied in memory
        self.natural = {}  # signal key -> the replay's own decision ('entered' / 'skipped')
        self.first_slot = self.warm = self.first_day = None  # the replay's first slot; an hour after it; its UTC day

    def load(self, first_slot):
        """The journal's signals, orders, buy fills and exits from MARGIN_SLOTS before the replay's first slot on."""
        self.first_slot = first_slot
        lo, j = first_slot - MARGIN_SLOTS, self.journal
        self.sig = {key_of(r): r for r in j.rows("signals", "t >= ?", (lo,))}
        self.orders = {key_of(r): r for r in j.rows("orders", "t >= ?", (lo,))}
        self.buys = {key_of(r): r for r in j.rows("fills", "t >= ? AND side = 'buy'", (lo,))}
        self.exits = {key_of(r): r for r in j.rows("exits", "t >= ?", (lo,))}
        self.entered = sorted((r["t"], k) for k, r in self.sig.items() if r["status"] == "entered")
        self.exit_order = sorted((exit_slot(r), k) for k, r in self.exits.items())

    def due_signals(self, slot):
        """The journal's entered signals up to `slot` the replay has not decided itself (marked decided now)."""
        out = []
        while self.next_entered < len(self.entered) and self.entered[self.next_entered][0] <= slot:
            k = self.entered[self.next_entered][1]
            self.next_entered += 1
            if k not in self.applied:
                self.applied.add(k)
                out.append(self.sig[k])
        return out

    def due_exits(self, slot):
        """The journal's exits up to `slot` not yet applied (marked applied now)."""
        out = []
        while self.next_exit < len(self.exit_order) and self.exit_order[self.next_exit][0] <= slot:
            k = self.exit_order[self.next_exit][1]
            self.next_exit += 1
            if k not in self.closed:
                self.closed.add(k)
                out.append(self.exits[k])
        return out

    def compare(self, ms_slots, complete_from):
        """The replay's own decisions against the journal's over [HWM - 6 h, HWM): counts, or why not compared."""
        lo = self.slot - ms_slots
        if complete_from is not None and lo < complete_from:
            return {"compared": False, "why": f"HWM - 6 h ({lo}) before this start's complete_from ({complete_from})"}
        if self.first_slot is None or lo < self.first_slot:
            return {"compared": False, "why": f"HWM - 6 h ({lo}) before the replay's first slot ({self.first_slot})"}
        c = Counter()
        for k, nat in self.natural.items():
            if lo <= k[3] < self.slot:
                j = self.sig.get(k)
                c["same" if j is not None and j["status"] == nat else "different" if j is not None else "not_in_journal"] += 1
        c["journal_only"] = sum(1 for k in self.sig if lo <= k[3] < self.slot and k not in self.natural)
        return {"compared": True, "from_slot": lo, **c}


def window_start_seq(segments, hwm, hours=WINDOW_H):
    """The first recorder segment a restart replays: the first complete one closed (written_unix) at or after the HWM's
    minute - `hours`. Segments without written_unix (older recorders), or a HWM without a minute (a run that ended
    before it saw any block time): every segment."""
    if hwm.get("minute") is None:
        return segments[0][0] if segments else 0
    cutoff = hwm["minute"] * 60 - hours * 3600
    for seq, _, meta in segments:
        if meta.get("written_unix") is not None and meta["written_unix"] >= cutoff:
            return seq
    return segments[0][0] if segments else 0


def window_start_slot(hwm_slot, clock, hours=WINDOW_H):
    """The same window in slots (a replay feed's first slot, in the tests)."""
    return hwm_slot - clock.slots(hours * 3_600_000, hwm_slot)


def exit_slot(row):
    """When the journal's exit took effect: its exit slot; a failed entry's at its entry."""
    for c in ("exit_slot", "entry_slot", "t"):
        if row.get(c) is not None:
            return row[c]
    return 0


def restart_lines(journal, day):
    """The restarts that resumed on this day (state 'restarts'): the HWM and what the replay found."""
    raw = journal.state(RESTARTS_KEY)
    if not raw:
        return []
    today = [r for r in json.loads(raw) if r.get("day") == day]
    if not today:
        return []
    lines = ["", "## Restarts", ""]
    for r in today:
        c = r.get("counts", {})
        diff = ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if v and k not in BEFORE)
        before = ", ".join(f"{k} {c[k]}" for k in BEFORE if c.get(k))
        lines.append(f"- resumed at the HWM slot {r['hwm']}, the replay from slot {r.get('first_slot')}: "
                     f"{('differences to the journal: ' + diff) if diff else 'no difference to the journal'}"
                     + (f" (from before the replay or its first hour: {before})" if before else ""))
        if c.get("restart_lost"):
            lines.append(f"  - {c['restart_lost']} open positions the replay could not carry, closed at their last mark "
                         "(exit_reason restart_lost)")
        cmp_ = r.get("compare_6h") or {}
        if cmp_.get("compared"):
            lines.append(f"  - the last 6 h before the HWM, the replay's own decisions: {cmp_.get('same', 0)} as journaled, "
                         f"{cmp_.get('different', 0)} different, {cmp_.get('not_in_journal', 0)} not in the journal, "
                         f"{cmp_.get('journal_only', 0)} journaled but not seen")
        elif cmp_:
            lines.append(f"  - the last 6 h before the HWM not compared: {cmp_.get('why')}")
    return lines
