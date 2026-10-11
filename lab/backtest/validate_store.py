"""Store rebuild validation: per-epoch counts before (old store) and after (new store).

  python -m backtest.validate_store snapshot <old.duckdb> <before.json>
  python -m backtest.validate_store check <new.duckdb> <before.json> <out.md>   # exit 1 on failure

check requires:
- the new store tiles its period with every epoch present (blocks, curve and pool rows, ms/slot);
- on the epochs of the old store: the same blocks per epoch, the same curve rows for mints created
  in the old period (the new store also has trades of mints created before it), and pool rows at
  least the old count and at most 2% more (pools first seen earlier can be classified now).
"""

import json
import sys

import duckdb

EPOCH = 432_000
POOL_TOLERANCE = 0.02


def _con(path):
    return duckdb.connect(path, read_only=True, config={"memory_limit": "1GB", "threads": 2})


def counts(path, mint_from=None):
    con = _con(path)
    meta = json.loads(con.execute("SELECT json FROM meta").fetchone()[0])
    out = {"meta": {k: meta[k] for k in ("slot_start", "slot_end_exclusive", "chunks", "counts", "ms_per_slot", "git_commit")}}
    out["blocks"] = dict(con.execute(f"SELECT (slot // {EPOCH})::VARCHAR, count(*) FROM blocks GROUP BY 1").fetchall())
    out["curve"] = dict(con.execute(f"SELECT (slot // {EPOCH})::VARCHAR, count(*) FROM curve GROUP BY 1").fetchall())
    mf = mint_from if mint_from is not None else meta["slot_start"]
    out["curve_old_mints"] = dict(con.execute(f"""SELECT (c.slot // {EPOCH})::VARCHAR, count(*) FROM curve c
                                                  JOIN mints m USING (mint) WHERE m.create_slot >= {mf} GROUP BY 1""").fetchall())
    out["pool"] = dict(con.execute(f"SELECT (slot // {EPOCH})::VARCHAR, count(*) FROM pool GROUP BY 1").fetchall())
    out["pool_classes"] = dict(con.execute("SELECT quote_class, count(*) FROM pools GROUP BY 1").fetchall())
    con.close()
    return out


def check(new_path, before):
    old_lo = before["meta"]["slot_start"]
    new = counts(new_path, mint_from=old_lo)
    m = new["meta"]
    lines, bad = [f"# Store validation: {new_path}", ""], []
    lo, hi = m["slot_start"], m["slot_end_exclusive"]
    epochs = [str(e) for e in range(lo // EPOCH, hi // EPOCH)]
    lines += [f"New store: slots {lo}–{hi}, {m['chunks']} chunks, built from {m['git_commit']}.",
              f"Old store: slots {old_lo}–{before['meta']['slot_end_exclusive']}, built from {before['meta']['git_commit']}.", "",
              "| epoch | ms/slot | blocks | curve rows | pool rows | old blocks | old curve | new curve (old-period mints) | old pool |",
              "|---|---|---|---|---|---|---|---|---|"]
    for e in epochs:
        ms = m["ms_per_slot"].get(e)
        b, c, p = new["blocks"].get(e, 0), new["curve"].get(e, 0), new["pool"].get(e, 0)
        if not (ms and b and c and p):
            bad.append(f"epoch {e}: missing data (ms/slot {ms}, blocks {b}, curve {c}, pool {p})")
        ob, oc, op = (before[k].get(e) for k in ("blocks", "curve", "pool"))
        nc = new["curve_old_mints"].get(e, 0)
        if ob is not None:
            if b != ob:
                bad.append(f"epoch {e}: blocks {b} != old {ob}")
            if nc != oc:
                bad.append(f"epoch {e}: curve rows of old-period mints {nc} != old {oc}")
            if not (op <= p <= op * (1 + POOL_TOLERANCE)):
                bad.append(f"epoch {e}: pool rows {p} outside [old {op}, +{POOL_TOLERANCE:.0%}]")
        lines.append(f"| {e} | {ms} | {b} | {c} | {p} | {'' if ob is None else ob} | {'' if oc is None else oc} | "
                     f"{nc if ob is not None else ''} | {'' if op is None else op} |")
    lines += ["", f"Pool classes: {new['pool_classes']} (old: {before['pool_classes']})", "",
              "Result: " + ("**PASS**" if not bad else "**FAIL**")] + [f"- {x}" for x in bad]
    return not bad, "\n".join(lines) + "\n"


def main():
    if sys.argv[1] == "snapshot":
        with open(sys.argv[3], "w") as f:
            json.dump(counts(sys.argv[2]), f, indent=1)
    elif sys.argv[1] == "check":
        with open(sys.argv[3]) as f:
            ok, text = check(sys.argv[2], json.load(f))
        with open(sys.argv[4], "w") as f:
            f.write(text)
        print(text)
        sys.exit(0 if ok else 1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
