"""Rule (c)'s file for the recorder's learned tx_new_reversed exclusions (creation-filters spec v0.2 P1, src/exclude.rs):
every account of every reversed pool creation (a PumpSwap CreatePoolEvent with base WSOL) in OF1's extracted chunks of
a slot range: its ix_accounts, signers and fee_payer. One address per line, sorted, after `#` header lines naming the
range, the chunks and the counts. Read-only on the chunks; --out is written atomically; its sha256 is printed.

Refused (exit 1, nothing written):
- a range that touches the hold-out (epochs 1047-1051) or the clean period (1055-1063);
- a range the complete chunks do not cover without a gap;
- a chunk without ix_accounts in its CreatePoolEvent rows (an older extractor);
- no reversed creation at all (an empty input is no evidence).

    python lab/recorder/creation_accounts.py --chunks DIR --from 454896000 --to 455760000 --out FILE
"""

import argparse
import glob
import hashlib
import json
import os
import sys

import duckdb

WSOL = "So11111111111111111111111111111111111111112"
EPOCH = 432_000
CLOSED = {"hold-out 1047-1051": (1047 * EPOCH, 1052 * EPOCH), "clean 1055-1063": (1055 * EPOCH, 1064 * EPOCH)}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def refuse(why):
    print(f"creation_accounts: refused: {why}", file=sys.stderr)
    sys.exit(1)


def is_address(s):
    """A base58 string of 32 bytes (the recorder's watch.rs `valid`)."""
    if not isinstance(s, str) or not s or any(ch not in B58 for ch in s):
        return False
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    zeros = len(s) - len(s.lstrip("1"))
    return zeros + (n.bit_length() + 7) // 8 == 32


def chunks_for(root, lo, hi):
    """The complete chunks inside [lo, hi), refused unless they cover it without a gap."""
    out = []
    for m in glob.glob(os.path.join(root, "*", "_manifest.json")):
        with open(m) as f:
            man = json.load(f)
        a, b = man.get("slot_start"), man.get("slot_end_exclusive")
        if man.get("status") == "complete" and a is not None and b is not None and lo <= a and b <= hi:
            out.append((a, b, os.path.dirname(m), man.get("extractor_sha256")))
    out.sort()
    at = lo
    for a, b, d, _ in out:
        if a != at:
            refuse(f"no complete chunk covers [{at}, {a}) under {root}")
        at = b
    if at != hi:
        refuse(f"no complete chunk covers [{at}, {hi}) under {root}")
    return out


def accounts(ix, signers, fee_payer):
    out = set()
    for v in json.loads(ix).values():
        out.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str))
    out.update(x for x in (signers or "").split(",") if x)
    if fee_payer:
        out.add(fee_payer)
    return out


def build(root, lo, hi):
    """(sorted addresses, header lines)."""
    for name, (a, b) in CLOSED.items():
        if lo < b and a < hi:
            refuse(f"[{lo}, {hi}) touches the {name} period [{a}, {b})")
    chunks = chunks_for(root, lo, hi)
    con = duckdb.connect(config={"memory_limit": "1GB", "threads": 1})
    found, creations, skipped = set(), 0, 0
    for a, b, d, _ in chunks:
        files = sorted(glob.glob(os.path.join(d, "pump_amm", "CreatePoolEvent", "*.parquet")))
        if not files:
            continue
        cols = {c[0] for c in con.execute("SELECT * FROM read_parquet(?, union_by_name = true) LIMIT 0", [files]).description}
        if "ix_accounts" not in cols:
            refuse(f"{d}: CreatePoolEvent has no ix_accounts (an older extractor)")
        signers = "signers" if "signers" in cols else "NULL"
        rows = con.execute(f"SELECT ix_accounts, {signers}, fee_payer FROM read_parquet(?, union_by_name = true) "
                           "WHERE base_mint = ? AND slot >= ? AND slot < ?", [files, WSOL, a, b]).fetchall()
        for ix, sg, fp in rows:
            if ix is None:
                refuse(f"{d}: a reversed creation without ix_accounts")
            creations += 1
            for x in accounts(ix, sg, fp):
                if is_address(x):
                    found.add(x)
                else:
                    skipped += 1
    con.close()
    if not creations:
        refuse(f"no reversed creation in [{lo}, {hi})")
    shas = sorted({s for *_, s in chunks if s})
    header = [f"# creation_accounts.txt: rule (c) of lab/recorder/src/exclude.rs (creation-filters spec v0.2 P1)",
              f"# every account (ix_accounts, signers, fee_payer) of the reversed PumpSwap creations in slots [{lo}, {hi})",
              f"# chunks {len(chunks)}, reversed creations {creations}, accounts {len(found)}, non-address values skipped {skipped}",
              f"# extractor sha256: {', '.join(shas) or 'not in the manifests'}"]
    return sorted(found), header


def write(path, lines):
    """Atomic; the file's sha256."""
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chunks", required=True, help="the extracted chunks' root (one directory per chunk)")
    ap.add_argument("--from", dest="lo", type=int, required=True)
    ap.add_argument("--to", dest="hi", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.hi <= a.lo:
        refuse("--to must be above --from")
    found, header = build(a.chunks, a.lo, a.hi)
    sha = write(a.out, header + found)
    print(json.dumps({"out": os.path.abspath(a.out), "accounts": len(found), "sha256": sha, "header": header}, indent=1))


if __name__ == "__main__":
    main()
