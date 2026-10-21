"""The lead of a processed-commitment stream over a confirmed one, per transaction, from two lab-probe runs side by
side on the same filters and the same host (one `--commitment processed`, one `--commitment confirmed`, each with
`--signatures-out`). Offline, stdlib only.

  python -m checks.probe_lead --processed P.csv --confirmed C.csv [--margin-slots 32] [--out FILE]   # JSON

Both files are lab-probe's `signature,slot,bank_id,filters,received_unix_ns`. A signature counts at its first
receive in each file. Only the slots both runs covered count: from the later first slot to the earlier last slot,
less --margin-slots at each end (a transaction at the edge can miss the other run only because that run had not
started yet or had already stopped).

  lead          received on confirmed - received on processed, in ms, for the signatures in both: n, p50, p90, p99,
                mean, min, max, the share above 50 ms and the share below 0 (linear interpolation, as lab-probe)
  only_processed  signatures the processed run saw and the confirmed run did not: transactions of slots that died or
                  lost a fork; with their slot count and the most frequent slots
  only_confirmed  the other way round (the processed stream dropped or missed them)
  moved         signatures in both whose slot differs (processed saw them first in a slot that lost its fork)
  multi_bank    signatures the processed run received more than once (in more than one slot or bank)
"""

import argparse
import csv
import json
import sys
from collections import Counter

FIELDS = ["signature", "slot", "bank_id", "filters", "received_unix_ns"]


def read(path):
    """signature -> [(received_unix_ns, slot, bank_id)] in file order; the header must be lab-probe's."""
    out = {}
    with open(path, newline="") as f:
        r = csv.reader(f)
        if next(r, None) != FIELDS:
            raise ValueError(f"{path}: not a lab-probe --signatures-out file (header {FIELDS})")
        for row in r:
            if len(row) != len(FIELDS) or not row[0]:
                continue
            out.setdefault(row[0], []).append((int(row[4]), int(row[1]), int(row[2])))
    return out


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def first(rows):
    """(received_unix_ns, slot) of a signature's first receive."""
    t, slot, _ = min(rows)
    return t, slot


def lead(processed, confirmed, margin_slots=32):
    p = {s: first(r) for s, r in processed.items()}
    c = {s: first(r) for s, r in confirmed.items()}
    if not p or not c:
        return {"error": "an empty file", "processed_signatures": len(p), "confirmed_signatures": len(c)}
    lo = max(min(v[1] for v in p.values()), min(v[1] for v in c.values())) + margin_slots
    hi = min(max(v[1] for v in p.values()), max(v[1] for v in c.values())) - margin_slots
    inside = lambda v: lo <= v[1] <= hi  # noqa: E731
    p_in = {s: v for s, v in p.items() if inside(v)}
    c_in = {s: v for s, v in c.items() if inside(v)}
    both = sorted(set(p_in) & set(c_in))
    ms = sorted((c_in[s][0] - p_in[s][0]) / 1e6 for s in both)
    only_p = [s for s in p_in if s not in c]
    only_c = [s for s in c_in if s not in p]
    moved = sum(1 for s in both if p_in[s][1] != c_in[s][1])
    multi = sum(1 for s in p_in if len({(slot, bank) for _, slot, bank in processed[s]}) > 1)
    slots = lambda sigs, m: Counter(m[s][1] for s in sigs)  # noqa: E731
    sp, sc = slots(only_p, p_in), slots(only_c, c_in)
    return {
        "slot_range": [lo, hi], "margin_slots": margin_slots,
        "processed_signatures": len(p_in), "confirmed_signatures": len(c_in), "both": len(both),
        "lead_ms": {"n": len(ms), "p50": quantile(ms, 0.5), "p90": quantile(ms, 0.9), "p99": quantile(ms, 0.99),
                    "mean": sum(ms) / len(ms) if ms else None, "min": ms[0] if ms else None, "max": ms[-1] if ms else None,
                    "share_gt_50ms": sum(1 for x in ms if x > 50) / len(ms) if ms else None,
                    "share_lt_0": sum(1 for x in ms if x < 0) / len(ms) if ms else None},
        "only_processed": {"n": len(only_p), "slots": len(sp), "top_slots": sp.most_common(20)},
        "only_confirmed": {"n": len(only_c), "slots": len(sc), "top_slots": sc.most_common(20)},
        "moved": moved, "multi_bank": multi,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--processed", required=True)
    ap.add_argument("--confirmed", required=True)
    ap.add_argument("--margin-slots", type=int, default=32)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = lead(read(a.processed), read(a.confirmed), a.margin_slots)
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
