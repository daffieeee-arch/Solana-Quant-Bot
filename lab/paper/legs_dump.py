"""The engine's event stream as lines: the reference for the Rust legs reader (lab/live `lab-live legs`, M1 C1a of
lab/docs/rust-live-route-design-2026-10-16.md; the porting spec is research/m1-c1a-legs-spec-2026-10-19.md, §7 P1).

  python -m paper.legs_dump --store <store.duckdb> --from SLOT --to SLOT --summary FILE [--out FILE [--out-from SLOT]]
                            [--window N]

Every Event of ReplayFeed(store, from, to), in its order, as one tab-separated line:
- common: venue key slot tx outer inner block_time tau_u (tau_u = hash(order) % 10_000, the PositionSim's draw);
- curve: variant is_buy t q fee pre(vt vq rt rq) post(vt vq rt rq) fee_bps creator_bps trader budget max_cost
  min_tokens min_out direct;
- pool: kind exact_out base quote_net pre(b e lp protocol creator cashback virt) post(the same) trader limit_quote
  limit_base boost_left direct lp_amount lp_supply farmer.
None is `null`, a bool `true`/`false`, an int decimal. The summary (JSON) has the count and sha256 of all lines, the
count per venue and pool kind, and per slot window [from + k*window, ...) its count and sha256, so a difference can be
found by window; --out writes the lines themselves (small ranges; with --out-from only those of the slots from there
on: a window that differs, in a run from the store's start, whose pool context and farmer history it needs).
"""

import argparse
import hashlib
import json

from .feed import ReplayFeed


def render(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _pool_state(p):
    return (p.b, p.e, p.lp_bps, p.protocol_bps, p.creator_bps, p.cashback_bps, p.virt)


def line(ev):
    """One Event as its dump line (the Rust reader writes the same)."""
    l = ev.leg
    if (l.slot, l.tx, l.order) != (ev.slot, ev.tx_index, ev.order):
        raise AssertionError(f"leg and event disagree at {ev.order}")
    out = [ev.venue, ev.key, ev.slot, ev.tx_index, ev.outer_ix, ev.inner_ix, ev.block_time, hash(ev.order) % 10_000]
    if ev.venue == "curve":
        out += [l.variant, l.is_buy, l.t, l.q, l.fee, l.pre.vt, l.pre.vq, l.pre.rt, l.pre.rq, l.post.vt, l.post.vq, l.post.rt,
                l.post.rq, l.post.fee_bps, l.post.creator_bps, l.trader, l.budget, l.max_cost, l.min_tokens, l.min_out, l.direct]
    else:
        out += [l.kind, l.exact_out, l.base, l.quote_net, *_pool_state(l.pre), *_pool_state(l.post), l.trader, l.limit_quote,
                l.limit_base, l.boost_left, l.direct, l.lp_amount, l.lp_supply, l.farmer]
    return "\t".join(render(v) for v in out)


def dump(store, lo, hi, window=10_000, out=None, out_from=0):
    """The summary of ReplayFeed's events over [lo, hi); with `out`, the lines of the slots from `out_from` on too."""
    feed = ReplayFeed(store, lo, hi)
    total, kinds, windows = hashlib.sha256(), {}, []
    n = 0
    cur = None  # [a, b, count, sha]
    f = open(out, "w") if out else None
    try:
        for ev in feed.events():
            s = line(ev)
            a = lo + (ev.slot - lo) // window * window
            if cur is None or cur[0] != a:
                if cur is not None:
                    windows.append([cur[0], cur[1], cur[2], cur[3].hexdigest()])
                cur = [a, min(a + window, hi), 0, hashlib.sha256()]
            b = (s + "\n").encode()
            total.update(b)
            cur[2] += 1
            cur[3].update(b)
            n += 1
            k = "curve" if ev.venue == "curve" else f"pool/{ev.leg.kind}"
            kinds[k] = kinds.get(k, 0) + 1
            if f and ev.slot >= out_from:
                f.write(s + "\n")
    finally:
        if f:
            f.close()
        feed.con.close()
    if cur is not None:
        windows.append([cur[0], cur[1], cur[2], cur[3].hexdigest()])
    return {"from": lo, "to": hi, "window": window, "events": n, "sha256": total.hexdigest(), "kinds": dict(sorted(kinds.items())),
            "windows": windows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--from", dest="lo", type=int, required=True)
    ap.add_argument("--to", dest="hi", type=int, required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--out")
    ap.add_argument("--out-from", dest="out_from", type=int, default=0)
    ap.add_argument("--window", type=int, default=10_000)
    a = ap.parse_args(argv)
    res = dump(a.store, a.lo, a.hi, a.window, a.out, a.out_from)
    with open(a.summary, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "windows"}))


if __name__ == "__main__":
    main()
