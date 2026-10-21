#!/bin/bash
# The venue decoders, PR 1 (research/venue-decoders-spec-2026-10-21.md v0.2: Meteora DBC, Meteora DAMM v2, Raydium
# LaunchLab events and the DBC / LaunchLab graduations; LAB_VENUES=1) on the NAS, the lab runner. Old Faithful history
# only: nothing is sent or traded.
#
#   STEP=smoke (kort): the 1054 smoke range twice with this commit's extractor (BIN): without any flag, and with
#              LAB_VENUES=1 and LAB_VENUE_CENSUS=1. GREEN only when
#              - every table the run without flags writes is the same with them (row counts and rows: EXCEPT ALL both
#                ways), and the flagged run adds only venue_census, venue_graduations and <venue>/<Event> tables;
#              - every venue event table decodes ok (status ok or prefix) for >= 99.9 % of its rows, and the manifest
#                counts no venue event of an unknown discriminator;
#              - per venue, the successful transactions' swap instructions in venue_census (DBC swap/swap2(+hook), DAMM
#                v2 swap/swap2, LaunchLab buy_exact_*/sell_exact_*) and the distinct swapping parents of the swap events
#                (slot, tx_index, outer_ix, parent_inner_ix; DBC emits EvtSwap and EvtSwap2 for one swap) differ by at
#                most 1 %; no swap event comes from a non-swap or unknown parent; no venue event is unknown to the IDLs
#                (venue_unknown_events lists them by discriminator to be named).
#              Prints rows and MB per 1,000 slots per table and the epoch extrapolation.
#   STEP=epoch (lang, ~1.5-2.5 h): dev epoch 1045 with LAB_VENUES=1 and LAB_VENUE_CENSUS=1 into $DATA/events/venues-v1/chunks
#              (ops' extract_epochs.sh, THREADS=3), then the same decode and count checks per chunk.
#
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venues.sh
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=epoch LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venues.sh
#
# Overrides for a dry run (never for the evidence): OPS (the ops scripts' directory), SMOKE_SLOTS, EPOCH, VENUES_ROOT.
# Writes to $DATA/backtests/venues-<commit7>/ (job.log, smoke-*/, the checks' logs).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=smoke or epoch}"
case "$STEP" in smoke | epoch) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
: "${BIN:?BIN=/lab/bin/lab-extractor-<commit7>, this commit build}"
OUT=$DATA/backtests/venues-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
OPS=${OPS:-/lab/ops}
SMOKE_SLOTS=${SMOKE_SLOTS:-455328000:455329000}  # the 1054 smoke range of the earlier extractor smokes
EPOCH=${EPOCH:-1045}                             # dev (never hold-out 1047-1051 or clean 1055-1063)
VENUES_ROOT=${VENUES_ROOT:-$DATA/events/venues-v1/chunks}
export PYTHONDONTWRITEBYTECODE=1
case "$EPOCH" in 1047 | 1048 | 1049 | 1050 | 1051 | 1055 | 1056 | 1057 | 1058 | 1059 | 1060 | 1061 | 1062 | 1063)
  log "RED: epoch $EPOCH is hold-out or clean"; exit 1 ;;
esac
[ -x "$BIN" ] || { log "RED: no extractor at $BIN"; exit 1; }
log "extractor $BIN sha256 $(sha256sum "$BIN" | cut -c1-16)"

# The checks of one flagged chunk (smoke or epoch): decode status, unknown events, swap events against the census.
check_chunk() {  # chunk dir, slots per extrapolation (0: none)
  "$PY" - "$1" "$2" <<'EOF'
import glob, json, os, sys
import duckdb

chunk, span = sys.argv[1], int(sys.argv[2])
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
bad = []
with open(os.path.join(chunk, "_manifest.json")) as f:
    man = json.load(f)
if man.get("status") != "complete" or not man.get("venues") or not man.get("venue_census"):
    bad.append(f"manifest: status {man.get('status')}, venues {bool(man.get('venues'))}, venue_census {bool(man.get('venue_census'))}")
lit = lambda fs: "[" + ",".join("'" + f.replace("'", "''") + "'" for f in sorted(fs)) + "]"
tables = {}
for f in glob.glob(os.path.join(chunk, "**", "*.parquet"), recursive=True):
    tables.setdefault(os.path.relpath(os.path.dirname(f), chunk), []).append(f)
venue_tables = sorted(t for t in tables if "/" in t and t.split("/")[0] in ("meteora_dbc", "meteora_damm_v2", "raydium_launchlab"))
for t in venue_tables:
    n, ok = con.execute(f"SELECT count(*), count(*) FILTER (WHERE decode_status IN ('ok', 'prefix')) FROM read_parquet({lit(tables[t])}, union_by_name = true)").fetchone()
    mb = sum(os.path.getsize(f) for f in tables[t]) / 1e6
    ext = f", ~{mb / span * 432_000 / 1000:.2f} GB and ~{n / span * 432_000 / 1e6:.2f} M rows per epoch" if span else ""
    print(f"{t}: {n:,} rows, {ok / n if n else 0:.4%} ok, {mb:.2f} MB{ext}")
    if n and ok / n < 0.999:
        bad.append(f"{t}: {ok / n:.4%} decode ok")
g = tables.get("venue_graduations", [])
print("venue_graduations:", con.execute(f"SELECT count(*) FROM read_parquet({lit(g)})").fetchone()[0] if g else 0)
# Swaps (spec v0.2 §6, smoke 99zzzzr): one swap instruction is one (slot, tx_index, outer_ix, parent_inner_ix), whatever
# number of events it emits (DBC emits EvtSwap and EvtSwap2 for one swap). Per venue, the distinct swapping parents
# (parent_ix_name a swap instruction) against the census's swap instructions of successful transactions; swap events
# whose parent is not a swap instruction (or unknown to the IDLs) are unexplained.
census = tables.get("venue_census", [])
SWAPS = {"meteora_dbc": (("swap", "swap2", "swap2_with_transfer_hook"), ("EvtSwap", "EvtSwap2", "EvtSwap2WithTransferHook", "EvtSwap3", "EvtSwap3WithTransferHook")),
         "meteora_damm_v2": (("swap", "swap2"), ("EvtSwap2",)),
         "raydium_launchlab": (("buy_exact_in", "buy_exact_out", "sell_exact_in", "sell_exact_out"), ("TradeEvent",))}
for venue, (ixs, events) in SWAPS.items():
    names = ", ".join(f"'{venue}:{x}'" for x in ixs)
    n_ix = con.execute(f"""SELECT count(*) FROM (SELECT split_part(x, ':', 1) || ':' || split_part(x, ':', 2) AS vx
                           FROM (SELECT unnest(string_split(dex_ixs, ';')) AS x FROM read_parquet({lit(census)}) WHERE NOT failed))
                           WHERE vx IN ({names})""").fetchone()[0] if census else 0
    n_q = con.execute(f"""SELECT count(*) FROM (SELECT unnest(string_split(dex_ixs, ';')) AS x FROM read_parquet({lit(census)}) WHERE NOT failed)
                          WHERE starts_with(x, '{venue}:?:')""").fetchone()[0] if census else 0
    files = [f for e in events for f in tables.get(f"{venue}/{e}", [])]
    sw = ", ".join(f"'{x}'" for x in ixs)
    if files:
        src = f"read_parquet({lit(files)}, union_by_name = true)"
        n_ev, n_par = con.execute(f"""SELECT count(*), count(DISTINCT (slot, tx_index, outer_ix, parent_inner_ix))
                                      FROM {src} WHERE parent_ix_name IN ({sw})""").fetchone()
        other = con.execute(f"""SELECT COALESCE(parent_ix_name, '(unknown)'), parent_ix_disc, count(*) FROM {src}
                                WHERE parent_ix_name IS NULL OR parent_ix_name NOT IN ({sw}) GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()
    else:
        n_ev, n_par, other = 0, 0, []
    print(f"{venue}: swap instructions {n_ix:,}, swapping parents {n_par:,} ({n_ev:,} swap events, "
          f"{n_ev / n_par if n_par else 0:.2f} per swap); census instructions unknown to the IDL ('?'): {n_q:,}")
    for name, disc, n in other:
        print(f"  swap events from a non-swap parent: {name} {disc}: {n:,}")
    if not n_par or abs(n_par - n_ix) > 0.01 * max(n_ix, 1):
        bad.append(f"{venue}: {n_par} swapping parents against {n_ix} swap instructions")
    if other:
        bad.append(f"{venue}: {sum(n for *_, n in other)} swap events from non-swap or unknown parents")
unk = tables.get("venue_unknown_events", [])
n_unk = 0
if unk:
    for venue, disc, parent, n in con.execute(f"""SELECT venue, event_disc, COALESCE(parent_ix_name, '(unknown)'), count(*)
                                                FROM read_parquet({lit(unk)}) GROUP BY 1, 2, 3 ORDER BY 4 DESC""").fetchall():
        print(f"unknown event: {venue} {disc} from {parent}: {n:,}")
        n_unk += n
n_unk = max(n_unk, (man.get("counters") or {}).get("venue_unknown_events", 0))
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
new_ok = lambda t: t in ("venue_census", "venue_graduations", "venue_unknown_events") or t.split("/")[0] in ("meteora_dbc", "meteora_damm_v2", "raydium_launchlab")
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
log "done $STEP"
