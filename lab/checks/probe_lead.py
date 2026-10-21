"""How far ahead an earlier Dragon's Mouth stream delivers the same transactions, from lab-probe runs side by side on
the same filters and the same host, each with `--signatures-out`: deshred (`--deshred`, rebuilt from shreds before
execution), processed (`--commitment processed`) and confirmed (`--commitment confirmed`). Any two of them, or all
three. Offline, stdlib only.

  python -m checks.probe_lead [--deshred D.csv] [--processed P.csv] [--confirmed C.csv] [--margin-slots 32] [--out FILE]

The files are lab-probe's `signature,slot,bank_id,filters,received_unix_ns` (bank_id empty on deshred). A signature
counts at its first receive in each file. Only the slots every run covered count: from the latest first slot to the
earliest last slot, less --margin-slots at each end (a transaction at the edge can miss another run only because that
run had not started yet or had already stopped).

Per pair, earlier stream first (deshred -> processed -> confirmed):
  lead          received on the later stream - received on the earlier one, in ms, for the signatures in both: n,
                p50, p90, p99, mean, min, max, the share above 50 ms and the share below 0 (linear interpolation, as
                lab-probe)
  only_<name>   signatures in the range that the other stream of the pair never saw (anywhere), with the slots
  moved         signatures in both whose slot differs (seen first in a slot that lost its fork)
Per stream: the signatures in the range, those only it saw among all the given streams (only_in_one), and those it
received more than once (multi_bank: more than one slot or bank). Expected: only-deshred holds transactions that fail
or never execute (deshred sees them before execution); only-processed holds transactions of slots that died or lost
their fork; only-confirmed ones are transactions a faster stream dropped.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from itertools import combinations

FIELDS = ["signature", "slot", "bank_id", "filters", "received_unix_ns"]
ORDER = ("deshred", "processed", "confirmed")  # earliest first
NOTE = ("only-deshred includes transactions that fail or never execute (deshred sees them before execution); "
        "only-processed includes transactions of slots that died or lost their fork")


def read(path):
    """signature -> [(received_unix_ns, slot, bank_id or None)] in file order; the header must be lab-probe's."""
    out = {}
    with open(path, newline="") as f:
        r = csv.reader(f)
        if next(r, None) != FIELDS:
            raise ValueError(f"{path}: not a lab-probe --signatures-out file (header {FIELDS})")
        for row in r:
            if len(row) != len(FIELDS) or not row[0]:
                continue
            out.setdefault(row[0], []).append((int(row[4]), int(row[1]), int(row[2]) if row[2] else None))
    return out


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def first(rows):
    """(received_unix_ns, slot) of a signature's first receive."""
    t, slot, _ = min(rows, key=lambda r: (r[0], r[1]))
    return t, slot


def lead_stats(ms):
    ms = sorted(ms)
    n = len(ms)
    return {"n": n, "p50": quantile(ms, 0.5), "p90": quantile(ms, 0.9), "p99": quantile(ms, 0.99),
            "mean": sum(ms) / n if n else None, "min": ms[0] if n else None, "max": ms[-1] if n else None,
            "share_gt_50ms": sum(1 for x in ms if x > 50) / n if n else None,
            "share_lt_0": sum(1 for x in ms if x < 0) / n if n else None}


def _slots(sigs, firsts):
    c = Counter(firsts[s][1] for s in sigs)
    return {"n": len(sigs), "slots": len(c), "top_slots": c.most_common(20)}


def compare(streams, margin_slots=32):
    """streams: {name: read(file)} for two or three of ORDER."""
    names = [n for n in ORDER if n in streams]
    if len(names) < 2:
        raise ValueError("give at least two of --deshred, --processed, --confirmed")
    firsts = {n: {s: first(r) for s, r in streams[n].items()} for n in names}
    if any(not firsts[n] for n in names):
        return {"error": "an empty file", "signatures": {n: len(firsts[n]) for n in names}}
    lo = max(min(v[1] for v in firsts[n].values()) for n in names) + margin_slots
    hi = min(max(v[1] for v in firsts[n].values()) for n in names) - margin_slots
    inside = {n: {s: v for s, v in firsts[n].items() if lo <= v[1] <= hi} for n in names}
    pairs = {}
    for a, b in combinations(names, 2):  # ORDER keeps the earlier stream first
        both = sorted(set(inside[a]) & set(inside[b]))
        pairs[f"{a}_vs_{b}"] = {
            "both": len(both),
            "lead_ms": lead_stats([(inside[b][s][0] - inside[a][s][0]) / 1e6 for s in both]),
            f"only_{a}": _slots([s for s in inside[a] if s not in firsts[b]], inside[a]),
            f"only_{b}": _slots([s for s in inside[b] if s not in firsts[a]], inside[b]),
            "moved": sum(1 for s in both if inside[a][s][1] != inside[b][s][1]),
        }
    only_in_one = {n: sum(1 for s in inside[n] if not any(s in firsts[m] for m in names if m != n)) for n in names}
    multi = {n: sum(1 for s in inside[n] if len({(slot, bank) for _, slot, bank in streams[n][s]}) > 1) for n in names}
    return {"streams": names, "slot_range": [lo, hi], "margin_slots": margin_slots,
            "signatures": {n: len(inside[n]) for n in names}, "pairs": pairs, "only_in_one": only_in_one,
            "multi_bank": multi, "note": NOTE}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for n in ORDER:
        ap.add_argument(f"--{n}", help=f"the {n} run's --signatures-out file")
    ap.add_argument("--margin-slots", type=int, default=32)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        res = compare({n: read(getattr(a, n)) for n in ORDER if getattr(a, n)}, a.margin_slots)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
