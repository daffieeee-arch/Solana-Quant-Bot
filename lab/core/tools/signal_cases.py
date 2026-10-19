"""Expected F7 signal rows for lab/core's C1b unit tests, from the real paper engine (paper/engine.py, unmodified) on
synthetic feeds; prints Rust source (the data module lab/core/src/signals/cases.rs) to stdout.

Each case: a clock, dev bounds, tokens, SOL-market pools and events (legs as fields). The engine runs with
variants 0.04:600, d_ms 600, ignore_risk, the primary K3 (segments None, farm all, n2_k as given) and group H
(pool_reversed, organic, n2_k 0, require_farm_complete), as the C1b reference config (spec D3). Rows are read back
from the journal (the last write wins); gate = reason when it is a farm gate, else 'pass'.

Run from lab/: PYTHONPATH=. $PY core/tools/signal_cases.py > /tmp/cases.rs (the lab Python with numpy and DuckDB).
cases.rs holds this output's s, t2 and x cases (as private consts) after its hand-written test harness; the T1 cases
printed here are tested in signals.rs from the 13 real legs (f7.rs T1_LEGS) instead."""
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import textwrap
from dataclasses import replace

from backtest.replay import CurveLeg, PoolLeg
from backtest.strategies import Clock, _seed, decision_bounds
from backtest.venues import Curve, Pool
from paper.engine import Engine, Group
from paper.feed import Event, Feed, PoolInfo, TokenInfo
from paper.journal import Journal

SOL = 10**9
ONES = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
PUMPQ = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
MAYHEM = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
DEV2 = {1033: 316.36, 1034: 315.9, 1035: 316.53, 1036: 317.13, 1037: 266.46, 1038: 266.83, 1039: 267.12, 1040: 266.17,
        1041: 265.83, 1042: 267.83, 1043: 267.93, 1044: 268.26, 1045: 267.65, 1046: 267.35}
FWD = {1053: 218.29}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
VT, RT, TOK = 1_000_000_000_000_000, 700_000_000_000_000, 1_000_000_000_000


def b58(label):
    n = int.from_bytes(hashlib.sha256(label.encode()).digest(), "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return s


class Case:
    def __init__(self, name, clock, dev, n2_k=1, bt0=1_790_000_000):
        self.name, self.clock, self.dev, self.n2_k, self.bt0 = name, clock, dev, n2_k, bt0
        self.names, self.ids, self.tokens, self.pools, self.events = [], {}, {}, {}, []
        self.id(MAYHEM)
        self.doc, self.labels = "", {0: "MAYHEM_AGENT"}

    def id(self, s):
        if s is None:
            return None
        if s not in self.ids:
            self.ids[s] = len(self.names)
            self.names.append(s)
        return self.ids[s]

    def key(self, label):
        s = b58(label)
        self.labels[self.id(s)] = label
        return s

    def token(self, mint, create, creator, mayhem=False, quote=ONES):
        self.id(mint), self.id(creator)
        self.tokens[mint] = TokenInfo(mint, create, creator, mayhem, quote, None, None)

    def pool(self, pool, reversed_, mint=None, coin_creator=None, mayhem=False):
        self.id(pool), self.id(mint), self.id(coin_creator)
        # feed.py: COALESCE(mint, pool), so a pool without a mint looks itself up among the tokens.
        self.pools[pool] = PoolInfo(pool, mint or pool, "reversed" if reversed_ else "normal", "sol", mayhem, coin_creator, None)

    def bt(self, slot):
        return self.bt0 + (slot - 450_000_000) * 2 // 5

    def curve(self, slot, tx, mint, trader, is_buy, q, vq, rq, bt="auto"):
        self.id(mint), self.id(trader)
        post = Curve(VT, vq, RT, rq, 95, 30)
        s = 1 if is_buy else -1
        pre = Curve(VT + s * TOK, vq - s * q, RT + s * TOK, rq - s * q, 95, 30)
        leg = CurveLeg(slot, tx, (slot, tx, 0, 0), is_buy, TOK, q, 0, "buy" if is_buy else "sell", pre, post, trader,
                       max_cost=10**15 if is_buy else None, min_out=0 if not is_buy else None, direct=True)
        self.events.append((slot, tx, self.bt(slot) if bt == "auto" else bt, "curve", mint, leg, (is_buy, q, vq, rq)))

    def trade(self, slot, tx, pool, trader, kind, base, quote_net, b, e, farmer=False, bt="auto"):
        self.id(pool), self.id(trader)
        pre = Pool(b, e, 20, 5, 5, 0, virt=0)
        post = replace(pre, b=b + base if kind in ("sell", "deposit") else b - base)
        leg = PoolLeg(slot, tx, (slot, tx, 0, 0), kind, False, base, quote_net, pre, post, trader, farmer=farmer)
        self.events.append((slot, tx, self.bt(slot) if bt == "auto" else bt, "pool", pool, leg, (kind, base, quote_net, b, e, farmer)))

    def run(self):
        events = sorted(self.events, key=lambda x: (x[0], x[1]))
        case = self

        class Stub(Feed):
            meta_bounds = self.dev
            all_pools = True  # as ReplayFeed (feed.py:88): the replay rule of farm_window_complete

            def token(self, m):
                return case.tokens.get(m)

            def pool(self, p):
                return case.pools.get(p)

            def curve_history(self, m, upto):
                return [e[5] for e in events if e[3] == "curve" and e[4] == m and e[0] <= upto]

            def events(self):
                for slot, tx, bt, venue, key, leg, _ in events:
                    yield Event(slot, tx, 0, 0, bt, venue, key, leg)

        d = tempfile.mkdtemp(dir="/tmp")
        try:
            H = Group("H", ["flow300_H6", "flow300_tp100_H6"], farm="organic", segments=["pool_reversed"], d_ms=600, q=0.5,
                      n2_k=0, risk_path=None, wallet_file="wallet-h.json", require_farm_complete=True)
            clock = Clock({str(k): v for k, v in self.clock.items()})
            eng = Engine(Stub(), Journal(d), None, variants=["0.04:600"], d_ms=600, q=0.5, n2_k=self.n2_k, ignore_risk=True,
                         dev_bounds=self.dev, clock=clock, backup=False, groups=[H])
            eng.run()
            db = sqlite3.connect(f"{d}/journal.sqlite")
            cols = "family, variant, key, t, day, venue, orientation, depth, trigger_trader, farm, pool_seen_slot, " \
                   "farm_window_complete, status, reason, block_time, d_ms, farm_complete"
            rows = db.execute(f"SELECT {cols} FROM signals ORDER BY t, family, key, variant").fetchall()
            assert all(r[14] is None and r[15] is None and r[16] is None for r in rows)
            self.rows, self.hour_events = rows, {k: list(v) for k, v in eng.hour_events.items()}
            assert all(x[1][0] == x[0] and x[1][2:] == (0, 0) for xs in eng.farm_win.values() for x in xs)
            self.farm = {p: [(x[0], x[1][1], x[2], x[3]) for x in xs] for p, xs in eng.farm_win.items()}
            self.bounds = (eng.dlo, eng.dhi)
            self.statuses = sorted({(r[12], r[13]) for r in rows}, key=str)
        finally:
            shutil.rmtree(d)
        return self

    def rust(self):
        doc = " ".join(self.doc.split()).replace(" - ", "\xa0-\xa0").replace(" + ", "\xa0+\xa0")  # no break inside t - n
        o = [x.replace("\xa0", " ") for x in textwrap.wrap(f"{self.name}: {doc}", 120, initial_indent="/// ", subsequent_indent="/// ")]
        o.append(f"pub(super) const {self.name.upper()}: Case = Case {{")
        line = "    // ids: "
        for i, l in sorted(self.labels.items()):
            x = f"{i} {l}, "
            if len(line) + len(x) > 122:
                o.append(line.rstrip())
                line = "    //   "
            line += x
        o.append(line.rstrip(", "))
        o += wrap("    names: &[", [f'"{n}"' for n in self.names], "],")
        o += wrap("    seeds: &[", [str(_seed(n)) for n in self.names], "],")
        trig = sorted({(self.ids[e[4]], e[0]) for e in self.events})
        o += wrap("    trigger_seeds: &[", [f"({k}, {t}, {_seed('paper-N2', self.names[k], t)})" for k, t in trig], "],")
        o += wrap("    clock: &[", [f"({e}, {v})" for e, v in sorted(self.clock.items())], "],")
        o.append(f"    dev: {self.dev},")
        o.append(f"    n2_k: {self.n2_k},")
        o.append(f"    bounds: {self.bounds},")
        o += wrap("    mints: &[", [f"({self.ids[m]}, {opt(tk.create_slot)}, {idn(self, tk.creator)}, {opt(tk.mayhem, b=True)}, "
                                    f"{opt(tk.quote_mint, s=True)})" for m, tk in self.tokens.items()], "],")
        o += wrap("    pools: &[", [f"({self.ids[p]}, {str(pi.orientation == 'reversed').lower()}, {str(bool(pi.mayhem)).lower()}, "
                                    f"{opt(self.ids.get(pi.mint))}, {idn(self, pi.coin_creator)})" for p, pi in self.pools.items()], "],")
        o.append("    events: &[")
        for slot, tx, bt, venue, key, leg, f in sorted(self.events, key=lambda x: (x[0], x[1])):
            if venue == "curve":
                lf = f"C({str(f[0]).lower()}, {f[1]}, {f[2]}, {f[3]})"
            else:
                lf = f"P(PoolKind::{f[0].capitalize()}, {f[1]}, {f[2]}, {f[3]}, {f[4]}, {str(f[5]).lower()})"
            o.append(f"        ({slot}, {tx}, {opt(bt)}, {self.ids[key]}, {idn(self, leg.trader)}, {lf}),")
        o.append("    ],")
        o.append("    expect: &[")
        for r in self.rows:
            fam, var, key, t, day, venue, orient, depth, trader, farm, seen, fwc, status, reason = r[:14]
            gate = reason if status == "skipped" and reason in ("farm flagged", "farm history incomplete", "farm window incomplete") else "pass"
            o.append(f'        ("{fam}", "{var}", {self.ids[key]}, {t}, {opt(day, s=True)}, "{orient}", {depth}, '
                     f'{opt(self.ids.get(trader))}, "{farm}", {opt(seen)}, {opt(fwc)}, "{gate}"),')
        o.append("    ],")
        o.append("    hour_events: &[")
        for (venue, orient), xs in sorted(self.hour_events.items()):
            seg = "Curve" if venue == "curve" else ("PoolReversed" if orient == "reversed" else "PoolNormal")
            o += wrap(f"        (Segment::{seg}, &[", [f"({self.ids[k]}, {s}, {d})" for k, s, d in xs], "]),", indent="            ")
        o.append("    ],")
        o.append("    farm: &[" if self.farm else "    farm: &[],")
        for p, xs in sorted(self.farm.items(), key=lambda x: self.ids[x[0]]):
            o += wrap(f"        ({self.ids[p]}, &[", [f"({s}, {tx}, {a}, {f})" for s, tx, a, f in xs], "]),", indent="            ")
        if self.farm:
            o.append("    ],")
        o.append("};")
        o.append(f"// Python's statuses: {self.statuses}")
        return "\n".join(o)


def wrap(head, items, tail, width=124, indent="        "):
    """head items, tail on one line when it fits, else one indented block of lines up to width."""
    one = head + ", ".join(items) + tail
    if len(one) <= width:
        return [one]
    out, line = [head], indent
    for x in items:
        if len(line) + len(x) + 2 > width and line.strip():
            out.append(line.rstrip())
            line = indent
        line += x + ", "
    out.append(line.rstrip())
    out.append(indent[:-4] + tail)
    return out


def opt(x, s=False, b=False):
    if x is None:
        return "None"
    if s:
        return f'Some("{x}")'
    if b:
        return f"Some({str(x).lower()})"
    return f"Some({x})"


def idn(c, s):
    return "NONE" if s is None else str(c.ids[s])


def inflow_then_sell_curve(c, mint, t, trader, depth_sol, sell_q=5 * SOL, creator_sell_at=None, creator=None):
    """A buy of 6 SOL at t - 10 and a trigger-sized sell at t with pre real SOL depth_sol."""
    c.curve(t - 10, 0, mint, b58("buyer"), True, 6 * SOL, 100 * SOL, (depth_sol - 2) * SOL)
    if creator_sell_at is not None:
        c.curve(creator_sell_at, 1, mint, creator, False, SOL // 10, 100 * SOL, (depth_sol - 2) * SOL)
    c.curve(t, 0, mint, trader, False, sell_q, 100 * SOL, depth_sol * SOL - sell_q)


DEV = (446_256_000, 452_304_000)


def s1():
    """Mixed curves and pools around dev2's 1041 -> 1042 clock change; the hour grid moves at 450144000."""
    c = Case("s1_mixed", DEV2, (446_256_000, 452_304_000), n2_k=1)
    c.doc = s1.__doc__
    k = c.key
    creator, coin, tc, ka, t1, t2, t3, b = (k(x) for x in ("creator", "coin", "tokcreator", "absentcoin", "trader1", "trader2", "trader3", "buyer"))
    CA, CB, CO, CN, CP, CM, CX, CA2, CB2 = (k(x) for x in ("CA", "CB", "CO", "CN", "CP", "CM", "CX", "CA2", "CB2"))
    for m in (CA, CB, CA2, CB2):
        c.token(m, 450_100_000, creator)
    c.token(CO, 446_000_000, creator)  # before burn: no F7, an N2 candidate
    c.token(CN, 450_100_000, creator, quote=None)
    c.token(CP, 450_100_000, creator, quote=PUMPQ)
    c.token(CM, 450_100_000, creator, mayhem=True)
    # CX: no token at all
    PR1, PR2, PR3, PR4, PRC, PR5, PR6, PRD, PN1, PN2, PA, PM, PO = (k(x) for x in (
        "PR1", "PR2", "PR3", "PR4", "PRC", "PR5", "PR6", "PRD", "PN1", "PN2", "PA", "PM", "PO"))
    TN1, TN2 = k("TN1"), k("TN2")
    c.token(TN1, 450_100_000, tc, quote=PUMPQ)  # a PUMP-quoted token: pools do not test the quote
    c.token(TN2, 450_100_000, None)  # NULL creator
    for p in (PR1, PR2, PR3, PR4, PRC, PR5, PR6, PRD):
        c.pool(p, True, coin_creator=coin)
    c.pool(PN1, False, mint=TN1, coin_creator=coin)
    c.pool(PN2, False, mint=TN2, coin_creator=coin)
    c.pool(PA, False, mint=k("absentmint"), coin_creator=ka)
    c.pool(PM, False, coin_creator=coin, mayhem=True)
    # PO: an 'other' pool, absent from the SOL-market map

    F = 450_136_500  # the feed's first slot (bucket 450136080 at h 13542)
    # Candidates at F: curves at 40 and 42 SOL real, reversed pools at 310 SOL, and the rejected ones.
    c.curve(F, 0, CB, b, True, SOL, 100 * SOL, 40 * SOL)
    c.curve(F, 1, CO, b, True, SOL, 100 * SOL, 42 * SOL)
    for i, m in enumerate((CN, CP, CM, CX)):
        c.curve(F, 2 + i, m, b, True, SOL, 100 * SOL, 44 * SOL)
    c.trade(F, 10, PRC, b, "sell", SOL, 0, 310 * SOL, 0)
    c.trade(F, 11, PM, b, "buy", 0, SOL, 0, 300 * SOL)
    # CA triggers at 45 SOL; the rejected curves try the same.
    t = 450_136_700
    for m in (CA, CO, CN, CP, CM, CX):
        inflow_then_sell_curve(c, m, t, t1, 45)
    # Before the window is complete: PR1 (no token, coin creator `coin`) triggers -> H: farm window incomplete.
    t = 450_137_000
    c.trade(t - 10, 0, PR1, b, "sell", 20 * SOL, 0, 400 * SOL, 0)
    c.trade(t, 0, PR1, t2, "buy", 20 * SOL, 0, 420 * SOL, 0)
    # PR2: farmers made the volume; its trigger after the flip -> H: farm flagged; K3: pass with farm flagged.
    for i in range(3):
        c.trade(450_145_000 + i, 0, PR2, k(f"farmer{i}"), "sell", 10 * SOL, 0, 250 * SOL, 0, farmer=True)
    c.trade(450_145_100, 0, PR2, t3, "buy", SOL, 0, 255 * SOL, 0)  # organic, small
    t = 450_150_500
    c.trade(t - 5, 0, PR2, b, "sell", 5 * SOL, 0, 254 * SOL, 0)
    c.trade(t, 0, PR2, t2, "buy", 15 * SOL, 0, 259 * SOL, 0)
    # PN1 (normal, token creator tc, coin creator `coin`): the coin creator's sell is no trigger leg, and its small sell
    # in the creator window does not block (the creator is tc); the next sell triggers. At t + 300 tc's small sell
    # blocks.
    t = 450_138_000
    c.trade(t - 20, 0, PN1, b, "buy", 0, 10 * SOL, 0, 500 * SOL)
    c.trade(t - 5, 0, PN1, coin, "sell", 0, SOL, 0, 510 * SOL)
    c.trade(t, 0, PN1, coin, "sell", 0, 25 * SOL, 0, 509 * SOL)
    c.trade(t, 1, PN1, t3, "sell", 0, 20 * SOL, 0, 484 * SOL)
    c.trade(t + 280, 0, PN1, b, "buy", 0, 10 * SOL, 0, 464 * SOL)
    c.trade(t + 297, 0, PN1, tc, "sell", 0, SOL, 0, 474 * SOL)
    c.trade(t + 300, 0, PN1, t1, "sell", 0, 20 * SOL, 0, 473 * SOL)
    # PN2 (token with a NULL creator): the coin creator's sell in slot t blocks nothing, the creator being NONE.
    t = 450_138_100
    c.trade(t - 20, 0, PN2, b, "buy", 0, 10 * SOL, 0, 500 * SOL)
    c.trade(t, 0, PN2, coin, "sell", 0, 25 * SOL, 0, 510 * SOL)  # the coin creator: no trigger leg
    c.trade(t, 1, PN2, t1, "sell", 0, 25 * SOL, 0, 485 * SOL)
    # PA (mint absent: creator = coin creator ka): ka's small sell blocks at t; at t + 300 a trigger passes.
    t = 450_138_200
    c.trade(t - 20, 0, PA, b, "buy", 0, 10 * SOL, 0, 500 * SOL)
    c.trade(t - 3, 0, PA, ka, "sell", 0, SOL // 2, 0, 510 * SOL)
    c.trade(t, 0, PA, t1, "sell", 0, 25 * SOL, 0, 509 * SOL)
    c.trade(t + 290, 0, PA, b, "buy", 0, 30 * SOL, 0, 484 * SOL)
    c.trade(t + 300, 0, PA, t1, "sell", 0, 25 * SOL, 0, 514 * SOL)
    # PO and PM try to trigger: no rows.
    t = 450_138_400
    for p in (PO, PM):
        c.trade(t - 20, 0, p, b, "buy", 0, 10 * SOL, 0, 500 * SOL)
        c.trade(t, 0, p, t1, "sell", 0, 25 * SOL, 0, 510 * SOL)
    # The clock change: the first event slot of 1042, CA2 first in by_key and triggering, so its N2 still sees the
    # old bucket (CA, CB, CO); then the rollover; CB2 trades in the new bucket.
    c.curve(450_143_990, 0, CA2, b, True, 6 * SOL, 100 * SOL, 41 * SOL)
    c.curve(450_144_003, 0, CA2, t1, False, 5 * SOL, 100 * SOL, 38 * SOL + 1)
    c.curve(450_144_003, 1, CB2, b, True, SOL, 100 * SOL, 43 * SOL)
    # A slot without a block time: the day stays. PRC trades again (its pool_seen stays F) at 450 SOL.
    c.trade(450_146_000, 0, PRC, b, "sell", SOL, 0, 450 * SOL, 0, bt=None)
    # N2 collision: PR3 (400) and PR4 (520 SOL) trigger in one slot whose first event has no block time; PRC is the
    # only key in both bands, so both pick it and PR4's row wins (depth 520).
    t = 450_146_500
    c.trade(t - 30, 0, PR3, b, "sell", 20 * SOL, 0, 380 * SOL, 0)
    c.trade(t - 30, 1, PR4, b, "sell", 20 * SOL, 0, 500 * SOL, 0)
    c.trade(t, 0, PR3, t1, "buy", 16 * SOL, 0, 400 * SOL, 0, bt=None)
    c.trade(t, 1, PR4, t2, "buy", 21 * SOL, 0, 520 * SOL, 0)
    # The rollover at 450152531 (the next bucket): PR5, first in by_key, triggers and picks PRD from the previous
    # hour; then the rollover; PR6 sees only this hour (PR5's event) and picks PR5.
    c.trade(450_150_000, 0, PRD, b, "sell", SOL, 0, 1_000 * SOL, 0)
    t = 450_152_531
    c.trade(t - 40, 0, PR5, b, "sell", 20 * SOL, 0, 980 * SOL, 0)
    c.trade(t - 40, 1, PR6, b, "sell", 20 * SOL, 0, 1_500 * SOL, 0)
    c.trade(t, 0, PR5, t1, "buy", 40 * SOL, 0, 1_000 * SOL, 0)
    c.trade(t, 1, PR6, t2, "buy", 44 * SOL, 0, 1_100 * SOL, 0)
    return c


def s2():
    """The 24 h pool_seen prune reads pool_last (dev2, epochs 1042-1043)."""
    c = Case("s2_pool_seen", DEV2, (446_256_000, 452_304_000), n2_k=0)
    c.doc = s2.__doc__
    k = c.key
    PL1, PL2, X, coin, b, t1 = (k(x) for x in ("PL1", "PL2", "X", "coin", "buyer", "trader1"))
    for p in (PL1, PL2, X):
        c.pool(p, True, coin_creator=coin)
    s0 = 450_200_000
    c.trade(s0, 0, PL1, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(s0, 1, PL2, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(s0 + 150_000, 0, PL1, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(s0 + 300_000, 0, PL1, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(s0 + 350_000, 0, X, b, "sell", SOL, 0, 300 * SOL, 0)  # a rollover without PL2: PL2 is forgotten
    for p, t in ((PL1, s0 + 400_020), (PL2, s0 + 400_010)):
        c.trade(t - 10, 0, p, b, "sell", 20 * SOL, 0, 300 * SOL, 0)
        c.trade(t, 0, p, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


def s3():
    """The decision-bound edges dlo - 1, dlo, dhi, dhi + 1 (reversed pools; fwd1053's clock, live-like bounds)."""
    clock = Clock({str(k): v for k, v in FWD.items()})
    dev = (454_700_000, 455_400_000)
    dlo, dhi = decision_bounds(None, *dev, clock)
    c = Case("s3_bounds", FWD, dev, n2_k=1)
    c.doc = s3.__doc__
    k = c.key
    coin, b, t1 = k("coin"), k("buyer"), k("trader1")
    for i, t in enumerate((dlo - 1, dlo, dhi, dhi + 1)):
        p = k(f"E{i}")
        c.pool(p, True, coin_creator=coin)
        c.trade(t - 10, 0, p, b, "sell", 20 * SOL, 0, 300 * SOL, 0)
        c.trade(t, 0, p, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


def t2(as_sol):
    """T2: a pool modelled on 8iXGs3rS... (class 'other', normal, PUMP-quoted token, coin_creator 5cHQC...)."""
    c = Case("t2_sol" if as_sol else "t2_other", DEV2, (446_256_000, 452_304_000), n2_k=1)
    c.doc = ("the same legs with a 'sol' PoolMeta: one F7 row." if as_sol else
             "T2, a pool modelled on 8iXGs3rS… (class 'other', normal, PUMP-quoted token, coin_creator 5cHQC…), as stored: "
             "not a SOL market.")
    P, M, cc, b, tr = "8iXGs3rSqshaWD9iCZEXu5XLvD5CUhuu7DSA7kd3Cg92", c.key("t2mint"), c.key("5cHQC"), c.key("buyer"), c.key("seller")
    c.labels[c.id(P)] = "8iXGs (T2)"
    c.token(M, 450_100_000, c.key("t2creator"), quote=PUMPQ)
    if as_sol:
        c.pool(P, False, mint=M, coin_creator=cc)
    t = 450_145_128
    c.trade(t - 30, 0, P, b, "buy", 0, 40 * SOL, 0, 2_524_283_573_690)
    c.trade(t, 0, P, tr, "sell", 0, 195_002_882_750, 0, 2_564_283_573_690)
    return c


def x1():
    """A new hour's first slot: pool P (tx 0) triggers and rolls the hour before curve C (tx 1) triggers; each has an
    N2 candidate in the old hour (curve D at 44 SOL, reversed pool Q at 400 SOL). Only P's N2 sees the old hour."""
    c = Case("x1_pool_then_curve", DEV2, DEV, n2_k=1)
    c.doc = x1.__doc__
    k = c.key
    coin, b, t1, t2, cr = k("coin"), k("buyer"), k("trader1"), k("trader2"), k("creator")
    P, Q, C, D = k("P"), k("Q"), k("C"), k("D")
    for m in (C, D):
        c.token(m, 450_100_000, cr)
    for p in (P, Q):
        c.pool(p, True, coin_creator=coin)
    S = 450_152_531  # a bucket start at h 13441
    c.curve(450_150_000, 0, D, b, True, SOL, 100 * SOL, 44 * SOL)
    c.trade(450_150_010, 0, Q, b, "sell", SOL, 0, 400 * SOL, 0)
    c.trade(S - 10, 0, P, b, "sell", 20 * SOL, 0, 380 * SOL, 0)
    c.curve(S - 10, 1, C, b, True, 6 * SOL, 100 * SOL, 43 * SOL)
    c.trade(S, 0, P, t1, "buy", 16 * SOL, 0, 400 * SOL, 0)
    c.curve(S, 1, C, t2, False, 5 * SOL, 100 * SOL, 40 * SOL)  # depth 45 SOL: D is in its band
    return c


def x2():
    """dev2 1036 -> 1037 (n 11352 -> 13510): at the add of L the lazy prune takes n at L, so nothing goes (the first
    item, L - 25000, is not before L - 27020) and the farmer trade at L - 12000 flags the trigger at L + 1."""
    c = Case("x2_lazy_prune_n_at_last", DEV2, DEV, n2_k=0)
    c.doc = x2.__doc__
    k = c.key
    coin, b, t1, f = k("coin"), k("buyer"), k("trader1"), k("farmer")
    R = k("R")
    c.pool(R, True, coin_creator=coin)
    L = 1037 * 432_000 + 50
    for d in (25_000, 18_000):
        c.trade(L - d, 0, R, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(L - 12_000, 0, R, f, "sell", 100 * SOL, 0, 300 * SOL, 0, farmer=True)
    c.trade(L - 5_000, 0, R, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(L, 0, R, b, "sell", 2 * SOL, 0, 300 * SOL, 0)
    c.trade(L + 1, 0, R, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


def x3():
    """A pool creator's SOL-side sell after the trigger leg in slot t blocks: the creator window ends at t, not t - 1
    (reversed pool R, no token, so the creator is the coin creator). The control R2 has the same legs with the sell at
    t2 + 1: it triggers."""
    c = Case("x3_pool_creator_sell_in_t", DEV2, DEV, n2_k=0)
    c.doc = x3.__doc__
    k = c.key
    coin, b, t1 = k("coin"), k("buyer"), k("trader1")
    R, R2 = k("R"), k("R2")
    for p in (R, R2):
        c.pool(p, True, coin_creator=coin)
    for p, t, (cs, ctx) in ((R, 450_200_000, (0, 1)), (R2, 450_201_000, (1, 0))):
        c.trade(t - 10, 0, p, b, "sell", 20 * SOL, 0, 300 * SOL, 0)
        c.trade(t, 0, p, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
        c.trade(t + cs, ctx, p, coin, "buy", SOL, 0, 305 * SOL, 0)
    return c


def x4():
    """A pool's N2 depth is its slot's last leg of any kind: Q's deposit after its sell (pre b 520 SOL) puts Q outside
    P's band [336, 504], where the sell's 420 SOL would be inside."""
    c = Case("x4_n2_depth_last_leg", DEV2, DEV, n2_k=1)
    c.doc = x4.__doc__
    k = c.key
    coin, b, t1, lp = k("coin"), k("buyer"), k("trader1"), k("lp")
    P, Q = k("P"), k("Q")
    for p in (P, Q):
        c.pool(p, True, coin_creator=coin)
    t = 450_200_000
    c.trade(t - 100, 0, Q, b, "sell", 100 * SOL, 0, 420 * SOL, 0)
    c.trade(t - 100, 1, Q, lp, "deposit", 10 * SOL, 0, 520 * SOL, 0)
    c.trade(t - 10, 0, P, b, "sell", 20 * SOL, 0, 400 * SOL, 0)
    c.trade(t, 0, P, t1, "buy", 17 * SOL, 0, 420 * SOL, 0)
    return c


def x5():
    """The 24 h prune is strict: at the rollover S, PL (pool_last exactly S - slots(24 h, S)) keeps its pool_seen and
    PM (one slot earlier) loses it."""
    c = Case("x5_pool_seen_edge", DEV2, DEV, n2_k=0)
    c.doc = x5.__doc__
    k = c.key
    coin, b, t1 = k("coin"), k("buyer"), k("trader1")
    PL, PM, X = k("PL"), k("PM"), k("X")
    for p in (PL, PM, X):
        c.pool(p, True, coin_creator=coin)
    clock = Clock({str(e): v for e, v in DEV2.items()})
    h = clock.slots(3_600_000, 450_400_000)
    S = 450_400_000 - 450_400_000 % h + h
    a = S - clock.slots(86_400_000, S)
    assert S // 432_000 == 1042 and clock.slots(3_600_000, S) == h and (S, a) == (450_407_910, 450_085_317), (S, a)
    c.trade(a - 1, 0, PM, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(a, 0, PL, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(S, 0, X, b, "sell", SOL, 0, 300 * SOL, 0)
    for p, t in ((PL, S + 110), (PM, S + 130)):
        c.trade(t - 10, 0, p, b, "sell", 20 * SOL, 0, 300 * SOL, 0)
        c.trade(t, 0, p, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


def x6():
    """dev2 1036 -> 1037: the lazy prune fires at the add of L (the first item, L - 30000, is before L - 2 n(L)) and
    keeps from L - n(L) = L - 13510, so the farmer trade at L - 12000 stays (L - n at the first item would drop it)."""
    c = Case("x6_lazy_prune_keeps_from_n_at_last", DEV2, DEV, n2_k=0)
    c.doc = x6.__doc__
    k = c.key
    coin, b, t1, f = k("coin"), k("buyer"), k("trader1"), k("farmer")
    R = k("R")
    c.pool(R, True, coin_creator=coin)
    L = 1037 * 432_000 + 50
    for d in (30_000, 20_000):  # gaps under an hour: the rollovers keep R
        c.trade(L - d, 0, R, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(L - 12_000, 0, R, f, "sell", 100 * SOL, 0, 300 * SOL, 0, farmer=True)
    c.trade(L, 0, R, b, "sell", 2 * SOL, 0, 300 * SOL, 0)
    c.trade(L + 1, 0, R, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


def x7():
    """dev2 1036 -> 1037: the rollover at S (in 1037, X first) takes n at S (13510), so R, whose last trade is a farmer
    trade at S - 12000 in 1036, stays (n at that trade, 11352, would drop it); R's trigger at S + 5 is flagged."""
    c = Case("x7_rollover_n_at_slot", DEV2, DEV, n2_k=0)
    c.doc = x7.__doc__
    k = c.key
    coin, b, t1, f = k("coin"), k("buyer"), k("trader1"), k("farmer")
    R, X, Z = k("R"), k("X"), k("Z")
    for p in (R, X, Z):
        c.pool(p, True, coin_creator=coin)
    S = 1037 * 432_000 + 50
    c.trade(S - 30_000, 0, Z, b, "sell", SOL, 0, 300 * SOL, 0)  # the feed's first slot: the hour is complete at S + 5
    c.trade(S - 12_000, 0, R, f, "sell", 100 * SOL, 0, 300 * SOL, 0, farmer=True)
    c.trade(S, 0, X, b, "sell", SOL, 0, 300 * SOL, 0)
    c.trade(S, 1, R, b, "sell", 20 * SOL, 0, 300 * SOL, 0)
    c.trade(S + 5, 0, R, t1, "buy", 15 * SOL, 0, 320 * SOL, 0)
    return c


T1 = "7orRHgZjwNmvpcSBHUMQ8wqW6rwKpqE1hJJR43AkQa1Z"
T1_LEGS = [  # dev2, the spec's appendix query
    (450196501, 923, 3, 27, True, 80346362644573, 88859178201, 992653637355427, 1186685931639, 712753637355427, 88859178201, "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc"),
    (450196501, 924, 1, 6, True, 32130101425978, 39695372283, 960523535929449, 1226381303922, 680623535929449, 128554550484, "SLURahzredh2LEoFXnteqzvLh16ZjNu9ZLGLUNA3X64"),
    (450196501, 924, 3, 6, True, 29896604844106, 39397782299, 930626931085343, 1265779086221, 650726931085343, 167952332783, "CSkYjaWv6RmCwPg9UARpaJEbig8pQrSwwmJ2jX352Mx3"),
    (450196501, 925, 1, 6, True, 28090766603369, 39396432279, 902536164481974, 1305175518500, 622636164481974, 207348765062, "8uF4DDpcpCvkTnudWviS2aRtfVNJ9nf4uvydaB4jDq7E"),
    (450196514, 942, 2, 9, False, 40173181322286, 55619532144, 942709345804260, 1249555986356, 662809345804260, 151729232918, "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc"),
    (450196532, 804, 2, 9, False, 40173181322287, 51072877812, 982882527126547, 1198483108544, 702982527126547, 100656355106, "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc"),
    (450196603, 826, 3, 7, False, 32130101425978, 37937837177, 1015012628552525, 1160545271367, 735112628552525, 62718517929, "SLURahzredh2LEoFXnteqzvLh16ZjNu9ZLGLUNA3X64"),
    (450196616, 754, 3, 7, False, 28090766603369, 31253475448, 1043103395155894, 1129291795919, 763203395155894, 31465042481, "8uF4DDpcpCvkTnudWviS2aRtfVNJ9nf4uvydaB4jDq7E"),
    (450196616, 758, 3, 7, False, 29896604844106, 31465042475, 1073000000000000, 1097826753444, 793100000000000, 6, "CSkYjaWv6RmCwPg9UARpaJEbig8pQrSwwmJ2jX352Mx3"),
    (450196616, 820, 3, 6, True, 41918882589137, 44632444533, 1031081117410863, 1142459197977, 751181117410863, 44632444539, "GG5ATPW7bxGm5y4aGa2uWWZV1JvjETiM2Rabc2fT8Y7f"),
    (450197178, 473, 2, 6, False, 41918882589137, 44632444532, 1073000000000000, 1097826753445, 793100000000000, 7, "GG5ATPW7bxGm5y4aGa2uWWZV1JvjETiM2Rabc2fT8Y7f"),
    (450197179, 10, 4, 6, True, 49999999999998, 53657221577, 1023000000000002, 1151483975022, 743100000000002, 53657221584, "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9"),
    (450197213, 1, 5, 6, False, 49999999999998, 53657221576, 1073000000000000, 1097826753446, 793100000000000, 8, "6Ljyr7EoLpPyvbWUZ6Urh126cYLxDXVsRoHve1WWxok9"),
]


def t1(name, quote="as stored", mayhem=False, absent=False):
    """T1's 13 real legs on the engine (as the scratch t1_engine.py, without the store)."""
    c = Case(name, DEV2, (446_256_000, 452_304_000), n2_k=1)
    c.id(T1)
    creator = "2CFywHXDPjDK2iRQsb95vnjgncDUZeQKJ6MceJ4ALpdc"
    if not absent:
        c.token(T1, 450196501, creator, mayhem=mayhem, quote=PUMPQ if quote == "as stored" else quote)
    for slot, tx, oix, iix, is_buy, tok, q, vt, vq, rt, rq, trader in T1_LEGS:
        c.id(trader)
        post = Curve(vt, vq, rt, rq, 95, 30)
        s = 1 if is_buy else -1
        pre = Curve(vt + s * tok, vq - s * q, rt + s * tok, rq - s * q, 95, 30)
        leg = CurveLeg(slot, tx, (slot, tx, oix, iix), is_buy, tok, q, 0, "buy" if is_buy else "sell", pre, post, trader,
                       max_cost=10**15 if is_buy else None, min_out=0 if not is_buy else None, direct=True)
        c.events.append((slot, tx, c.bt(slot), "curve", T1, leg, (is_buy, q, vq, rq)))
    return c


if __name__ == "__main__":
    cases = [s1(), s2(), s3(), t2(False), t2(True), t1("t1_stored"), t1("t1_ones", quote=ONES), t1("t1_wsol", quote=WSOL),
             t1("t1_quote_null", quote=None), t1("t1_absent", absent=True), t1("t1_mayhem", quote=ONES, mayhem=True),
             x1(), x2(), x3(), x4(), x5(), x6(), x7()]
    want = sys.argv[1:]
    for c in cases:
        c.run()
        if not want or c.name in want:
            print(c.rust())
            print()
        print(f"{c.name}: {len(c.rows)} rows, statuses {c.statuses}", file=sys.stderr)
