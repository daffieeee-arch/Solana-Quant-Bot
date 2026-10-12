"""Which Yellowstone transaction filter catches PumpSwap pool creations cheaply?

The live recorder only receives full transactions of its watchlist, so it does not see a pool being
created (M1 trades young pools). A Yellowstone transaction filter matches when the transaction has
every `account_required` account, at least one `account_include` account (if any) and no
`account_exclude` account. Every rule here requires the PumpSwap program, so only transactions
touching PumpSwap can match. Measured on chunks with the `ix_accounts` column (epoch 1052 on):

- create transactions: those with a pump_amm CreatePoolEvent, by how the pool was made (migration:
  the transaction also has pump's CompletePumpAmmMigrationEvent; direct otherwise) and pool class
  (sol: quote is WSOL, reversed: base is WSOL, other);
- other transactions: every other successful transaction with a pump_amm event, hash-sampled.

A transaction's accounts are what the dataset shows of it: the accounts of every event's parent
instruction (`ix_accounts`, remaining accounts included), fee payer, signers, outer and parent
program. Accounts used only by other instructions are invisible, so for a required-account rule
the matches among other transactions are a lower bound, and for an exclude rule the creations
lost are a lower bound. The recorder's byte meter in the first hour gives the real figure.

Rules:
  1. account_required [PumpSwap, A] for single accounts A;
  2. account_required [PumpSwap, A, B] for pairs of the best singles;
  3. account_include [PumpSwap], account_exclude E: E built greedily from accounts frequent in other
     transactions, while at most --max-lost of the creations of each group are lost.

Usage: python creation_filter.py [--chunks DIR] [--slots LO:HI] [--sample-pct 5] [--out result.json] [--md result.md]
Default window: the first 100,000 slots of the complete chunks that have ix_accounts.
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import duckdb

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
B58 = "[1-9A-HJ-NP-Za-km-z]{32,44}"
DEFAULT_CHUNKS = "/home/chupa/Solana-project/data-old-faithful-one/lab/events/v1/chunks"


def chunks_with_accounts(root):
    """(start, end, dir) of complete chunks whose build wrote ix_accounts."""
    out = []
    for m in glob.glob(os.path.join(root, "*", "_manifest.json")):
        with open(m) as f:
            man = json.load(f)
        if man.get("status") == "complete" and "ix_accounts" in (man.get("event_context") or ()):
            out.append((man["slot_start"], man["slot_end_exclusive"], os.path.dirname(m)))
    return sorted(out)


def src(chunks, table):
    fs = sorted(f for _, _, d in chunks for f in glob.glob(os.path.join(d, table, "*.parquet")))
    if not fs:
        return None
    return "read_parquet([" + ",".join("'" + f.replace("'", "''") + "'" for f in fs) + "], union_by_name = true)"


def tables(chunks):
    return sorted({os.path.relpath(os.path.dirname(f), d) for _, _, d in chunks
                   for f in glob.glob(os.path.join(d, "pump*", "*", "*.parquet"))})


def collect(con, chunks, lo, hi, pct):
    """Temp tables txs(slot, tx_index, is_create, grp) and acc(slot, tx_index, account), and the
    address -> IDL account name map."""
    cp = src(chunks, "pump_amm/CreatePoolEvent")
    mig = src(chunks, "pump/CompletePumpAmmMigrationEvent")
    con.execute(f"""CREATE TEMP TABLE creates AS
        SELECT slot, tx_index, any_value(base_mint) AS base, any_value(quote_mint) AS quote
        FROM {cp} WHERE slot >= {lo} AND slot < {hi} GROUP BY slot, tx_index""")
    mig_sql = (f"SELECT DISTINCT slot, tx_index FROM {mig} WHERE slot >= {lo} AND slot < {hi}" if mig
               else "SELECT NULL::UBIGINT AS slot, NULL::UBIGINT AS tx_index WHERE false")
    amm = [t for t in tables(chunks) if t.startswith("pump_amm/")]
    amm_txs = " UNION ".join(f"SELECT slot, tx_index FROM {src(chunks, t)} WHERE slot >= {lo} AND slot < {hi}" for t in amm)
    con.execute(f"""CREATE TEMP TABLE txs AS
        WITH m AS ({mig_sql}), a AS ({amm_txs})
        SELECT a.slot, a.tx_index, c.slot IS NOT NULL AS is_create,
               CASE WHEN c.slot IS NULL THEN 'other_tx'
                    ELSE (CASE WHEN m.slot IS NOT NULL THEN 'migration' ELSE 'direct' END) || '/' ||
                         (CASE WHEN c.base = '{WSOL}' THEN 'reversed' WHEN c.quote = '{WSOL}' THEN 'sol' ELSE 'other' END)
               END AS grp
        FROM a LEFT JOIN creates c USING (slot, tx_index) LEFT JOIN m USING (slot, tx_index)
        WHERE c.slot IS NOT NULL OR hash(a.slot, a.tx_index) % 1000 < {int(pct * 10)}""")
    parts = []
    for t in tables(chunks):
        s = src(chunks, t)
        cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {s}").fetchall()}
        if "ix_accounts" not in cols:
            continue
        signers = "string_split(COALESCE(signers, ''), ',')" if "signers" in cols else "[]::VARCHAR[]"
        parts.append(f"""SELECT e.slot, e.tx_index,
              list_concat(list_concat(regexp_extract_all(COALESCE(ix_accounts, ''), '"({B58})"', 1),
                                      [fee_payer, outer_program, parent_program]), {signers}) AS accts,
              regexp_extract_all(COALESCE(ix_accounts, ''), '"([a-z0-9_]+)":\\s*"{B58}"', 1) AS names,
              regexp_extract_all(COALESCE(ix_accounts, ''), '"[a-z0-9_]+":\\s*"({B58})"', 1) AS named
            FROM {s} e SEMI JOIN txs USING (slot, tx_index) WHERE e.slot >= {lo} AND e.slot < {hi}""")
    con.execute("CREATE TEMP TABLE raw AS " + " UNION ALL ".join(parts))
    con.execute(f"""CREATE TEMP TABLE acc AS SELECT DISTINCT slot, tx_index, a AS account
                    FROM (SELECT slot, tx_index, unnest(accts) AS a FROM raw) WHERE a IS NOT NULL AND a <> ''""")
    names = defaultdict(Counter)
    for name, addr, n in con.execute("""SELECT n, a, count(*) FROM (SELECT unnest(names) AS n, unnest(named) AS a FROM raw)
                                        GROUP BY 1, 2""").fetchall():
        names[addr][name] += n
    con.execute("DROP TABLE raw")
    return {a: c.most_common(1)[0][0] for a, c in names.items()}


def bitsets(con, accounts):
    """{account: (create bits, other bits)}, the transaction index lists and their groups."""
    tx = con.execute("SELECT slot, tx_index, is_create, grp FROM txs ORDER BY is_create DESC, slot, tx_index").fetchall()
    c_idx, o_idx, groups = {}, {}, []
    for s, i, is_create, grp in tx:
        if is_create:
            c_idx[(s, i)] = len(c_idx)
            groups.append(grp)
        else:
            o_idx[(s, i)] = len(o_idx)
    con.execute("CREATE TEMP TABLE cand (account VARCHAR)")
    con.executemany("INSERT INTO cand VALUES (?)", [(a,) for a in accounts])
    idx = defaultdict(lambda: ([], []))
    for s, i, a in con.execute("SELECT slot, tx_index, account FROM acc SEMI JOIN cand USING (account)").fetchall():
        k = (s, i)
        if k in c_idx:
            idx[a][0].append(c_idx[k])
        elif k in o_idx:
            idx[a][1].append(o_idx[k])
    con.execute("DROP TABLE cand")

    def as_int(positions, n):
        b = bytearray(n // 8 + 1)
        for p in positions:
            b[p >> 3] |= 1 << (p & 7)
        return int.from_bytes(b, "little")

    bits = defaultdict(lambda: (0, 0))
    for a, (c, o) in idx.items():
        bits[a] = (as_int(c, len(c_idx)), as_int(o, len(o_idx)))
    return bits, len(c_idx), len(o_idx), groups


def group_masks(groups):
    out = defaultdict(int)
    for i, g in enumerate(groups):
        out[g] |= 1 << i
    return dict(out)


def catch(cbits, masks):
    return {g: round((cbits & m).bit_count() / m.bit_count(), 4) for g, m in sorted(masks.items())}


def analyse(con, chunks, lo, hi, pct, top=30, max_lost=0.01, bytes_per_tx=3000):
    names = collect(con, chunks, lo, hi, pct)
    n_create, n_other = con.execute("SELECT count(*) FILTER (WHERE is_create), count(*) FILTER (WHERE NOT is_create) FROM txs").fetchone()
    if not n_create:
        raise SystemExit("no pool creation in the window")
    blocks = src(chunks, "blocks")
    span_s = None
    if blocks:
        t0, t1 = con.execute(f"SELECT min(block_time), max(block_time) FROM {blocks} WHERE slot >= {lo} AND slot < {hi}").fetchone()
        span_s = (t1 - t0) if t0 is not None and t1 is not None and t1 > t0 else None
    per_day = 86400 / span_s if span_s else None
    other_per_day = n_other * 100 / pct * per_day if per_day else None
    creates_per_day = n_create * per_day if per_day else None

    # Candidates: accounts in many creations (required rules) or in many other txs (exclude rules).
    req = [a for a, n in con.execute(f"""SELECT account, count(*) FROM acc JOIN txs USING (slot, tx_index)
                                          WHERE is_create AND account <> '{PUMP_AMM}' GROUP BY 1
                                          HAVING count(*) >= {max(1, n_create // 20)} ORDER BY 2 DESC""").fetchall()]
    exc = [a for a, n in con.execute(f"""SELECT account, count(*) FROM acc JOIN txs USING (slot, tx_index)
                                          WHERE NOT is_create AND account <> '{PUMP_AMM}' GROUP BY 1
                                          HAVING count(*) >= {max(1, n_other // 1000)} ORDER BY 2 DESC LIMIT 2000""").fetchall()]
    bits, nc, no, groups = bitsets(con, set(req) | set(exc))
    masks = group_masks(groups)
    all_c = (1 << nc) - 1
    all_o = (1 << no) - 1

    def scale(other_hits):
        return round(other_hits * 100 / pct * per_day) if per_day else None

    def rule(kind, accounts, c, o):
        r = {"rule": kind, "accounts": [{"address": a, "name": names.get(a)} for a in accounts],
             "catch": catch(c, masks), "catch_all": round(c.bit_count() / nc, 4),
             "other_share": round(o.bit_count() / no, 6) if no else None, "other_per_day": scale(o.bit_count())}
        if per_day:
            r["gb_per_day"] = round(((c.bit_count() * per_day) + r["other_per_day"]) * bytes_per_tx / 1e9, 3)
        return r

    singles = sorted((rule("required", [a], *bits[a]) for a in req), key=lambda r: (-r["catch_all"], r["other_share"]))
    best = [r["accounts"][0]["address"] for r in singles[:top]]
    pairs = []
    for i, a in enumerate(best):
        for b in best[i + 1:]:
            pairs.append(rule("required", [a, b], bits[a][0] & bits[b][0], bits[a][1] & bits[b][1]))
    pairs.sort(key=lambda r: (-r["catch_all"], r["other_share"]))

    # Exclude rule: greedy, each step removes the most remaining other txs while every group keeps
    # at least 1 - max_lost of its creations.
    keep_c, keep_o, chosen, steps = all_c, all_o, [], []
    while True:
        best_a, best_gain = None, 0
        for a in exc:
            if a in chosen:
                continue
            c_after = keep_c & ~bits[a][0]
            if any((c_after & m).bit_count() < (1 - max_lost) * m.bit_count() for m in masks.values()):
                continue
            gain = (keep_o & bits[a][1]).bit_count()
            if gain > best_gain:
                best_a, best_gain = a, gain
        if best_a is None or best_gain < max(1, no // 1000):
            break
        chosen.append(best_a)
        keep_c &= ~bits[best_a][0]
        keep_o &= ~bits[best_a][1]
        steps.append(rule("exclude", list(chosen), keep_c, keep_o))
    return {
        "window": [lo, hi], "span_seconds": span_s, "sample_pct": pct,
        "create_txs": n_create, "create_groups": {g: m.bit_count() for g, m in sorted(masks.items())},
        "creates_per_day": round(creates_per_day) if creates_per_day else None,
        "other_txs_sampled": n_other, "other_txs_per_day": round(other_per_day) if other_per_day else None,
        "bytes_per_tx_assumed": bytes_per_tx,
        "required_single": singles[:top], "required_pair": pairs[:top], "exclude_greedy": steps,
        "note": "other matches of required rules and creations lost by exclude rules are lower bounds "
                "(only parent-instruction accounts, fee payer, signers and programs are visible)",
    }


def markdown(res):
    def acc(r):
        return " + ".join(f"`{a['name'] or '?'}` {a['address']}" for a in r["accounts"])

    groups = list(res["create_groups"])
    head = "| accounts | " + " | ".join(groups) + " | other tx/day | GB/day |\n|---|" + "---|" * (len(groups) + 2)
    out = [f"# Creation filter on slots {res['window'][0]}–{res['window'][1]}", "",
           f"Creations: {res['create_txs']} ({res['create_groups']}), ~{res['creates_per_day']}/day. "
           f"Other PumpSwap txs: ~{res['other_txs_per_day']}/day ({res['sample_pct']}% sample). {res['note']}.", ""]
    for title, key, extra in (("account_required [PumpSwap, A]", "required_single", ""),
                              ("account_required [PumpSwap, A, B]", "required_pair", ""),
                              ("account_include [PumpSwap], account_exclude E (greedy)", "exclude_greedy", "")):
        out += [f"## {title}", "", head]
        for r in res[key][:15]:
            out.append(f"| {acc(r)} | " + " | ".join(str(r["catch"].get(g, "")) for g in groups)
                       + f" | {r['other_per_day']} | {r.get('gb_per_day')} |")
        out.append(extra)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=os.environ.get("LAB_CHUNKS", DEFAULT_CHUNKS))
    ap.add_argument("--slots", default=None, help="LO:HI (default: the first 100,000 slots with ix_accounts)")
    ap.add_argument("--sample-pct", type=float, default=5.0)
    ap.add_argument("--max-lost", type=float, default=0.01)
    ap.add_argument("--memory", default=os.environ.get("LAB_DUCKDB_MEM", "4GB"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    a = ap.parse_args()
    chunks = chunks_with_accounts(a.chunks)
    if not chunks:
        sys.exit("no complete chunk with ix_accounts under " + a.chunks)
    lo, hi = (map(int, a.slots.split(":")) if a.slots else (chunks[0][0], chunks[0][0] + 100_000))
    chunks = [c for c in chunks if c[0] < hi and lo < c[1]]
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{a.memory}'; SET threads TO 4; SET preserve_insertion_order = false")
    res = analyse(con, chunks, lo, hi, a.sample_pct, max_lost=a.max_lost)
    text = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    if a.md:
        with open(a.md, "w") as f:
            f.write(markdown(res))
    print(markdown(res))


if __name__ == "__main__":
    main()
