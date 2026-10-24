"""Event-level diff of two extractor chunk directories, read-only (pipeline diagnostic (i), research/notes/
pipeline-diagnostics-map-2026-10-24.json part "extract"): is a chunk the same when another extractor build or thread
count writes it? Diagnostics only: no clean verdict changes on its result and nothing is re-tuned.

  python -m checks.chunk_diff --old <chunk dir> --new <chunk dir> --out DIR [options]
  python -m checks.chunk_diff --old-root R --new-root R --chunks S-T[,S-T ...] --out DIR [options]
  python -m checks.chunk_diff footprint <chunk dir> [...]      # JSON on stdout, the input of --old-footprint

A table is a directory of *.parquet below the chunk (blocks, failed_txs, pump/<Event>, pump_amm/<Event>, ...).
  both sides     row multisets on the common columns, joined on the table's key (KEYS; event tables
                 (slot, tx_index, outer_ix, inner_ix), the table naming the discriminator, event_seq a column):
                 only_old, only_new, and matched rows identical / differing only as the --normalize rules explain
                 ('expected') / differing ('differing', unexpected), with per-column counts and samples. Tables
                 without a key (token_deltas, token_transfers, sol_funding) are compared as multisets of rows.
                 Each common column is cast to one comparison type (both integer: HUGEINT, else the shared type,
                 else VARCHAR). A different parquet physical type or DuckDB type is reported apart as type drift
                 and is unexpected; writer annotations are not drift (DuckDB's strip-failed-signatures rewrite of
                 the dev failed_txs writes UINT_64 without arrow's logical type).
  one side only  NEW_TABLES on the new side (written only by builds after the dev ones: 'new after the dev builds')
                 are listed with their row counts and ignored; any other table only one side has is unexpected (the IDLs are identical).
  columns        only in new: listed and ignored (the six #178 ix_* columns; parent_ix_* and signers against
                 3b8b45ff). Only in old: unexpected, unless EXPECTED_REMOVED.
  duplicates     per side, on the key: identical rows (a firehose restart re-delivering a slot) and conflicting
                 rows (one key, different contents), reported apart and unexpected on either side. The comparison
                 then runs on the de-duplicated rows without the conflicting keys. The clean store does not
                 de-duplicate (data.parquet dedup=False), so a duplicate in a new output is a real finding.
--normalize fca8c80: the old side predates fca8c80 (0cfc4db2 = b10c5c8: dev 1041-1043). The rules come from that
  commit's extract.rs (fca8c80_rules) and touch the old side only: a cell they explain is 'expected', a cell they
  do not explain is unexpected (also a new value the rule would have nulled). Without the flag nothing is
  normalised; on an old side already written with fca8c80 semantics the rules change no value.
Manifests: status complete, slot range = the directory name, coverage.missing_slots 0, the SAME_COUNTERS both
  sides have equal, idl_sha256 equal, a blocks table on each side (every extractor chunk writes one: a chunk without
  it compares nothing), per side sum(blocks.executed_tx_count) = counters.txs_seen (the new side must have the
  counter), manifest rows = parquet rows per table. --expect-new / --expect-old FIELD=VALUE assert manifest
  fields (dotted paths; VALUE compared as text or as JSON). --old-footprint FILE: the old chunk must match a
  footprint taken elsewhere (the VPS copy dev2 was built from).
Refusals (exit 2, nothing written): a path containing "clean", "holdout" or "hold-out" (as given or resolved), an
  output or spill path under events/ or store/, an existing --out, an empty or repeated --chunks entry, an old (or
  --extra) chunk that resolves to the new chunk or lies inside it or around it (a chunk compared with itself is
  trivially equal), a chunk name or manifest reaching slot
  452,304,000 (the hold-out start), a file whose slot statistics reach it (files without statistics: a scan of the
  slot column first). Rows outside the chunk's own range are unexpected.
Memory: one DuckDB connection (--memory, --threads; spill in a new directory under --spill, capped by --spill-max
  and removed at the end). Each side of a table is read once into a narrow projection (key, bucket, row hashes) in
  the spill directory; the join runs per bucket (hash of the slot mod --buckets); full rows are read again only for
  the keys whose normalised row hashes differ (64-bit hashes: a collision hiding a differing row has a chance of about
  1e-12 in a 30M-row table).
Output (--out, must not exist): summary.json, report.md, diffs/<chunk>/<table>.<category>.parquet (at most --samples
  rows each), footprints.json, SHA256SUMS. Exit 0 = GREEN (no unexpected finding anywhere), 1 = RED, 2 = refused,
  3 = failed: any other exception (DuckDB out of memory or over --spill-max, an unreadable parquet file) is a tool or
  resource failure, never a data finding; it writes no summary.json and its last stderr line starts with FAILED.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

import duckdb

HOLDOUT_START = 452_304_000  # epoch 1047: the hold-out and everything after it is never read here
FORBIDDEN = ("clean", "holdout", "hold-out")
POS = ("slot", "tx_index", "outer_ix", "inner_ix")
KEYS = {"blocks": ("slot",), "failed_txs": ("slot", "tx_index"), "tx_ctx": ("slot", "tx_index"),
        "anomalies": POS, "token_meta": POS}
# Written only by builds after the dev ones (tx_ctx from 107f6c5, token_meta, the LAB_TRANSFERS tables from 5a97187).
NEW_TABLES = {"tx_ctx", "token_meta", "token_deltas", "token_transfers", "sol_funding"}
EXPECTED_REMOVED = {"failed_txs": {"signature": "dropped at fca8c80; older chunks rewritten by strip-failed-signatures.py"}}
SAME_COUNTERS = ("blocks", "skipped_markers", "txs_seen", "txs_touching", "txs_failed_touching", "events", "anomalies",
                 "missing_meta", "sink_errors")
INFO_COUNTERS = ("firehose_errors", "http_429")  # OF1 retries: they depend on the run, not on the data
NORMALIZE = ("none", "fca8c80")
INTS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}
FLOATS = {"FLOAT", "DOUBLE"}
HIST_SLOTS = 8000  # only_old / only_new per 8,000 slots (27 bins in a 216,000-slot chunk)


class Refused(Exception):
    pass


def q(name):
    return '"' + name.replace('"', '""') + '"'


def lit(files):
    return "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"


def refuse_path(path, output=False):
    """Refuse a clean or hold-out path (as given and resolved); an output also under events/ or store/."""
    for p in (path, os.path.realpath(path)):
        if any(w in p.lower() for w in FORBIDDEN):
            raise Refused(f"{path}: a clean or hold-out path")
        if output and any(seg in ("events", "store") for seg in p.split(os.sep)):
            raise Refused(f"{path}: an output under events/ or store/")


def nested(a, b):
    """True when a and b resolve to the same directory or one lies inside the other."""
    ra, rb = os.path.realpath(a).rstrip(os.sep) + os.sep, os.path.realpath(b).rstrip(os.sep) + os.sep
    return ra.startswith(rb) or rb.startswith(ra)


def chunk_range(name):
    m = re.fullmatch(r"(\d+)-(\d+)", name)
    if not m or int(m[1]) >= int(m[2]):
        raise Refused(f"{name!r} is no chunk name S-T")
    if int(m[2]) > HOLDOUT_START:
        raise Refused(f"chunk {name} reaches slot {HOLDOUT_START:,} (the hold-out)")
    return int(m[1]), int(m[2])


def load_manifest(chunk):
    p = os.path.join(chunk, "_manifest.json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        m = json.load(f)
    if max(int(m.get("slot_end_exclusive") or 0), int(m.get("slot_start") or 0) + 1) > HOLDOUT_START:
        raise Refused(f"{p}: the manifest reaches slot {HOLDOUT_START:,} (the hold-out)")
    return m


def tables(chunk):
    """Table name -> its parquet files: every directory below the chunk holding *.parquet (hidden files aside)."""
    out = {}
    for root, dirs, files in os.walk(chunk):
        dirs.sort()
        fs = sorted(os.path.join(root, f) for f in files if f.endswith(".parquet") and not f.startswith("."))
        if fs:
            out[os.path.relpath(root, chunk)] = fs
    return out


def key_of(table):
    return POS if table.startswith(("pump/", "pump_amm/")) else KEYS.get(table)


def fca8c80_rules(table, old_cols):
    """{column: SQL over the old row} for the values fca8c80 changed, from its extract.rs: `signers` is Val::Null
    unless sigs > 1 (more than one key: a comma); ix_raw is Val::Null when the decoded args are a string containing
    "_decode":"ok" or "_decode":"prefix", else the capped hex as before (null args keep the hex). Event tables get
    parent_ix_data by parent_ix_args, failed_txs ix_data by ix_args."""
    if not (table == "failed_txs" or table.startswith(("pump/", "pump_amm/"))):
        return {}
    raw, args = ("ix_data", "ix_args") if table == "failed_txs" else ("parent_ix_data", "parent_ix_args")
    rules = {"signers": f"CASE WHEN contains({q('signers')}, ',') THEN {q('signers')} END"}
    if args in old_cols:
        exact = f"""contains({q(args)}, '"_decode":"ok"') OR contains({q(args)}, '"_decode":"prefix"')"""
        rules[raw] = f"CASE WHEN {exact} THEN NULL ELSE {q(raw)} END"
    return {c: e for c, e in rules.items() if c in old_cols}


def footprint(chunk):
    """{relative path: [size, sha256]} of every file in the chunk; for parquet files the sha256 of the footer only
    (it holds every row group's offsets, sizes, row counts and column statistics), so a copy is matched without
    reading its data. Other files up to 64 MB are hashed whole."""
    out = {}
    for root, dirs, files in os.walk(chunk):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            size = os.path.getsize(p)
            with open(p, "rb") as fh:
                if f.endswith(".parquet"):
                    digest = "bad-footer"
                    if size >= 12:
                        fh.seek(size - 8)
                        tail = fh.read(8)
                        n = int.from_bytes(tail[:4], "little")
                        if tail[4:] == b"PAR1" and n + 12 <= size:
                            fh.seek(size - 8 - n)
                            digest = hashlib.sha256(fh.read(n)).hexdigest()
                else:
                    digest = hashlib.sha256(fh.read()).hexdigest() if size <= 64 << 20 else "large"
            out[os.path.relpath(p, chunk)] = [size, digest]
    return out


def schema(con, files):
    """{column: (DuckDB type, parquet physical types)} in file order."""
    duck = con.execute(f"DESCRIBE SELECT * FROM read_parquet({lit(files)}, union_by_name = true)").fetchall()
    phys = {}
    for name, typ in con.execute(f"SELECT name, type FROM parquet_schema({lit(files)}) WHERE type IS NOT NULL").fetchall():
        phys.setdefault(name, set()).add(typ)
    return {r[0]: (r[1], tuple(sorted(phys.get(r[0], ())))) for r in duck}


def cmp_type(a, b):
    if a == b:
        return a
    if a in INTS and b in INTS:
        return "HUGEINT"
    if a in FLOATS and b in FLOATS:
        return "DOUBLE"
    return "VARCHAR"


def slot_max(con, files):
    """The highest slot in the files: row group statistics, or a scan of the slot column where a file has none."""
    rows = con.execute(f"SELECT file_name, stats_max_value FROM parquet_metadata({lit(files)}) WHERE path_in_schema = 'slot'").fetchall()
    best = [int(v) for _, v in rows if v is not None]
    bare = sorted({f for f, v in rows if v is None} | (set(files) - {f for f, _ in rows}))
    if bare:
        best.append(con.execute(f"SELECT max(slot) FROM read_parquet({lit(bare)}, union_by_name = true)").fetchone()[0] or 0)
    return max(best) if best else None


def expectations(label, m, wanted):
    bad = []
    for item in wanted:
        field, _, want = item.partition("=")
        got = m or {}
        for part in field.split("."):
            got = got.get(part, "<missing>") if isinstance(got, dict) else "<missing>"
        if not (str(got) == want or json.dumps(got) == want):
            bad.append(f"{label} manifest {field} = {json.dumps(got)}, expected {want}")
    return bad


class Diff:
    """One DuckDB connection, the spill directory and the samples of a run."""

    def __init__(self, con, spill, buckets, samples, normalize, out):
        self.con, self.spill, self.buckets, self.samples, self.normalize, self.out = con, spill, buckets, samples, normalize, out
        self.have, self.names = {}, {}  # samples kept per (chunk, table, category), and their temp tables

    def x(self, sql):
        return self.con.execute(sql)

    def one(self, sql):
        return self.con.execute(sql).fetchone()

    def table(self, chunk, rng, table, old_files, new_files):
        s, t = rng
        key = key_of(table)
        so, sn = schema(self.con, old_files), schema(self.con, new_files)
        common = [c for c in so if c in sn]
        removed_ok = EXPECTED_REMOVED.get(table, {})
        r = {"key": list(key) if key else None, "findings": [],
             "columns": {"common": len(common), "new_only_ignored": [c for c in sn if c not in so],
                         "old_only_expected": {c: removed_ok[c] for c in so if c not in sn and c in removed_ok}}}
        r["findings"] += [f"column {c} only in old" for c in so if c not in sn and c not in removed_ok]
        r["type_drift"] = [{"column": c, "old": list(so[c]), "new": list(sn[c])} for c in common if so[c] != sn[c]]
        r["findings"] += [f"type drift {d['column']}: old {d['old']} new {d['new']}" for d in r["type_drift"]]
        if "slot" not in common or (key and not set(key) <= set(common)):
            r["findings"].append(f"key {list(key or ('slot',))} not in both schemas")
            r["unexpected"] = len(r["findings"])
            return r
        typ = {c: cmp_type(so[c][0], sn[c][0]) for c in common}
        rules = fca8c80_rules(table, so) if self.normalize == "fca8c80" else {}
        rules = {c: e for c, e in rules.items() if c in common}
        r["normalised"] = sorted(rules)
        cast = lambda e, c: f"CAST(({e}) AS {typ[c]})"  # noqa: E731
        kproj = ", ".join(f"{cast(q(c), c)} AS {q(c)}" for c in (key or ("slot",)))
        bucket = f"hash(CAST({q('slot')} AS UBIGINT)) % {self.buckets} AS b"
        flags = "".join(f", (({rules[c]}) IS DISTINCT FROM {q(c)}) AS {q('x_' + c)}" for c in sorted(rules))
        po, pn = os.path.join(self.spill, "old.parquet"), os.path.join(self.spill, "new.parquet")
        for p, files, cols, h, fl in ((po, old_files, so, [cast(rules.get(c, q(c)), c) for c in common], flags),
                                      (pn, new_files, sn, [cast(q(c), c) for c in common], "")):
            self.x(f"""COPY (SELECT {kproj}, {bucket}, hash({', '.join(h)}) AS h, hash({', '.join(q(c) for c in cols)}) AS hf{fl}
                          FROM read_parquet({lit(files)}, union_by_name = true)) TO '{p}' (FORMAT parquet)""")
        r["rows"], r["fingerprint"], r["slot_range"], r["outside_range"] = {}, {}, {}, {}
        for side, p in (("old", po), ("new", pn)):
            n, hs, hx, lo, hi, outside = self.one(f"""SELECT count(*), sum(h::HUGEINT), bit_xor(h), min(slot), max(slot),
                count(*) FILTER (WHERE slot < {s} OR slot >= {t}) FROM read_parquet('{p}')""")
            if hi is not None and hi >= HOLDOUT_START:  # unreachable after run()'s guard; kept as the last line of defence
                raise Refused(f"{chunk} {table}: a {side} row at slot {hi:,} (the hold-out)")
            r["rows"][side], r["fingerprint"][side], r["slot_range"][side], r["outside_range"][side] = n, [n, str(hs), hx], [lo, hi], outside
        if "decode_status" in common:
            r["decode_status"] = {side: dict(self.x(f"""SELECT decode_status, count(*) FROM read_parquet({lit(fs)}, union_by_name = true)
                                                       GROUP BY 1 ORDER BY 1""").fetchall())
                                  for side, fs in (("old", old_files), ("new", new_files))}
        if key:
            self.keyed(chunk, table, r, key, common, typ, rules, old_files, new_files, s)
        else:
            self.keyless(r)
        r["unexpected"] = len(r["findings"]) + sum(r["outside_range"].values()) + r["only_old"] + r["only_new"] + r.get("differing", 0) + \
            sum(d["identical_rows"] + d["conflict_rows"] for d in r.get("duplicates", {}).values())
        os.remove(po), os.remove(pn)
        return r

    def keyless(self, r):
        acc = {"only_old": 0, "only_new": 0, "matched": 0}
        for k in range(self.buckets):
            res = self.one(f"""
                WITH o AS (SELECT h, count(*) AS n FROM read_parquet('{self.spill}/old.parquet') WHERE b = {k} GROUP BY h),
                     n AS (SELECT h, count(*) AS n FROM read_parquet('{self.spill}/new.parquet') WHERE b = {k} GROUP BY h)
                SELECT coalesce(sum(greatest(coalesce(o.n, 0) - coalesce(n.n, 0), 0)), 0),
                       coalesce(sum(greatest(coalesce(n.n, 0) - coalesce(o.n, 0), 0)), 0),
                       coalesce(sum(least(coalesce(o.n, 0), coalesce(n.n, 0))), 0)
                FROM o FULL OUTER JOIN n USING (h)""")
            for name, v in zip(acc, res):
                acc[name] += int(v)
        r.update(acc)
        # no key: identical rows can be legitimate, so they are informative here
        r["identical_rows_info"] = {side: self.one(f"SELECT count(*) - count(DISTINCT hf) FROM read_parquet('{self.spill}/{side}.parquet')")[0]
                                    for side in ("old", "new")}

    def keyed(self, chunk, table, r, key, common, typ, rules, old_files, new_files, s):
        K = ", ".join(q(c) for c in key)
        flags = [f"x_{c}" for c in sorted(rules)]
        dup = {side: {"keys": 0, "identical_rows": 0, "conflict_keys": 0, "conflict_rows": 0} for side in ("old", "new")}
        acc = {"only_old": 0, "only_new": 0, "identical": 0, "expected": 0, "differing": 0}
        expected_cells = {c: 0 for c in rules}
        coldiff = {c: {"raw": 0, "unexpected": 0} for c in common}
        hist = {}
        for k in range(self.buckets):
            for tag, side in (("o", "old"), ("n", "new")):
                self.x(f"CREATE OR REPLACE TEMP TABLE {tag} AS SELECT * EXCLUDE (b) FROM read_parquet('{self.spill}/{side}.parquet') WHERE b = {k}")
                self.x(f"""CREATE OR REPLACE TEMP TABLE dup_{tag} AS SELECT {K}, count(*) AS n, count(DISTINCT hf) AS nd
                           FROM {tag} GROUP BY {K} HAVING count(*) > 1""")
                res = self.one(f"""SELECT count(*), coalesce(sum(n - nd), 0), count(*) FILTER (WHERE nd > 1),
                                          coalesce(sum(n) FILTER (WHERE nd > 1), 0) FROM dup_{tag}""")
                for name, v in zip(dup[side], res):
                    dup[side][name] += int(v)
                if res[0]:
                    self.sample(chunk, table, f"duplicates_{side}", f"SELECT {K}, n AS rows, nd AS distinct_rows FROM dup_{tag} ORDER BY {K}")
            self.x(f"CREATE OR REPLACE TEMP TABLE conflict AS SELECT {K} FROM dup_o WHERE nd > 1 UNION SELECT {K} FROM dup_n WHERE nd > 1")
            for tag in ("o", "n"):
                fl = "".join(f", any_value({q(f)}) AS {q(f)}" for f in flags) if tag == "o" else ""
                self.x(f"""CREATE OR REPLACE TEMP TABLE u{tag} AS SELECT {K}, any_value(h) AS h{fl}
                           FROM {tag} ANTI JOIN conflict USING ({K}) GROUP BY {K}""")
            self.x(f"""CREATE OR REPLACE TEMP TABLE j AS SELECT {K}, uo.h AS ho, un.h AS hn{''.join(f', uo.{q(f)}' for f in flags)}
                       FROM uo FULL OUTER JOIN un USING ({K})""")
            anyflag = " OR ".join(q(f) for f in flags) or "false"
            sums = "".join(f", coalesce(sum({q(f)}::INT) FILTER (WHERE ho = hn), 0)" for f in flags)
            res = self.one(f"""SELECT count(*) FILTER (WHERE hn IS NULL), count(*) FILTER (WHERE ho IS NULL),
                                      count(*) FILTER (WHERE ho = hn AND NOT ({anyflag})), count(*) FILTER (WHERE ho = hn AND ({anyflag})),
                                      count(*) FILTER (WHERE ho <> hn){sums} FROM j""")
            for name, v in zip(acc, res[:5]):
                acc[name] += int(v)
            for c, v in zip(sorted(rules), res[5:]):
                expected_cells[c] += int(v)
            if res[0] or res[1]:
                for bin_, oo, on in self.x(f"""SELECT (CAST(slot AS BIGINT) - {s}) // {HIST_SLOTS}, count(*) FILTER (WHERE hn IS NULL),
                                                    count(*) FILTER (WHERE ho IS NULL) FROM j WHERE ho IS NULL OR hn IS NULL GROUP BY 1""").fetchall():
                    h = hist.setdefault(int(bin_), [0, 0])
                    h[0], h[1] = h[0] + oo, h[1] + on
                self.sample(chunk, table, "only_old", f"SELECT {K} FROM j WHERE hn IS NULL ORDER BY {K}")
                self.sample(chunk, table, "only_new", f"SELECT {K} FROM j WHERE ho IS NULL ORDER BY {K}")
            if res[4]:
                self.mismatches(chunk, table, key, common, typ, rules, old_files, new_files, int(res[4]), expected_cells, coldiff)
        r.update(acc)
        r["duplicates"] = dup
        r["expected_cells"] = expected_cells
        r["column_diffs"] = {c: v for c, v in coldiff.items() if v["raw"] or v["unexpected"]}
        r["only_by_slot"] = {str(s + b * HIST_SLOTS): v for b, v in sorted(hist.items())}

    def mismatches(self, chunk, table, key, common, typ, rules, old_files, new_files, n, expected_cells, coldiff):
        """Matched keys whose normalised row hashes differ: both full rows again, per-column counts and samples."""
        K = ", ".join(q(c) for c in key)
        self.x(f"CREATE OR REPLACE TEMP TABLE m AS SELECT {K} FROM j WHERE ho <> hn")
        kc = ", ".join(f"CAST({q(c)} AS {typ[c]}) AS {q(c)}" for c in key)
        i = {c: n_ for n_, c in enumerate(common)}
        ocols = ", ".join(f"CAST({q(c)} AS {typ[c]}) AS o{i[c]}" + (f", CAST(({rules[c]}) AS {typ[c]}) AS z{i[c]}" if c in rules else "")
                          for c in common)
        ncols = ", ".join(f"CAST({q(c)} AS {typ[c]}) AS n{i[c]}" for c in common)
        for tag, cols, files in (("mo", ocols, old_files), ("mn", ncols, new_files)):
            self.x(f"""CREATE OR REPLACE TEMP TABLE {tag} AS SELECT DISTINCT ON ({K}) * FROM
                       (SELECT {kc}, {cols} FROM read_parquet({lit(files)}, union_by_name = true)) SEMI JOIN m USING ({K})""")
        z = {c: f"z{i[c]}" if c in rules else f"o{i[c]}" for c in common}
        aggs = ", ".join(f"count(*) FILTER (WHERE o{i[c]} IS DISTINCT FROM n{i[c]}), count(*) FILTER (WHERE {z[c]} IS DISTINCT FROM n{i[c]})"
                         for c in common)
        res = self.one(f"SELECT count(*), {aggs} FROM mo JOIN mn USING ({K})")
        if res[0] != n:
            raise RuntimeError(f"{chunk} {table}: {n} differing keys, {res[0]} found again in the parquet files")
        for c in common:
            coldiff[c]["raw"] += res[1 + 2 * i[c]]
            coldiff[c]["unexpected"] += res[2 + 2 * i[c]]
            if c in rules:
                expected_cells[c] += self.one(f"""SELECT count(*) FROM mo JOIN mn USING ({K})
                                                  WHERE o{i[c]} IS DISTINCT FROM n{i[c]} AND z{i[c]} IS NOT DISTINCT FROM n{i[c]}""")[0]
        left = self.samples - self.have.get((chunk, table, "differing"), 0)
        if left > 0:
            self.x(f"CREATE OR REPLACE TEMP TABLE ms AS SELECT * FROM mo JOIN mn USING ({K}) ORDER BY {K} LIMIT {left}")
            lab = lambda c: "'" + c.replace("'", "''") + "'"  # noqa: E731
            parts = [f"""SELECT {K}, {lab(c)} AS "column", CAST(o{i[c]} AS VARCHAR) AS old, CAST({z[c]} AS VARCHAR) AS old_normalised,
                         CAST(n{i[c]} AS VARCHAR) AS new FROM ms WHERE {z[c]} IS DISTINCT FROM n{i[c]}""" for c in common]
            self.sample(chunk, table, "differing", " UNION ALL ".join(parts))

    def sample(self, chunk, table, cat, select):
        """Keep at most --samples rows per (chunk, table, category) in a temp table; written by write_samples()."""
        k = (chunk, table, cat)
        left = self.samples - self.have.get(k, 0)
        if left <= 0:
            return
        if k not in self.names:
            self.names[k] = "samp_" + hashlib.sha1(repr(k).encode()).hexdigest()[:12]
            self.x(f"CREATE TEMP TABLE {self.names[k]} AS SELECT * FROM ({select}) LIMIT {left}")
        else:
            self.x(f"INSERT INTO {self.names[k]} SELECT * FROM ({select}) LIMIT {left}")
        self.have[k] = self.one(f"SELECT count(*) FROM {self.names[k]}")[0]

    def write_samples(self):
        for (chunk, table, cat), name in sorted(self.names.items()):
            if self.have.get((chunk, table, cat)):
                d = os.path.join(self.out, "diffs", chunk)
                os.makedirs(d, exist_ok=True)
                self.x(f"COPY {name} TO '{os.path.join(d, table.replace('/', '__') + '.' + cat + '.parquet')}' (FORMAT parquet)")


def manifest_checks(rng, old_m, new_m, extra_m, expect_old, expect_new):
    s, t = rng
    bad, info = [], {}
    for label, m in [("old", old_m), ("new", new_m)] + [(f"extra {tb}", m) for tb, m in sorted(extra_m.items())]:
        if m is None:
            bad.append(f"{label}: no _manifest.json")
            continue
        if m.get("status") != "complete":
            bad.append(f"{label}: status {m.get('status')!r}")
        if [m.get("slot_start"), m.get("slot_end_exclusive")] != [s, t]:
            bad.append(f"{label}: manifest range {[m.get('slot_start'), m.get('slot_end_exclusive')]}, the chunk's is {[s, t]}")
        if (m.get("coverage") or {}).get("missing_slots") != 0:
            bad.append(f"{label}: coverage.missing_slots {(m.get('coverage') or {}).get('missing_slots')!r}")
        info[label] = {k: m.get(k) for k in ("extractor_sha256", "threads", "jetstreamer")}
    if old_m is None or new_m is None:
        return bad, info
    co, cn = old_m.get("counters") or {}, new_m.get("counters") or {}
    bad += [f"counter {k}: old {co[k]} new {cn[k]}" for k in SAME_COUNTERS if k in co and k in cn and co[k] != cn[k]]
    info["counters_one_side"] = {"old": sorted(set(co) - set(cn)), "new": sorted(set(cn) - set(co))}
    info["counters_info"] = {k: [co.get(k), cn.get(k)] for k in INFO_COUNTERS}
    if old_m.get("idl_sha256") != new_m.get("idl_sha256"):
        bad.append(f"idl_sha256: old {old_m.get('idl_sha256')} new {new_m.get('idl_sha256')}")
    info["manifest_keys_new_only"] = sorted(set(new_m) - set(old_m))
    return bad + expectations("old", old_m, expect_old) + expectations("new", new_m, expect_new), info


def row_checks(con, label, m, tabs, only=None):
    """Manifest rows = parquet rows per table; a blocks table, and sum(blocks.executed_tx_count) = counters.txs_seen
    (every transaction of every delivered block was seen; the new side must have the counter). `only`: an --extra
    root's tables, which need no blocks."""
    bad = [] if only is not None or "blocks" in tabs else [f"{label}: no blocks table"]
    rows = (m or {}).get("rows") or {}
    for tb in sorted((set(rows) | set(tabs)) & (only or (set(rows) | set(tabs)))):
        n = con.execute(f"SELECT count(*) FROM read_parquet({lit(tabs[tb])}, union_by_name = true)").fetchone()[0] if tb in tabs else 0
        if rows.get(tb, 0) != n:
            bad.append(f"{label} {tb}: manifest rows {rows.get(tb)}, parquet {n}")
    if "blocks" in tabs and (only is None or "blocks" in only):
        seen = ((m or {}).get("counters") or {}).get("txs_seen")
        total = con.execute(f"SELECT coalesce(sum(executed_tx_count), 0) FROM read_parquet({lit(tabs['blocks'])}, union_by_name = true)").fetchone()[0]
        if seen is None:
            if label == "new":
                bad.append("new: no counters.txs_seen")
        elif int(total) != int(seen):
            bad.append(f"{label}: sum(blocks.executed_tx_count) {total} != counters.txs_seen {seen}")
    return bad


def run(a, argv=()):
    """Checks every refusal before anything is written, then compares chunk by chunk; returns the summary."""
    if a.old or a.new:
        if a.old_root or a.new_root or a.chunks or not (a.old and a.new):
            raise Refused("either --old and --new, or --old-root, --new-root and --chunks")
        name = os.path.basename(os.path.normpath(a.old))
        if os.path.basename(os.path.normpath(a.new)) != name:
            raise Refused(f"--old {a.old} and --new {a.new} are different chunks")
        pairs = [(name, a.old, a.new)]
    else:
        if not (a.old_root and a.new_root and a.chunks):
            raise Refused("either --old and --new, or --old-root, --new-root and --chunks")
        names = a.chunks.split(",")
        if any(not n for n in names) or len(set(names)) < len(names):
            raise Refused(f"--chunks {a.chunks!r}: an empty or repeated entry")
        pairs = [(n, os.path.join(a.old_root, n), os.path.join(a.new_root, n)) for n in names]
    extras = {}
    for e in a.extra:
        tb, _, root = e.partition("=")
        if not tb or not root or tb in extras:
            raise Refused(f"--extra {e!r}: TABLE=ROOT, one per table")
        extras[tb] = root
    for p in [p for p in (a.old, a.new, a.old_root, a.new_root, a.old_footprint) if p] + list(extras.values()):
        refuse_path(p)
    refuse_path(a.out, output=True)
    refuse_path(a.spill, output=True)
    if os.path.exists(a.out):
        raise Refused(f"--out {a.out} exists (every output is new)")
    ranges = {n: chunk_range(n) for n, _, _ in pairs}
    for n, o, nw in pairs:
        for d in [o, nw] + [os.path.join(r, n) for r in extras.values()]:
            if not os.path.isdir(d):
                raise Refused(f"no chunk directory {d}")
        for label, d in [("--old", o)] + [(f"--extra {tb}", os.path.join(r, n)) for tb, r in extras.items()]:
            if nested(d, nw):
                raise Refused(f"{label} {d} and --new {nw} are one directory or nested (a chunk compared with itself)")
    mans = {n: (load_manifest(o), load_manifest(nw), {tb: load_manifest(os.path.join(r, n)) for tb, r in extras.items()})
            for n, o, nw in pairs}
    ref = None
    if a.old_footprint:
        with open(a.old_footprint) as f:
            ref = json.load(f)
    os.makedirs(a.spill, exist_ok=True)
    spill = tempfile.mkdtemp(prefix="chunk_diff-", dir=a.spill)
    try:
        con = duckdb.connect()
        con.execute(f"SET memory_limit = '{a.memory}'")
        con.execute(f"SET threads TO {int(a.threads)}")
        con.execute(f"SET temp_directory = '{os.path.join(spill, 'duckdb')}'")
        con.execute(f"SET max_temp_directory_size = '{a.spill_max}'")
        con.execute("SET preserve_insertion_order = false")
        sides = {}
        for n, o, nw in pairs:  # the hold-out guard on every file, before any data is read or anything written
            to, tn = tables(o), tables(nw)
            tx = {tb: tables(os.path.join(r, n)) for tb, r in extras.items()}
            for fs in list(to.values()) + list(tn.values()) + [f for x in tx.values() for f in x.values()]:
                hi = slot_max(con, fs)
                if hi is not None and hi >= HOLDOUT_START:
                    raise Refused(f"{fs[0]}: slot {hi:,} reaches the hold-out")
            sides[n] = (to, tn, tx)
        os.makedirs(a.out)
        d = Diff(con, spill, a.buckets, a.samples, a.normalize, a.out)
        summary = {"command": ["python", "-m", "checks.chunk_diff", *argv], "normalize": a.normalize, "buckets": a.buckets, "chunks": {}}
        prints = {}
        for n, o, nw in pairs:
            t0 = time.time()
            om, nm, xm = mans[n]
            to, tn, tx = sides[n]
            bad, info = manifest_checks(ranges[n], om, nm, xm, a.expect_old, a.expect_new)
            prints[n] = {"old": footprint(o), "new": footprint(nw)}
            if ref is not None:
                if n not in ref:
                    bad.append(f"old: no reference footprint for {n} in {a.old_footprint}")
                elif ref[n] != prints[n]["old"]:
                    off = sorted(k for k in set(ref[n]) | set(prints[n]["old"]) if ref[n].get(k) != prints[n]["old"].get(k))
                    bad.append(f"old: {len(off)} files differ from the reference footprint, e.g. {off[:3]}")
            bad += row_checks(con, "old", om, to) + row_checks(con, "new", nm, tn)
            for tb in extras:
                bad += row_checks(con, f"extra {tb}", xm[tb], tx[tb], only={tb})
            res = {}
            for tb in sorted(set(to) | set(tn) | set(extras)):
                ofs = tx[tb].get(tb) if tb in extras else to.get(tb)
                nfs = tn.get(tb)
                if ofs and nfs:
                    res[tb] = d.table(n, ranges[n], tb, ofs, nfs)
                elif nfs and tb in NEW_TABLES and tb not in extras:
                    res[tb] = {"ignored": "new after the dev builds: no counterpart in the old build", "rows": {"new": (nm or {}).get("rows", {}).get(tb)},
                               "unexpected": 0}
                elif ofs or nfs:
                    side, fs = ("old", ofs) if ofs else ("new", nfs)
                    cnt = con.execute(f"SELECT count(*) FROM read_parquet({lit(fs)}, union_by_name = true)").fetchone()[0]
                    res[tb] = {"only_side": side, "rows": {side: cnt}, "unexpected": max(cnt, 1), "findings": [f"table only in {side} ({cnt} rows)"]}
                else:
                    res[tb] = {"only_side": None, "rows": {}, "unexpected": 1, "findings": ["--extra table on neither side"]}
            summary["chunks"][n] = {"old": o, "new": nw, "extras": {tb: os.path.join(r, n) for tb, r in extras.items()},
                                    "manifest_findings": bad, "manifest_info": info, "tables": res,
                                    "unexpected": sum(v["unexpected"] for v in res.values()) + len(bad), "seconds": round(time.time() - t0, 1)}
        d.write_samples()
    finally:
        shutil.rmtree(spill, ignore_errors=True)
    summary["unexpected"] = sum(c["unexpected"] for c in summary["chunks"].values())
    summary["verdict"] = "GREEN" if summary["unexpected"] == 0 else "RED"
    with open(os.path.join(a.out, "footprints.json"), "w") as f:
        json.dump(prints, f, indent=1, sort_keys=True)
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "report.md"), "w") as f:
        f.write(report(summary))
    files = sorted(os.path.relpath(p, a.out) for p in glob.glob(os.path.join(a.out, "**", "*"), recursive=True) if os.path.isfile(p))
    with open(os.path.join(a.out, "SHA256SUMS"), "w") as f:
        for rel in files:
            with open(os.path.join(a.out, rel), "rb") as fh:
                f.write(f"{hashlib.sha256(fh.read()).hexdigest()}  {rel}\n")
    return summary


def report(s):
    out = [f"# chunk_diff: {s['verdict']} ({s['unexpected']} unexpected)", "",
           f"normalize: {s['normalize']}; buckets: {s['buckets']}; command: `{' '.join(map(str, s['command']))}`", ""]
    for n, c in s["chunks"].items():
        info = c["manifest_info"]
        side = lambda k: f"extractor {str((info.get(k) or {}).get('extractor_sha256'))[:8]}, threads {(info.get(k) or {}).get('threads')}"  # noqa: E731
        out += [f"## {n}: {c['unexpected']} unexpected", "", f"- old {c['old']} ({side('old')})", f"- new {c['new']} ({side('new')})"]
        out += [f"- old side of {tb}: {p} ({side('extra ' + tb)})" for tb, p in c["extras"].items()]
        out += [f"- informative counters [old, new]: {info.get('counters_info')}", "",
                "Manifest findings: " + ("none" if not c["manifest_findings"] else ""), *[f"- {b}" for b in c["manifest_findings"]], "",
                "| table | rows old | rows new | only old | only new | identical | expected | differing | dup old | dup new | unexpected | notes |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for tb, r in c["tables"].items():
            if "ignored" in r:
                out.append(f"| {tb} | | {r['rows'].get('new')} | | | | | | | | 0 | ignored: {r['ignored']} |")
                continue
            if "only_side" in r:
                out.append(f"| {tb} | {r['rows'].get('old', '')} | {r['rows'].get('new', '')} | | | | | | | | {r['unexpected']} | {r['findings'][0]} |")
                continue
            du = r.get("duplicates") or {}
            dd = lambda k: f"{du[k]['identical_rows']} / {du[k]['conflict_rows']}c" if du else ""  # noqa: E731
            notes = list(r["findings"])
            if r["columns"]["new_only_ignored"]:
                notes.append("new-only columns ignored: " + ", ".join(r["columns"]["new_only_ignored"]))
            if r["columns"]["old_only_expected"]:
                notes.append("old-only columns expected: " + ", ".join(r["columns"]["old_only_expected"]))
            if r.get("normalised"):
                notes.append("expected cells " + ", ".join(f"{k} {v}" for k, v in r["expected_cells"].items()))
            if r.get("column_diffs"):
                notes.append("differing columns " + ", ".join(f"{k} {v['unexpected']}" for k, v in r["column_diffs"].items() if v["unexpected"]))
            rows = r.get("rows", {})
            out.append(f"| {tb} | {rows.get('old', '')} | {rows.get('new', '')} | {r.get('only_old', '')} | {r.get('only_new', '')} | "
                       f"{r.get('identical', r.get('matched', ''))} | {r.get('expected', '')} | {r.get('differing', '')} | {dd('old')} | {dd('new')} | "
                       f"{r['unexpected']} | {'; '.join(notes)} |")
        ds = {tb: r["decode_status"] for tb, r in c["tables"].items() if r.get("decode_status")}
        ds = {tb: v for tb, v in ds.items() if set(v["old"]) - {"ok"} or set(v["new"]) - {"ok"}}
        if ds:
            out += ["", "decode_status other than ok, old / new (on-chain: 'prefix' on dev TradeEvent/BuyEvent/SellEvent, 'ok' from 1054):"]
            out += [f"- {tb}: {v['old']} / {v['new']}" for tb, v in ds.items()]
        out.append("")
    out += ["Duplicates: identical extra rows / rows of conflicting keys (c). Expected: differences the --normalize rules explain.",
            "Samples: diffs/<chunk>/<table>.<category>.parquet.", ""]
    return "\n".join(out)


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--old")
    p.add_argument("--new")
    p.add_argument("--old-root")
    p.add_argument("--new-root")
    p.add_argument("--chunks", help="S-T[,S-T ...] under both roots")
    p.add_argument("--extra", action="append", default=[], metavar="TABLE=ROOT", help="the old side of TABLE from ROOT/<chunk>/TABLE")
    p.add_argument("--normalize", choices=NORMALIZE, default="none")
    p.add_argument("--expect-old", action="append", default=[], metavar="FIELD=VALUE")
    p.add_argument("--expect-new", action="append", default=[], metavar="FIELD=VALUE")
    p.add_argument("--old-footprint", help="JSON {chunk: footprint} of the old chunks, taken elsewhere")
    p.add_argument("--out", required=True)
    p.add_argument("--memory", default="1GB")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--buckets", type=int, default=8)
    p.add_argument("--samples", type=int, default=1000)
    p.add_argument("--spill", default=os.environ.get("LAB_SPILL_DIR") or tempfile.gettempdir())
    p.add_argument("--spill-max", default="40GB")
    a = p.parse_args(argv)
    if a.buckets < 1 or a.samples < 0:
        p.error("--buckets >= 1 and --samples >= 0")
    return a


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["footprint"]:
            out = {}
            for c in argv[1:]:
                refuse_path(c)
                name = os.path.basename(os.path.normpath(c))
                chunk_range(name)
                out[name] = footprint(c)
            print(json.dumps(out, indent=1, sort_keys=True))
            return 0
        a = parse(argv)
        s = run(a, argv)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # not 1: an out-of-memory or unreadable file must not read as "the chunks differ"
        traceback.print_exc()
        print(f"FAILED: {type(e).__name__}: {(str(e).splitlines() or [''])[0]} (a tool or resource failure, not a data finding)",
              file=sys.stderr)
        return 3
    print(f"{s['verdict']}: {s['unexpected']} unexpected over {len(s['chunks'])} chunk(s); {os.path.join(a.out, 'report.md')}")
    return 0 if s["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
