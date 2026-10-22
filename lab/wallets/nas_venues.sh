#!/bin/bash
# The venue decoders (research/venue-decoders-spec-2026-10-21.md v0.2; LAB_VENUES=1) on the NAS, the lab runner: PR 1's
# Meteora DBC, Meteora DAMM v2 and Raydium LaunchLab events (emit_cpi) and the DBC / LaunchLab graduations; PR 2's Raydium
# CPMM events from the log ("Program data:" lines, emit!) with the vault-balance fallback, and CPMM's pool creations
# (venue_inits). Old Faithful history only: nothing is sent or traded.
#
#   STEP=smoke (kort): the 1054 smoke range twice with this commit's extractor (BIN): without any flag, and with
#              LAB_VENUES=1 and LAB_VENUE_CENSUS=1. GREEN only when
#              - every table the run without flags writes is the same with them (row counts and rows: EXCEPT ALL both
#                ways), and the flagged run adds only venue_census, venue_graduations, venue_inits, venue_unknown_events
#                and <venue>/<Event> tables;
#              - every venue event table decodes ok (status ok or prefix) for >= 99.9 % of its decoded rows (source cpi or
#                log; the balance rows are no decode), and the manifest counts no venue event of an unknown
#                discriminator; the ok / prefix / extra / error shares are printed per table, a prefix or extra share
#                above 1 % flagged (LAB review on #506: a pre-release epoch must not pass on prefix rows alone);
#              - per venue, the successful transactions' swap instructions in venue_census (DBC swap/swap2(+hook), DAMM
#                v2 swap/swap2, LaunchLab buy_exact_*/sell_exact_*, CPMM swap_base_input/output) and the distinct
#                swapping parents of the swap events of successful transactions (slot, tx_index, outer_ix,
#                parent_inner_ix; DBC emits EvtSwap and EvtSwap2 for one swap; CPMM's rows are log or balance) differ
#                by at most 1 % (the events a failed transaction recorded before its failure are counted apart; where
#                the counts differ, the first 10 transactions are listed); no swap event comes from a non-swap or
#                unknown parent; no venue event is unknown to the IDLs (venue_unknown_events lists them);
#              - CPMM's balance-fallback rows are at most 1 % of its swap rows (spec v0.2 §6 / §8.2).
#              Prints rows and MB per 1,000 slots per table and the epoch extrapolation, the census instructions unknown
#              to the IDLs with their first eight bytes ('?<hex>'), the IDL events without rows in the chunk (INFO), the
#              log counters (truncated, unpaired, undecodable) and the venue_inits rows.
#   STEP=migrate (kort-lang): LaunchLab's migrate_to_amm under its 2026-09-09 account layout (23 accounts; the pinned
#              IDL has the older 32): the WINDOW slots (default 20,000) of EPOCH's venues-v2 census with the most
#              migrate_to_amm transactions, extracted with this BIN (LAB_VENUES=1, LAB_VENUE_CENSUS=1); then per
#              migrate_to_amm graduation of a successful transaction: from_pool is a LaunchLab pool (a TradeEvent or
#              PoolCreateEvent pool_state of EPOCH's venues-v2 chunks or the window), to_pool is the AMM v4 pool of an
#              initialize2 in the same transaction (census), and the census names the same pool for migrate_to_amm as
#              from_pool. GREEN only with at least one graduation and every share >= 99 %.
#   STEP=epoch (lang, ~1.5-2.5 h): dev epoch 1045 with LAB_VENUES=1 and LAB_VENUE_CENSUS=1 into
#              $DATA/events/venues-v2/chunks (ops' extract_epochs.sh, THREADS=3; v1 keeps PR 1's 1045 evidence), then the
#              same decode and count checks per chunk.
#
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venues.sh
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=epoch LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venues.sh
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=migrate LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venues.sh
#
# Overrides for a dry run (never for the evidence): OPS (the ops scripts' directory), SMOKE_SLOTS, EPOCH, VENUES_ROOT, WINDOW.
# Writes to $DATA/backtests/venues-<commit7>/ (job.log, smoke-*/, the checks' logs).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=smoke or epoch}"
case "$STEP" in smoke | epoch | migrate) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
: "${BIN:?BIN=/lab/bin/lab-extractor-<commit7>, this commit build}"
OUT=$DATA/backtests/venues-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
OPS=${OPS:-/lab/ops}
SMOKE_SLOTS=${SMOKE_SLOTS:-455328000:455329000}  # the 1054 smoke range of the earlier extractor smokes
EPOCH=${EPOCH:-1045}                             # dev (never hold-out 1047-1051 or clean 1055-1063)
VENUES_ROOT=${VENUES_ROOT:-$DATA/events/venues-v2/chunks}  # v1: PR 1's 1045 chunks (6454b6c)
export PYTHONDONTWRITEBYTECODE=1
case "$EPOCH" in 1047 | 1048 | 1049 | 1050 | 1051 | 1055 | 1056 | 1057 | 1058 | 1059 | 1060 | 1061 | 1062 | 1063)
  log "RED: epoch $EPOCH is hold-out or clean"; exit 1 ;;
esac
[ -x "$BIN" ] || { log "RED: no extractor at $BIN"; exit 1; }
log "extractor $BIN sha256 $(sha256sum "$BIN" | cut -c1-16)"

# The checks of one flagged chunk (smoke or epoch): decode status, unknown events, swap events against the census.
check_chunk() {  # chunk dir, slots per extrapolation (0: none)
  "$PY" - "$1" "$2" "$REPO" <<'EOF'
import glob, json, os, sys
import duckdb

chunk, span, repo = sys.argv[1], int(sys.argv[2]), sys.argv[3]
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
bad = []
with open(os.path.join(chunk, "_manifest.json")) as f:
    man = json.load(f)
if man.get("status") != "complete" or not man.get("venues") or not man.get("venue_census"):
    bad.append(f"manifest: status {man.get('status')}, venues {bool(man.get('venues'))}, venue_census {bool(man.get('venue_census'))}")
counters = man.get("counters") or {}
lit = lambda fs: "[" + ",".join("'" + f.replace("'", "''") + "'" for f in sorted(fs)) + "]"
VENUES = ("meteora_dbc", "meteora_damm_v2", "raydium_launchlab", "raydium_cpmm")
IDLS = {"meteora_dbc": ["meteora_dbc.json"] + [f"meteora_dbc.release_0.1.{v}.json" for v in (6, 4, 3, 2)],
        "meteora_damm_v2": ["meteora_damm_v2.json"], "raydium_launchlab": ["raydium_launchlab.json"], "raydium_cpmm": ["raydium_cpmm.json"]}
tables = {}
for f in glob.glob(os.path.join(chunk, "**", "*.parquet"), recursive=True):
    tables.setdefault(os.path.relpath(os.path.dirname(f), chunk), []).append(f)
venue_tables = sorted(t for t in tables if "/" in t and t.split("/")[0] in VENUES)
for t in venue_tables:
    src = f"read_parquet({lit(tables[t])}, union_by_name = true)"
    n, n_dec, ok, prefix, extra, err, n_bal = con.execute(f"""SELECT count(*), count(*) FILTER (WHERE source <> 'balance'),
        count(*) FILTER (WHERE decode_status = 'ok'), count(*) FILTER (WHERE decode_status = 'prefix'),
        count(*) FILTER (WHERE decode_status = 'extra'), count(*) FILTER (WHERE decode_status = 'error'),
        count(*) FILTER (WHERE source = 'balance') FROM {src}""").fetchone()
    mb = sum(os.path.getsize(f) for f in tables[t]) / 1e6
    ext = f", ~{mb / span * 432_000 / 1000:.2f} GB and ~{n / span * 432_000 / 1e6:.2f} M rows per epoch" if span else ""
    share = lambda k: k / n_dec if n_dec else 0
    bal = f", {n_bal:,} balance rows" if n_bal else ""
    print(f"{t}: {n:,} rows, ok {share(ok):.4%}, prefix {share(prefix):.4%}, extra {share(extra):.4%}, error {share(err):.4%}{bal}, {mb:.2f} MB{ext}")
    if n_dec and (ok + prefix) / n_dec < 0.999:
        bad.append(f"{t}: {(ok + prefix) / n_dec:.4%} decode ok")
    for name, k in (("prefix", prefix), ("extra", extra)):
        if n_dec and k / n_dec > 0.01:
            print(f"  FLAG {t}: {name} {k / n_dec:.2%} of the decoded rows (an older or newer layout than the pinned IDL)")
# the IDL events without a table in this chunk (INFO; an absent table is no evidence)
empty = []
for venue, files in IDLS.items():
    names = set()
    for fn in files:
        path = os.path.join(repo, "lab", "extractor", "idl", "venues", fn)
        if os.path.exists(path):
            names |= {e["name"] for e in json.load(open(path)).get("events", [])}
    empty += [f"{venue}/{e}" for e in sorted(names) if f"{venue}/{e}" not in tables]
print(f"INFO: IDL events without rows in this chunk: {len(empty)}" + (f": {', '.join(empty)}" if empty else ""))
g = tables.get("venue_graduations", [])
print("venue_graduations:", con.execute(f"SELECT count(*) FROM read_parquet({lit(g)})").fetchone()[0] if g else 0)
ini = tables.get("venue_inits", [])
print("venue_inits:", dict(con.execute(f"SELECT venue || '/' || ix_name, count(*) FROM read_parquet({lit(ini)}) GROUP BY 1 ORDER BY 1").fetchall()) if ini else 0)
print("log counters: " + ", ".join(f"{k} {counters.get(k, 0):,}" for k in ("venue_log_truncated", "venue_log_unpaired", "venue_log_undecodable", "venue_balance_rows")))
# Swaps (spec v0.2 §6, smoke 99zzzzr): one swap instruction is one (slot, tx_index, outer_ix, parent_inner_ix), whatever
# number of events it emits (DBC emits EvtSwap and EvtSwap2 for one swap). Per venue, the distinct swapping parents
# (parent_ix_name a swap instruction) against the census's swap instructions of successful transactions; swap events
# whose parent is not a swap instruction (or unknown to the IDLs) are unexplained.
census = tables.get("venue_census", [])
SWAPS = {"meteora_dbc": (("swap", "swap2", "swap2_with_transfer_hook"), ("EvtSwap", "EvtSwap2", "EvtSwap2WithTransferHook", "EvtSwap3", "EvtSwap3WithTransferHook")),
         "meteora_damm_v2": (("swap", "swap2"), ("EvtSwap2",)),
         "raydium_launchlab": (("buy_exact_in", "buy_exact_out", "sell_exact_in", "sell_exact_out"), ("TradeEvent",)),
         "raydium_cpmm": (("swap_base_input", "swap_base_output"), ("SwapEvent",))}
for venue, (ixs, events) in SWAPS.items():
    names = ", ".join(f"'{venue}:{x}'" for x in ixs)
    n_ix = con.execute(f"""SELECT count(*) FROM (SELECT split_part(x, ':', 1) || ':' || split_part(x, ':', 2) AS vx
                           FROM (SELECT unnest(string_split(dex_ixs, ';')) AS x FROM read_parquet({lit(census)}) WHERE NOT failed))
                           WHERE vx IN ({names})""").fetchone()[0] if census else 0
    # an instruction the IDLs do not know carries its first eight bytes: '<venue>:?<hex>:<pool>' (LAB review on #506)
    unknown_ix = con.execute(f"""SELECT split_part(x, ':', 2), count(*) FROM (SELECT unnest(string_split(dex_ixs, ';')) AS x
                                 FROM read_parquet({lit(census)}) WHERE NOT failed) WHERE starts_with(x, '{venue}:?')
                                 GROUP BY 1 ORDER BY 2 DESC""").fetchall() if census else []
    n_q = sum(c for _, c in unknown_ix)
    files = [f for e in events for f in tables.get(f"{venue}/{e}", [])]
    sw = ", ".join(f"'{x}'" for x in ixs)
    if files:
        src = f"read_parquet({lit(files)}, union_by_name = true)"
        # like with like: the census counts swap instructions of successful transactions; events the runtime recorded
        # before a transaction failed (state rolled back) are counted apart (smoke 99zzzzs: DAMM v2's +458)
        n_ev, n_par, n_fail = con.execute(f"""SELECT count(*) FILTER (WHERE NOT failed),
                                                     count(DISTINCT (slot, tx_index, outer_ix, parent_inner_ix)) FILTER (WHERE NOT failed),
                                                     count(*) FILTER (WHERE failed)
                                              FROM {src} WHERE parent_ix_name IN ({sw})""").fetchone()
        other = con.execute(f"""SELECT COALESCE(parent_ix_name, '(unknown)'), parent_ix_disc, count(*) FROM {src}
                                WHERE parent_ix_name IS NULL OR parent_ix_name NOT IN ({sw}) GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()
        by_source = dict(con.execute(f"SELECT source || '/' || decode_status, count(*) FROM {src} WHERE NOT failed GROUP BY 1 ORDER BY 1").fetchall())
    else:
        n_ev, n_par, n_fail, other, by_source = 0, 0, 0, [], {}
    print(f"{venue}: swap instructions {n_ix:,}, swapping parents {n_par:,} ({n_ev:,} swap events, "
          f"{n_ev / n_par if n_par else 0:.2f} per swap); swap events of failed transactions (not trades) {n_fail:,}; "
          f"census instructions unknown to the IDL ('?'): {n_q:,}; rows by source/status {by_source}")
    for name, n in unknown_ix[:10]:
        print(f"  census instruction unknown to the IDLs: {venue}:{name}: {n:,}")
    for name, disc, n in other:
        print(f"  swap events from a non-swap parent: {name} {disc}: {n:,}")
    if not n_par or abs(n_par - n_ix) > 0.01 * max(n_ix, 1):
        bad.append(f"{venue}: {n_par} swapping parents against {n_ix} swap instructions")
        # where they differ: per successful transaction, the census's swap instructions against the swapping parents
        if census and files:
            for slot, txi, c_ix, e_par, parents, dex in con.execute(f"""
                    WITH c AS (SELECT slot, tx_index, count(*) AS n_ix, any_value(dex_ixs) AS dex
                               FROM (SELECT slot, tx_index, dex_ixs, unnest(string_split(dex_ixs, ';')) AS x
                                     FROM read_parquet({lit(census)}) WHERE NOT failed)
                               WHERE split_part(x, ':', 1) || ':' || split_part(x, ':', 2) IN ({names}) GROUP BY 1, 2),
                         e AS (SELECT slot, tx_index, count(DISTINCT (outer_ix, parent_inner_ix)) AS n_par,
                                      string_agg(DISTINCT outer_ix::VARCHAR || '.' || COALESCE(parent_inner_ix::VARCHAR, '-'), ',') AS parents
                               FROM {src} WHERE parent_ix_name IN ({sw}) AND NOT failed GROUP BY 1, 2)
                    SELECT slot, tx_index, COALESCE(c.n_ix, 0), COALESCE(e.n_par, 0), e.parents, left(c.dex, 300)
                    FROM c FULL JOIN e USING (slot, tx_index) WHERE COALESCE(c.n_ix, 0) <> COALESCE(e.n_par, 0)
                    ORDER BY slot, tx_index LIMIT 10""").fetchall():
                print(f"  differs: slot {slot} tx {txi}: census swap instructions {c_ix}, swapping parents {e_par} ({parents}); census {dex}")
    if other:
        bad.append(f"{venue}: {sum(n for *_, n in other)} swap events from non-swap or unknown parents")
    # CPMM's balance fallback: at most 1 % of its swap rows (spec v0.2 §6, LAB review §8.2)
    if venue == "raydium_cpmm" and files:
        n_bal = sum(v for k, v in by_source.items() if k.startswith("balance/"))
        n_all = sum(by_source.values())
        print(f"raydium_cpmm: balance-fallback rows {n_bal:,} of {n_all:,} swap rows ({n_bal / n_all if n_all else 0:.3%})")
        if n_all and n_bal / n_all > 0.01:
            bad.append(f"raydium_cpmm: {n_bal / n_all:.2%} balance-fallback rows (above 1 %)")
unk = tables.get("venue_unknown_events", [])
n_unk = 0
if unk:
    for venue, source, disc, parent, n in con.execute(f"""SELECT venue, source, event_disc, COALESCE(parent_ix_name, '(unknown)'), count(*)
                                                        FROM read_parquet({lit(unk)}) GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC""").fetchall():
        print(f"unknown event: {venue} ({source}) {disc} from {parent}: {n:,}")
        n_unk += n
n_unk = max(n_unk, counters.get("venue_unknown_events", 0))
if n_unk:
    bad.append(f"{n_unk} venue events unknown to the pinned IDLs (listed above, to be named)")
print("venues: " + ("GREEN" if not bad else "RED: " + "; ".join(bad)))
sys.exit(1 if bad else 0)
EOF
}

if [ "$STEP" = smoke ]; then
  for side in off on; do
    [ ! -e "$OUT/smoke-$side" ] || { log "RED: $OUT/smoke-$side exists (a smoke is made once)"; exit 1; }
  done
  rc=0
  BIN=$BIN SLOTS=$SMOKE_SLOTS D=$OUT/smoke-off OUT=$OUT/smoke-off/chunk bash "$OPS/smoke_extract.sh" > "$OUT/smoke-off.log" 2>&1 || rc=$?
  log "smoke without flags: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/smoke-off.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  LAB_VENUES=1 LAB_VENUE_CENSUS=1 BIN=$BIN SLOTS=$SMOKE_SLOTS D=$OUT/smoke-on OUT=$OUT/smoke-on/chunk bash "$OPS/smoke_extract.sh" \
    > "$OUT/smoke-on.log" 2>&1 || rc=$?
  log "smoke with LAB_VENUES=1 LAB_VENUE_CENSUS=1: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/smoke-on.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  "$PY" - "$OUT/smoke-off/chunk" "$OUT/smoke-on/chunk" > "$OUT/smoke-same.log" 2>&1 <<'EOF' || rc=$?
import glob, os, sys
import duckdb

off, on = sys.argv[1], sys.argv[2]
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
lit = lambda fs: "[" + ",".join("'" + f.replace("'", "''") + "'" for f in sorted(fs)) + "]"


def tables(chunk):
    out = {}
    for f in glob.glob(os.path.join(chunk, "**", "*.parquet"), recursive=True):
        out.setdefault(os.path.relpath(os.path.dirname(f), chunk), []).append(f)
    return out


t_off, t_on = tables(off), tables(on)
added = sorted(set(t_on) - set(t_off))
new_ok = lambda t: t in ("venue_census", "venue_graduations", "venue_inits", "venue_unknown_events") or t.split("/")[0] in ("meteora_dbc", "meteora_damm_v2", "raydium_launchlab", "raydium_cpmm")
bad = [f"tables only without the flags: {sorted(set(t_off) - set(t_on))}"] if set(t_off) - set(t_on) else []
bad += [f"unexpected new table {t}" for t in added if not new_ok(t)]
for t in sorted(set(t_off)):
    a, b = f"read_parquet({lit(t_off[t])}, union_by_name = true)", f"read_parquet({lit(t_on[t])}, union_by_name = true)"
    x = con.execute(f"SELECT (SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL SELECT * FROM {b})), "
                    f"(SELECT count(*) FROM (SELECT * FROM {b} EXCEPT ALL SELECT * FROM {a}))").fetchone()
    if x != (0, 0):
        bad.append(f"{t}: {x[0]} rows only without, {x[1]} only with the flags")
print(f"existing tables compared row by row: {len(t_off)}; added by the flags: {len(added)}")
print("same: " + ("GREEN" if not bad else "RED: " + "; ".join(bad)))
sys.exit(1 if bad else 0)
EOF
  tee -a "$OUT/job.log" < "$OUT/smoke-same.log"
  [ "$rc" = 0 ] || exit 1
  rc=0
  check_chunk "$OUT/smoke-on/chunk" "$(( ${SMOKE_SLOTS##*:} - ${SMOKE_SLOTS%%:*} ))" > "$OUT/smoke-check.log" 2>&1 || rc=$?
  tee -a "$OUT/job.log" < "$OUT/smoke-check.log"
  [ "$rc" = 0 ] || exit 1
  log "smoke: GREEN"
fi

if [ "$STEP" = epoch ]; then
  mkdir -p "$VENUES_ROOT"
  rc=0
  LAB_VENUES=1 LAB_VENUE_CENSUS=1 EPOCHS=$EPOCH BIN=$BIN OUT=$VENUES_ROOT LOGD=$OUT/extract-$EPOCH THREADS=${THREADS:-3} \
    bash "$OPS/extract_epochs.sh" >> "$OUT/extract-$EPOCH.console.log" 2>&1 || rc=$?
  log "extract_epochs $EPOCH: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/extract-$EPOCH.console.log" | tee -a "$OUT/job.log"; exit 1; }
  bad=0
  for m in "$VENUES_ROOT"/*/_manifest.json; do
    d=$(dirname "$m")
    s=${d##*/}
    [ "${s%%-*}" -ge $((EPOCH * 432000)) ] && [ "${s%%-*}" -lt $(((EPOCH + 1) * 432000)) ] || continue
    rc=0
    check_chunk "$d" 0 > "$OUT/epoch-check-$s.log" 2>&1 || rc=$?
    tee -a "$OUT/job.log" < "$OUT/epoch-check-$s.log"
    [ "$rc" = 0 ] || bad=1
  done
  [ "$bad" = 0 ] || { log "RED: a chunk check failed"; exit 1; }
fi
if [ "$STEP" = migrate ]; then
  M=$OUT/migrate-$EPOCH
  [ ! -e "$M" ] || { log "RED: $M exists (made once)"; exit 1; }
  mkdir -p "$M"
  window=$("$PY" - "$VENUES_ROOT" "$EPOCH" "${WINDOW:-20000}" <<'EOF'
import glob, json, os, sys
import duckdb
root, epoch, width = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
lo, hi = epoch * 432_000, (epoch + 1) * 432_000
fs = [f for m in glob.glob(os.path.join(root, "*", "_manifest.json"))
      if json.load(open(m)).get("status") == "complete" and lo <= json.load(open(m))["slot_start"] < hi
      for f in glob.glob(os.path.join(os.path.dirname(m), "venue_census", "*.parquet"))]
if not fs:
    sys.exit(f"no venue_census of epoch {epoch} under {root}")
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
slots = [s for (s,) in con.execute("SELECT slot FROM read_parquet(?) WHERE NOT failed AND contains(dex_ixs, 'raydium_launchlab:migrate_to_amm:') ORDER BY slot", [fs]).fetchall()]
if not slots:
    sys.exit(f"no migrate_to_amm in the census of epoch {epoch}: no evidence (set EPOCH)")
best, j = (0, slots[0]), 0
for i, s in enumerate(slots):
    while slots[j] < s - width + 1:
        j += 1
    if i - j + 1 > best[0]:
        best = (i - j + 1, slots[j])
print(f"{best[1]}:{best[1] + width} {best[0]} {len(slots)}")
EOF
  2> "$M.window.err") || { log "RED: no window: $(tail -1 "$M.window.err")"; exit 1; }
  read -r MSLOTS n_win n_all <<< "$window"
  log "migrate_to_amm in epoch $EPOCH's census: $n_all; window $MSLOTS holds $n_win"
  rc=0
  LAB_VENUES=1 LAB_VENUE_CENSUS=1 BIN=$BIN SLOTS=$MSLOTS D=$M OUT=$M/chunk bash "$OPS/smoke_extract.sh" > "$M.log" 2>&1 || rc=$?
  log "window extraction: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$M.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  "$PY" - "$M/chunk" "$VENUES_ROOT" "$EPOCH" > "$M-check.log" 2>&1 <<'EOF' || rc=$?
import glob, json, os, sys
import duckdb
chunk, root, epoch = sys.argv[1], sys.argv[2], int(sys.argv[3])
lo, hi = epoch * 432_000, (epoch + 1) * 432_000
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
t = lambda d, name: glob.glob(os.path.join(d, name, "*.parquet"))
dirs = [chunk] + [os.path.dirname(m) for m in glob.glob(os.path.join(root, "*", "_manifest.json"))
                  if json.load(open(m)).get("status") == "complete" and lo <= json.load(open(m))["slot_start"] < hi]
pool_files = [f for d in dirs for name in ("raydium_launchlab/TradeEvent", "raydium_launchlab/PoolCreateEvent") for f in t(d, name)]
con.execute("CREATE TABLE ll AS SELECT DISTINCT pool_state AS pool FROM read_parquet(?, union_by_name = true)", [pool_files])
grads, census = t(chunk, "venue_graduations"), t(chunk, "venue_census")
if not grads or not census:
    sys.exit("RED: the window has no venue_graduations or venue_census")
n, n_ll, n_amm, n_census = con.execute(f"""
    WITH g AS (SELECT * FROM read_parquet(?) WHERE ix_name = 'migrate_to_amm'),
         c AS (SELECT slot, tx_index, unnest(string_split(dex_ixs, ';')) AS x FROM read_parquet(?) WHERE NOT failed)
    SELECT count(*),
           count(*) FILTER (WHERE from_pool IN (SELECT pool FROM ll)),
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM c WHERE c.slot = g.slot AND c.tx_index = g.tx_index AND c.x = 'raydium_amm_v4:initialize2:' || g.to_pool)),
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM c WHERE c.slot = g.slot AND c.tx_index = g.tx_index AND c.x = 'raydium_launchlab:migrate_to_amm:' || g.from_pool))
    FROM g""", [grads, census]).fetchone()
share = lambda k: k / n if n else 0.0
print(f"migrate_to_amm graduations in the window: {n:,}")
print(f"from_pool a LaunchLab pool: {n_ll:,} ({share(n_ll):.2%}); to_pool the same transaction's AMM v4 initialize2 pool: {n_amm:,} "
      f"({share(n_amm):.2%}); the census's migrate_to_amm pool equals from_pool: {n_census:,} ({share(n_census):.2%})")
for row in con.execute("SELECT slot, tx_index, from_pool, to_pool FROM read_parquet(?) WHERE ix_name = 'migrate_to_amm' ORDER BY slot, tx_index LIMIT 5", [grads]).fetchall():
    print("  example:", row)
bad = [] if n else ["no migrate_to_amm graduation in the window"]
bad += [name for name, k in (("from_pool", n_ll), ("to_pool", n_amm), ("census pool", n_census)) if n and k / n < 0.99]
print("migrate: " + ("GREEN" if not bad else "RED: " + ", ".join(bad) + " below 99 %"))
sys.exit(1 if bad else 0)
EOF
  tee -a "$OUT/job.log" < "$M-check.log"
  [ "$rc" = 0 ] || { log "RED: the migrate check failed"; exit 1; }
fi
log "done $STEP"
