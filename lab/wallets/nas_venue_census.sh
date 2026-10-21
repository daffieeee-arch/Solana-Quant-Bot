#!/bin/bash
# The venue census (step 0 of the other venues; owner 2026-10-21, coordinator design approved) on the NAS, the lab runner
# (image python:3.13-trixie, app "solana-lab-run"). Old Faithful history only: nothing is sent or traded.
#
#   STEP=smoke  (kort): the 1054 smoke range twice with this commit's extractor (BIN), without and with
#               LAB_VENUE_CENSUS=1. GREEN only when every table but venue_census is the same in both (row counts and
#               rows: EXCEPT ALL both ways), venue_census has rows of at least two venues, and the report runs on it.
#               Prints venue_census's MB and rows per 1,000 slots and the extrapolation to an epoch (432,000 slots).
#   STEP=epoch  (lang, ~3-4 h): dev epoch 1045 with LAB_VENUE_CENSUS=1 into $DATA/events/census-v1/chunks
#               (ops' extract_epochs.sh, THREADS=3); every chunk complete with venue_census rows.
#   STEP=report (kort): wallets.venue_census over epoch 1045 into $OUT/report-1045 (venue_census.md/.json, venue_day.parquet).
#
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=smoke LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venue_census.sh
#   BIN=/lab/bin/lab-extractor-<commit7> STEP=epoch LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venue_census.sh
#   STEP=report LAB_PY=/lab/venv/bin/python bash lab/wallets/nas_venue_census.sh
#
# Overrides for a dry run (never for the evidence): OPS (the ops scripts' directory), SMOKE_SLOTS, EPOCH, CENSUS_ROOT.
# Writes to $DATA/backtests/venue-census-<commit7>/ (job.log, smoke-*/, report-*/).
set -euo pipefail
export LC_ALL=C  # the same sort order and tool output on the VPS and in the NAS container

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || cat "$REPO/COMMIT")
: "${STEP:?STEP=smoke, epoch or report}"
case "$STEP" in smoke | epoch | report) ;; *) echo "unknown STEP=$STEP"; exit 2 ;; esac
OUT=$DATA/backtests/venue-census-${COMMIT:0:7}
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/job.log"; }
log "start $STEP commit=$COMMIT out=$OUT"
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
OPS=${OPS:-/lab/ops}
SMOKE_SLOTS=${SMOKE_SLOTS:-455328000:455329000}  # the 1054 smoke range of the earlier extractor smokes
EPOCH=${EPOCH:-1045}                             # dev (never hold-out 1047-1051 or clean 1055-1063)
CENSUS_ROOT=${CENSUS_ROOT:-$DATA/events/census-v1/chunks}
export PYTHONDONTWRITEBYTECODE=1
case "$EPOCH" in 1047 | 1048 | 1049 | 1050 | 1051 | 1055 | 1056 | 1057 | 1058 | 1059 | 1060 | 1061 | 1062 | 1063)
  log "RED: epoch $EPOCH is hold-out or clean"; exit 1 ;;
esac

if [ "$STEP" = smoke ] || [ "$STEP" = epoch ]; then
  : "${BIN:?BIN=/lab/bin/lab-extractor-<commit7>, this commit build}"
  [ -x "$BIN" ] || { log "RED: no extractor at $BIN"; exit 1; }
  log "extractor $BIN sha256 $(sha256sum "$BIN" | cut -c1-16)"
fi

if [ "$STEP" = smoke ]; then
  for side in off on; do
    [ ! -e "$OUT/smoke-$side" ] || { log "RED: $OUT/smoke-$side exists (a smoke is made once)"; exit 1; }
  done
  rc=0
  BIN=$BIN SLOTS=$SMOKE_SLOTS D=$OUT/smoke-off OUT=$OUT/smoke-off/chunk bash "$OPS/smoke_extract.sh" > "$OUT/smoke-off.log" 2>&1 || rc=$?
  log "smoke without the census: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/smoke-off.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  LAB_VENUE_CENSUS=1 BIN=$BIN SLOTS=$SMOKE_SLOTS D=$OUT/smoke-on OUT=$OUT/smoke-on/chunk bash "$OPS/smoke_extract.sh" > "$OUT/smoke-on.log" 2>&1 || rc=$?
  log "smoke with the census: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/smoke-on.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  "$PY" - "$OUT/smoke-off/chunk" "$OUT/smoke-on/chunk" "$SMOKE_SLOTS" > "$OUT/smoke-check.log" 2>&1 <<'EOF' || rc=$?
import glob, json, os, sys
import duckdb

off, on, slots = sys.argv[1], sys.argv[2], sys.argv[3]
lo, hi = (int(x) for x in slots.split(":"))
bad = []


def tables(chunk):
    out = {}
    for f in glob.glob(os.path.join(chunk, "**", "*.parquet"), recursive=True):
        out.setdefault(os.path.relpath(os.path.dirname(f), chunk), []).append(f)
    return out


def manifest(chunk):
    with open(os.path.join(chunk, "_manifest.json")) as f:
        return json.load(f)


m_off, m_on = manifest(off), manifest(on)
for name, m in (("off", m_off), ("on", m_on)):
    if m.get("status") != "complete":
        bad.append(f"the {name} smoke is {m.get('status')}")
r_off, r_on = m_off.get("rows") or {}, m_on.get("rows") or {}
diff = {k: (r_off.get(k), r_on.get(k)) for k in set(r_off) | set(r_on) if k != "venue_census" and r_off.get(k) != r_on.get(k)}
print("manifest rows but venue_census:", "identical" if not diff else diff, "| tables", len(r_off))
if diff or not r_off:
    bad.append("manifest rows differ or are empty")
if m_off.get("venue_census") is not None or not m_on.get("venue_census"):
    bad.append("manifest venue_census: off must be null, on must describe the table")
con = duckdb.connect()
con.execute("SET memory_limit = '4GB'; SET threads = 4")
t_off, t_on = tables(off), tables(on)
if set(t_off) != set(t_on) - {"venue_census"}:
    bad.append(f"table sets differ: {sorted(set(t_off) ^ (set(t_on) - {'venue_census'}))}")
lit = lambda fs: "[" + ",".join("'" + f.replace("'", "''") + "'" for f in sorted(fs)) + "]"
for t in sorted(set(t_off) & set(t_on)):
    a, b = f"read_parquet({lit(t_off[t])}, union_by_name = true)", f"read_parquet({lit(t_on[t])}, union_by_name = true)"
    x = con.execute(f"SELECT (SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL SELECT * FROM {b})), "
                    f"(SELECT count(*) FROM (SELECT * FROM {b} EXCEPT ALL SELECT * FROM {a}))").fetchone()
    if x != (0, 0):
        bad.append(f"{t}: {x[0]} rows only without, {x[1]} only with the census")
print(f"tables compared row by row: {len(set(t_off) & set(t_on))}")
vc = t_on.get("venue_census", [])
if not vc:
    bad.append("no venue_census table")
else:
    n, venues = con.execute(f"SELECT count(*), count(DISTINCT v) FROM (SELECT unnest(string_split(venues, ',')) AS v "
                            f"FROM read_parquet({lit(vc)}))").fetchone()
    mb = sum(os.path.getsize(f) for f in vc) / 1e6
    per = (hi - lo) / 1000
    print(f"venue_census: {n:,} rows, {venues} venues, {mb:.2f} MB over {hi - lo:,} slots = {mb / per:.2f} MB and "
          f"{n / per:,.0f} rows per 1,000 slots; an epoch (432,000 slots): ~{mb / per * 432 / 1000:.1f} GB, ~{n / per * 432 / 1e6:.1f} M rows")
    for v, c in con.execute(f"SELECT v, count(*) FROM (SELECT unnest(string_split(venues, ',')) AS v FROM read_parquet({lit(vc)})) "
                            "GROUP BY v ORDER BY 2 DESC").fetchall():
        print(f"  {v}: {c:,}")
    if n == 0 or venues < 2:
        bad.append(f"venue_census has {n} rows over {venues} venues")
print("smoke: " + ("GREEN" if not bad else "RED: " + "; ".join(bad)))
sys.exit(1 if bad else 0)
EOF
  tee -a "$OUT/job.log" < "$OUT/smoke-check.log"
  [ "$rc" = 0 ] || exit 1
  # The report on the smoke chunk (a functional check; one partial day).
  root=$OUT/smoke-on/root
  mkdir -p "$root"
  ln -sfn "$OUT/smoke-on/chunk" "$root/chunk"
  rc=0
  (cd "$REPO/lab" && PYTHONPATH=$REPO/lab "$PY" -m wallets.venue_census --chunks "$root" --from "${SMOKE_SLOTS%%:*}" \
    --to "${SMOKE_SLOTS##*:}" --out "$OUT/report-smoke") > "$OUT/report-smoke.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -20 "$OUT/report-smoke.log" | tee -a "$OUT/job.log"; log "RED: the report failed on the smoke"; exit 1; }
  head -20 "$OUT/report-smoke/venue_census.md" | tee -a "$OUT/job.log"
  log "smoke: GREEN"
fi

if [ "$STEP" = epoch ]; then
  mkdir -p "$CENSUS_ROOT"
  rc=0
  LAB_VENUE_CENSUS=1 EPOCHS=$EPOCH BIN=$BIN OUT=$CENSUS_ROOT LOGD=$OUT/extract-$EPOCH THREADS=${THREADS:-3} \
    bash "$OPS/extract_epochs.sh" >> "$OUT/extract-$EPOCH.console.log" 2>&1 || rc=$?
  log "extract_epochs $EPOCH: rc $rc"
  [ "$rc" = 0 ] || { tail -20 "$OUT/extract-$EPOCH.console.log" | tee -a "$OUT/job.log"; exit 1; }
  rc=0
  "$PY" - "$CENSUS_ROOT" "$EPOCH" > "$OUT/epoch-check-$EPOCH.log" 2>&1 <<'EOF' || rc=$?
import glob, json, os, sys
root, e = sys.argv[1], int(sys.argv[2])
lo, hi = e * 432_000, (e + 1) * 432_000
bad, n, done = [], 0, 0
for m in sorted(glob.glob(os.path.join(root, "*", "_manifest.json"))):
    with open(m) as f:
        man = json.load(f)
    if not (man["slot_start"] < hi and lo < man["slot_end_exclusive"]):
        continue
    rows = (man.get("rows") or {}).get("venue_census", 0)
    mb = sum(os.path.getsize(f) for f in glob.glob(os.path.join(os.path.dirname(m), "venue_census", "*.parquet"))) / 1e6
    print(f"{os.path.basename(os.path.dirname(m))}: {man['status']}, venue_census {rows:,} rows, {mb:,.0f} MB, "
          f"{man.get('elapsed_seconds', 0) / 3600:.1f} h, http_429 {man.get('counters', {}).get('http_429')}")
    done += man["slot_end_exclusive"] - man["slot_start"] if man["status"] == "complete" else 0
    n += 1
    if man["status"] != "complete" or not rows or not man.get("venue_census"):
        bad.append(os.path.basename(os.path.dirname(m)))
if done < hi - lo:
    bad.append(f"complete chunks cover {done:,} of {hi - lo:,} slots")
print(f"epoch {e}: " + ("GREEN" if not bad and n else f"RED: {bad}"))
sys.exit(1 if bad or not n else 0)
EOF
  tee -a "$OUT/job.log" < "$OUT/epoch-check-$EPOCH.log"
  [ "$rc" = 0 ] || exit 1
fi

if [ "$STEP" = report ]; then
  rc=0
  (cd "$REPO/lab" && PYTHONPATH=$REPO/lab "$PY" -m wallets.venue_census --chunks "$CENSUS_ROOT" --epoch "$EPOCH" \
    --out "$OUT/report-$EPOCH" --memory "${MEMORY:-12GB}" --threads "${REPORT_THREADS:-6}") > "$OUT/report-$EPOCH.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { tail -20 "$OUT/report-$EPOCH.log" | tee -a "$OUT/job.log"; log "RED: the report failed"; exit 1; }
  head -40 "$OUT/report-$EPOCH/venue_census.md" | tee -a "$OUT/job.log"
fi
log "done $STEP"
