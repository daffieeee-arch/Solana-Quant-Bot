"""The deploy's check of rule (c)'s file for the recorder's learned tx_new_reversed exclusions (src/exclude.rs): before
the second measuring hour, on the VPS. Read-only.

1. The file as the recorder reads it (blank lines and `#` comments skipped; a line that is no address refused): its
   sha256 and address count, against --expect-sha256 / --expect-count when given (a mismatch: exit 1).
2. The measuring hour's fee recipients (fixtures/measure-20261020-recipients.json, the protocol and buyback recipients
   of its tx_new_reversed trades with their transaction counts) that the file holds: those are never learned, so the
   expected cut of the stream is lower. Without the hour's recording the coverage is approximate (the 64 most frequent
   allowed recipients' transaction counts summed: a trade naming two of them counts twice).
3. With --hour ROOT (recorder-measure-20261020): the exact cut, per transaction: the hour's tx_new_reversed trades that
   name a learnable recipient among the 64 most frequent, with and without the file, as a share of all the hour's
   tx_new_reversed messages.

    python lab/recorder/check_creation_accounts.py --file FILE [--expect-sha256 S] [--expect-count N] [--hour ROOT]
"""

import argparse
import glob
import hashlib
import json
import os
import sys
from collections import Counter

import creation_accounts as ca

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "measure-20261020-recipients.json")
CAP = 64
ROLES = ("protocol_fee_recipient", "buyback_fee_recipient")
# exclude.rs GUARD
GUARD = {"11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
         "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
         "ComputeBudget111111111111111111111111111111", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
         "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw", "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"}


def fail(why):
    print(f"check_creation_accounts: {why}", file=sys.stderr)
    sys.exit(1)


def read_file(path):
    """(addresses, sha256 of the bytes), as exclude.rs read_accounts reads it."""
    try:
        data = open(path, "rb").read()
    except OSError as e:
        fail(f"cannot read {path}: {e}")
    try:
        text = data.decode()
    except UnicodeDecodeError:
        fail(f"{path} is not UTF-8")
    out = set()
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not ca.is_address(line):
            fail(f"{path} line {i}: not an address")
        out.add(line)
    if not out:
        fail(f"{path} holds no address")
    return out, hashlib.sha256(data).hexdigest()


def top(counts, allowed):
    """The CAP most frequent allowed recipients (count, then address), as exclude.rs fills a fresh set."""
    return {a for a, _ in sorted(((a, n) for a, n in counts.items() if allowed(a)), key=lambda x: (-x[1], x[0]))[:CAP]}


def hour_cut(root, in_file):
    """The exact cut on the hour's recording: tx_new_reversed messages, its trade transactions, and those a top-64 set
    excludes without and with the file."""
    import duckdb
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 1})
    arrivals = glob.glob(os.path.join(root, "segments", "*", "arrivals", "*.parquet"))
    trades = [f for t in ("BuyEvent", "SellEvent") for f in glob.glob(os.path.join(root, "segments", "*", "pump_amm", t, "*.parquet"))]
    if not arrivals or not trades:
        fail(f"{root}: no arrivals or no trades under segments/")
    con.execute("CREATE TEMP TABLE nr AS SELECT DISTINCT signature FROM read_parquet(?, union_by_name = true) "
                "WHERE stream = 'tx_new_reversed'", [arrivals])
    messages = con.execute("SELECT count(*) FROM nr").fetchone()[0]
    per_tx = {}
    for sig, ix in con.execute("SELECT t.signature, t.ix_accounts FROM read_parquet(?, union_by_name = true) t SEMI JOIN nr USING (signature) "
                               "WHERE t.ix_accounts IS NOT NULL", [trades]).fetchall():
        d = json.loads(ix)
        per_tx.setdefault(sig, set()).update(d[r] for r in ROLES if isinstance(d.get(r), str))
    con.close()
    counts = Counter(a for s in per_tx.values() for a in s)
    cut = {}
    for name, allowed in (("without_file", lambda a: a not in GUARD), ("with_file", lambda a: a not in GUARD and a not in in_file)):
        learned = top(counts, allowed)
        n = sum(1 for s in per_tx.values() if s & learned)
        cut[name] = {"learned": len(learned), "trade_transactions_excluded": n,
                     "share_of_messages": round(n / messages, 4) if messages else None}
    return {"messages": messages, "trade_transactions": len(per_tx), "cut": cut}


def check(path, expect_sha=None, expect_count=None, recipients=FIXTURE, hour=None):
    accounts, sha = read_file(path)
    out = {"file": os.path.abspath(path), "sha256": sha, "count": len(accounts), "ok": True, "problems": []}
    if expect_sha and sha != expect_sha:
        out["problems"].append(f"sha256 {sha} is not the expected {expect_sha}")
    if expect_count is not None and len(accounts) != expect_count:
        out["problems"].append(f"{len(accounts)} addresses, not the expected {expect_count}")
    fx = json.load(open(recipients))
    rec = fx["recipients"]
    held = sorted(a for a in rec if a in accounts)
    allowed = lambda a: a not in GUARD and a not in accounts  # noqa: E731
    learned = top(rec, allowed)
    out["recipients"] = {
        "source": os.path.relpath(recipients, HERE), "total": len(rec), "in_file": len(held),
        "in_file_transactions": sum(rec[a] for a in held), "in_file_addresses": held,
        "top64_transactions_approx": {"without_file": sum(rec[a] for a in top(rec, lambda a: a not in GUARD)),
                                      "with_file": sum(rec[a] for a in learned)},
        "hour_trade_transactions": fx["counts"]["trade_transactions"]}
    if hour:
        out["hour"] = hour_cut(hour, accounts)
    out["ok"] = not out["problems"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", required=True)
    ap.add_argument("--expect-sha256")
    ap.add_argument("--expect-count", type=int)
    ap.add_argument("--recipients", default=FIXTURE)
    ap.add_argument("--hour", help="the measuring hour's recorder root, for the exact cut")
    a = ap.parse_args(argv)
    out = check(a.file, a.expect_sha256, a.expect_count, a.recipients, a.hour)
    print(json.dumps(out, indent=1))
    if not out["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
