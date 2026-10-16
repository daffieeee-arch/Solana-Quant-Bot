"""Alpenglow measurements M2-M7 of lab/reports/2026-10-16-alpenglow-meetnotitie.md, read-only, from the recorder's
segments (blocks/, block_footers/) or the keeper's kept copies of them, and the paper journal's F7 triggers (M2).
Nothing in the engine changes; the first week's before/after comparison comes from this script.

  python -m paper.alpenglow_metrics --recorder <root> [--kept <dir> ...] [--journal <dir>] [--switch-slot N]
                                    [--out report.md] [--json metrics.json]

Sources: every <root>/segments/<seq>/{blocks,block_footers}/*.parquet of a complete segment, plus <kept>/<seq>/<table>/
*.parquet or <kept>/<seq>/<table>.parquet (the keeper's layout; live-retention deletes segments after 3 days, so the
kept copies carry the before/after data). Rows are deduplicated, footers on (slot, bank_id) with their first receipt: a footer is not gated by
its slot's close, can sit in a later segment than its block, and comes again after a reconnect.

Sides: 'before' = slot < switch slot, 'after' = slot >= switch slot. The switch slot is --switch-slot, else the
recorder's <root>/alpenglow.json (first_footer_slot), else the first footer seen, else 459,653,000 (solana.com).

M2  per day and side: F7 triggers (each once) with latency_ms (receive - block_time, 1 s resolution) and
    producer_latency_ms (receive - the footer's block_producer_time_ns of slot t; after the switch only), medians,
    p90 and the share later than the simulated d_ms. A slot with several footers (two banks) takes the last
    received bank and is counted (multi_footer_slots): the blocks table carries no bank_id yet.
M3  per day and side: created - producer (Triton's server lag) and receive - created (network), median and p90.
M4  ms per slot per hour, per day and per epoch from blocks.block_time (seconds, >= 1,000 slots) and from the
    footers' producer time (nanoseconds).
M5  per day and side: blocks, skipped share, median and p90 executed_tx_count and entry_count.
M6  the switch: first footer slot, slot gaps > 25 between consecutive blocks, the segments' totals (late_rows,
    unconfirmed_slots) and reconnects per side.
M7  per day and side: footers, slots with more than one footer, footers with bank_id > 0.
"""

import argparse
import glob
import json
import os
import sys

import duckdb
import pyarrow as pa

SWITCH_SLOT = 459_653_000  # solana.com/upgrades/alpenglow: Alpenglow consensus takes over here (epoch 1064)
GAP_SLOTS = 25  # the engine's feed-gap heartbeat threshold
MIN_SPAN = 1_000  # slots for a ms-per-slot estimate (LiveFeed measures an epoch after 1,000 slots)
EPOCH_SLOTS = 432_000


def _files(recorder, kept, table):
    out = []
    if recorder:
        for m in glob.glob(os.path.join(recorder, "segments", "*", "_segment.json")):
            try:
                with open(m) as f:
                    complete = json.load(f).get("complete")
            except (OSError, ValueError):
                complete = False
            if complete:
                out += glob.glob(os.path.join(os.path.dirname(m), table, "*.parquet"))
    for k in kept or ():
        out += glob.glob(os.path.join(k, "*", table, "*.parquet")) + glob.glob(os.path.join(k, "*", f"{table}.parquet"))
    return sorted(set(out))


def _segment_totals(recorder, switch):
    """late_rows, unconfirmed_slots and reconnects of the complete segments, per side (by first_slot)."""
    out = {"before": {"segments": 0, "late_rows": 0, "unconfirmed_slots": 0, "reconnects": 0},
           "after": {"segments": 0, "late_rows": 0, "unconfirmed_slots": 0, "reconnects": 0}}
    if not recorder:
        return out
    for m in glob.glob(os.path.join(recorder, "segments", "*", "_segment.json")):
        try:
            with open(m) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if not meta.get("complete"):
            continue
        side = out["after" if (meta.get("first_slot") or 0) >= switch else "before"]
        t = meta.get("totals") or {}
        side["segments"] += 1
        side["late_rows"] += int(t.get("late_rows") or 0)
        side["unconfirmed_slots"] += int(t.get("unconfirmed_slots") or 0)
        side["reconnects"] += len(meta.get("reconnects") or [])
    return out


def _rows(con, sql):
    cur = con.execute(sql)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def compute(recorder=None, kept=(), journal=None, switch_slot=None):
    con = duckdb.connect()
    con.execute("SET memory_limit='512MB'")
    blocks, footers = _files(recorder, kept, "blocks"), _files(recorder, kept, "block_footers")
    con.execute(f"CREATE TABLE b AS SELECT DISTINCT * FROM read_parquet({blocks!r}, union_by_name = true)" if blocks else
                "CREATE TABLE b (slot BIGINT, parent_slot BIGINT, block_time BIGINT, block_height BIGINT, executed_tx_count BIGINT, "
                "entry_count BIGINT, skipped BOOLEAN)")
    con.execute(f"CREATE TABLE f AS SELECT DISTINCT * FROM read_parquet({footers!r}, union_by_name = true)" if footers else
                "CREATE TABLE f (slot BIGINT, bank_id BIGINT, block_producer_time_ns BIGINT, received_unix_ns BIGINT, created_unix_ns BIGINT)")
    note = None
    if recorder and os.path.exists(os.path.join(recorder, "alpenglow.json")):
        with open(os.path.join(recorder, "alpenglow.json")) as fh:
            note = json.load(fh)
    first_footer = con.execute("SELECT min(slot) FROM f").fetchone()[0]
    switch = switch_slot or (note or {}).get("first_footer_slot") or first_footer or SWITCH_SLOT
    side = f"CASE WHEN slot >= {switch} THEN 'after' ELSE 'before' END"
    out = {"switch_slot": int(switch), "switch_source": ("argument" if switch_slot else "alpenglow.json" if note else
                                                         "first footer" if first_footer else "default"),
           "first_footer": note or ({"first_footer_slot": first_footer} if first_footer else None),
           "sources": {"blocks": len(blocks), "block_footers": len(footers)}}
    # one row per (slot, bank_id): a reconnect can deliver a footer again (LAB review), the first receipt counts; then one
    # footer per slot: the last received bank (two banks per slot: the confirmed one is not identifiable without bank_id in blocks)
    con.execute("""CREATE TABLE fb AS SELECT slot, bank_id, arg_min(block_producer_time_ns, received_unix_ns) AS producer_ns, min(received_unix_ns) AS received_ns,
                     arg_min(created_unix_ns, received_unix_ns) AS created_ns FROM f GROUP BY slot, bank_id""")
    con.execute("""CREATE TABLE f1 AS SELECT slot, arg_max(bank_id, received_ns) AS bank_id, arg_max(producer_ns, received_ns) AS producer_ns,
                     max(received_ns) AS received_ns, arg_max(created_ns, received_ns) AS created_ns, count(*) AS n_footers
                   FROM fb GROUP BY slot""")
    day_b = "strftime(to_timestamp(block_time), '%Y-%m-%d')"
    day_f = "strftime(to_timestamp(producer_ns // 1000000000), '%Y-%m-%d')"
    out["m5_blocks"] = _rows(con, f"""SELECT {day_b} AS day, {side} AS side, count(*) AS blocks, round(avg(COALESCE(skipped, false)::INT), 4) AS skipped_share,
        median(executed_tx_count) FILTER (WHERE NOT COALESCE(skipped, false)) AS tx_median, quantile_cont(executed_tx_count, 0.9) FILTER (WHERE NOT COALESCE(skipped, false)) AS tx_p90,
        median(entry_count) FILTER (WHERE NOT COALESCE(skipped, false)) AS entries_median
        FROM b WHERE block_time IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""")
    out["m3_footers"] = _rows(con, f"""SELECT {day_f} AS day, {side} AS side, count(*) AS footers,
        median((created_ns - producer_ns) / 1e6) AS server_lag_ms_median, quantile_cont((created_ns - producer_ns) / 1e6, 0.9) AS server_lag_ms_p90,
        median((received_ns - created_ns) / 1e6) AS network_ms_median, quantile_cont((received_ns - created_ns) / 1e6, 0.9) AS network_ms_p90,
        median((received_ns - producer_ns) / 1e6) AS receive_ms_median, quantile_cont((received_ns - producer_ns) / 1e6, 0.9) AS receive_ms_p90
        FROM f1 WHERE producer_ns > 0 GROUP BY 1, 2 ORDER BY 1, 2""")
    out["m7_banks"] = _rows(con, f"""SELECT {day_f} AS day, {side} AS side, sum(n_footers) AS footers, count(*) FILTER (WHERE n_footers > 1) AS multi_footer_slots,
        count(*) FILTER (WHERE bank_id > 0) AS bank_id_gt0 FROM f1 GROUP BY 1, 2 ORDER BY 1, 2""")
    msb = "(max(block_time) - min(block_time)) * 1000.0 / (max(slot) - min(slot))"
    msf = "(max(producer_ns) - min(producer_ns)) / 1e6 / (max(slot) - min(slot))"
    out["m4_hour"] = _rows(con, f"""SELECT hour, any_value(side) AS side, max(n) AS n_slots, max(ms_blocks) AS ms_blocks, max(ms_footers) AS ms_footers FROM (
        SELECT strftime(to_timestamp(block_time), '%Y-%m-%d %H') AS hour, {side} AS side, count(*) AS n,
               CASE WHEN max(slot) - min(slot) >= {MIN_SPAN} THEN round({msb}, 1) END AS ms_blocks, NULL::DOUBLE AS ms_footers
        FROM b WHERE block_time IS NOT NULL GROUP BY 1, 2
        UNION ALL
        SELECT strftime(to_timestamp(producer_ns // 1000000000), '%Y-%m-%d %H'), {side}, count(*), NULL,
               CASE WHEN max(slot) - min(slot) >= {MIN_SPAN} THEN round({msf}, 1) END
        FROM f1 WHERE producer_ns > 0 GROUP BY 1, 2) GROUP BY 1 ORDER BY 1""")
    out["m4_day"] = _rows(con, f"""SELECT {day_b} AS day, {side} AS side, count(*) AS n_slots, min(slot) AS first_slot, max(slot) AS last_slot,
        CASE WHEN max(slot) - min(slot) >= {MIN_SPAN} THEN round({msb}, 1) END AS ms_blocks
        FROM b WHERE block_time IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""")
    out["m4_epoch"] = _rows(con, f"""SELECT slot // {EPOCH_SLOTS} AS epoch, count(*) AS n_slots,
        CASE WHEN max(slot) - min(slot) >= {MIN_SPAN} THEN round({msb}, 1) END AS ms_blocks
        FROM b WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1""")
    out["m4_epoch_footers"] = _rows(con, f"""SELECT slot // {EPOCH_SLOTS} AS epoch, count(*) AS n_slots,
        CASE WHEN max(slot) - min(slot) >= {MIN_SPAN} THEN round({msf}, 1) END AS ms_footers FROM f1 WHERE producer_ns > 0 GROUP BY 1 ORDER BY 1""")
    gaps = _rows(con, f"""SELECT prev AS slot_before, slot AS slot_after, slot - prev AS gap FROM (SELECT slot, lag(slot) OVER (ORDER BY slot) AS prev FROM b)
        WHERE slot - prev > {GAP_SLOTS} ORDER BY 1 LIMIT 50""")
    out["m6_switch"] = {"gaps_over_25_slots": gaps, "segments": _segment_totals(recorder, switch)}
    out["m2_triggers"] = []
    if journal and os.path.exists(os.path.join(journal, "journal.sqlite")):
        import sqlite3

        db = sqlite3.connect(f"file:{os.path.join(journal, 'journal.sqlite')}?mode=ro", uri=True)
        cols = ("key", "t", "day", "block_time", "received_unix_ns", "latency_ms", "segment_lag_ms", "d_ms")
        seen = {}
        for r in db.execute(f"SELECT {', '.join(cols)} FROM signals WHERE family = 'F7' AND block_time IS NOT NULL ORDER BY key, t"):
            seen.setdefault((r[0], r[1]), r)  # a trigger once: its variants and N2 signals share the arrival
        db.close()
        sig = pa.table({c: [r[i] for r in seen.values()] for i, c in enumerate(cols)},
                       schema=pa.schema([(c, pa.string() if c in ("key", "day") else pa.int64()) for c in cols]))
        con.register("s", sig)
        out["m2_triggers"] = _rows(con, f"""SELECT s.day, CASE WHEN s.t >= {switch} THEN 'after' ELSE 'before' END AS side, count(*) AS triggers,
            median(s.d_ms) AS d_ms_median, median(s.latency_ms) AS latency_ms_median, quantile_cont(s.latency_ms, 0.9) AS latency_ms_p90,
            round(avg((s.latency_ms > s.d_ms)::INT), 3) AS later_than_d_share, median(s.segment_lag_ms) AS segment_lag_ms_median,
            count(f1.slot) AS with_footer, median((s.received_unix_ns - f1.producer_ns) / 1e6) AS producer_latency_ms_median,
            quantile_cont((s.received_unix_ns - f1.producer_ns) / 1e6, 0.9) AS producer_latency_ms_p90,
            round(avg(((s.received_unix_ns - f1.producer_ns) / 1e6 > s.d_ms)::INT), 3) AS producer_later_than_d_share,
            count(*) FILTER (WHERE f1.n_footers > 1) AS multi_footer_slots
            FROM s LEFT JOIN f1 ON f1.slot = s.t AND f1.producer_ns > 0 GROUP BY 1, 2 ORDER BY 1, 2""")
    return out


def _table(rows, cols):
    if not rows:
        return ["- geen rijen"]
    fmt = lambda v: "" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))  # noqa: E731
    return ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(fmt(r.get(c)) for c in cols) + " |" for r in rows]


def render(m):
    L = ["# Alpenglow-metingen M2–M7", "", f"- switch-slot {m['switch_slot']} ({m['switch_source']}); eerste footer: {json.dumps(m['first_footer'])}",
         f"- bronnen: {m['sources']['blocks']} blocks-bestanden, {m['sources']['block_footers']} footer-bestanden", "",
         "## M2 F7-triggers: latency tegen bloktijd en tegen producenttijd (journal)", ""]
    L += _table(m["m2_triggers"], ["day", "side", "triggers", "d_ms_median", "latency_ms_median", "latency_ms_p90", "later_than_d_share",
                                   "segment_lag_ms_median", "with_footer", "producer_latency_ms_median", "producer_latency_ms_p90",
                                   "producer_later_than_d_share", "multi_footer_slots"])
    L += ["", "## M3 footers: serverlag (created − producent) en netwerk (ontvangst − created), ms", ""]
    L += _table(m["m3_footers"], ["day", "side", "footers", "server_lag_ms_median", "server_lag_ms_p90", "network_ms_median", "network_ms_p90",
                                  "receive_ms_median", "receive_ms_p90"])
    L += ["", "## M4 ms per slot", "", "Per epoch (blocks.block_time):", ""] + _table(m["m4_epoch"], ["epoch", "n_slots", "ms_blocks"])
    L += ["", "Per epoch (footers, producenttijd):", ""] + _table(m["m4_epoch_footers"], ["epoch", "n_slots", "ms_footers"])
    L += ["", "Per dag:", ""] + _table(m["m4_day"], ["day", "side", "n_slots", "first_slot", "last_slot", "ms_blocks"])
    L += ["", "Per uur:", ""] + _table(m["m4_hour"], ["hour", "side", "n_slots", "ms_blocks", "ms_footers"])
    L += ["", "## M5 blokken: skip-aandeel en transacties per blok", ""]
    L += _table(m["m5_blocks"], ["day", "side", "blocks", "skipped_share", "tx_median", "tx_p90", "entries_median"])
    s = m["m6_switch"]
    L += ["", "## M6 de switch", "", f"- slotsprongen > {GAP_SLOTS}: {len(s['gaps_over_25_slots'])}"]
    L += [f"  - {g['slot_before']} → {g['slot_after']} ({g['gap']} slots)" for g in s["gaps_over_25_slots"][:10]]
    L += [f"- segmenten {k}: {v['segments']}, late_rows {v['late_rows']}, unconfirmed_slots {v['unconfirmed_slots']}, reconnects {v['reconnects']}"
          for k, v in s["segments"].items()]
    L += ["", "## M7 banken per slot", ""] + _table(m["m7_banks"], ["day", "side", "footers", "multi_footer_slots", "bank_id_gt0"])
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--recorder")
    ap.add_argument("--kept", action="append", default=[])
    ap.add_argument("--journal")
    ap.add_argument("--switch-slot", type=int)
    ap.add_argument("--out")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    if not a.recorder and not a.kept:
        ap.error("--recorder or --kept is required")
    m = compute(a.recorder, a.kept, a.journal, a.switch_slot)
    text = render(m)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(m, f, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
