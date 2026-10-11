#!/bin/bash
# Whale tracker step 1 on the NAS (roadmap #167): episodes -> candidates -> follower replay -> daily scores.
# Resumable: every step skips outputs that already exist. One log per step under $OUT/nas-step1/log.
#
#   LAB_ENGINE=/home/chupa/Solana-project/data-old-faithful-one/lab/backtests/f7rev-1033-1046/engine/backtest \
#   LAB_SPILL_DIR=/lab/tmp/nas-step1 LAB_SPILL_MAX=8GB bash lab/wallets/nas_step1.sh
#
# Tunables (env): FROM/TO slots (development only; TO leaves 4 h before the hold-out for the sell
# tail), PAR (parallel episode processes), WORKERS (follow workers per day), MIN_N, CONTROL, CAP.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DATA=${LAB_DATA_ROOT:-/home/chupa/Solana-project/data-old-faithful-one/lab}
OUT=${LAB_WALLETS_OUT:-$DATA/wallets}
STORE=${LAB_STORE:-$DATA/store/dev2.duckdb}
PY=${LAB_PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
FROM=${FROM:-446256000}
TO=${TO:-452250000}
SLICE=${SLICE:-53850}          # ~4 h at 267 ms
PAR=${PAR:-7}
WORKERS=${WORKERS:-7}
MIN_N=${MIN_N:-5}
CONTROL=${CONTROL:-1000}
CAP=${CAP:-5}                  # episodes per wallet per day
EP=$OUT/episodes-v1
FO=$OUT/follow-v1
SC=$OUT/scores-v1
CL=$OUT/clusters-v1            # step-0 output (labels); optional
LOG=$OUT/nas-step1/log

# follow.py wants the directory that holds backtest/; accept .../engine/backtest too.
case "${LAB_ENGINE:-}" in */backtest) export LAB_ENGINE=${LAB_ENGINE%/backtest} ;; esac
export PYTHONPATH=$REPO/lab LAB_STORE=$STORE LAB_WALLETS_OUT=$OUT
export LAB_SPILL_DIR=${LAB_SPILL_DIR:-/lab/tmp/nas-step1} LAB_SPILL_MAX=${LAB_SPILL_MAX:-8GB}
mkdir -p "$EP" "$FO" "$SC" "$LOG" "$LAB_SPILL_DIR"
log() { echo "$(date +%FT%T) $*" | tee -a "$LOG/nas_step1.log"; }
log "start repo=$REPO commit=$(git -C "$REPO" rev-parse --short HEAD) store=$STORE out=$OUT engine=${LAB_ENGINE:-?}"

# ---------------------------------------------------------------- 1. episodes, PAR at a time
slices=()
a=$FROM
while [ "$a" -lt "$TO" ]; do b=$((a + SLICE)); [ "$b" -gt "$TO" ] && b=$TO; slices+=("$a $b"); a=$b; done
todo=0
for s in "${slices[@]}"; do set -- $s; [ -f "$EP/episodes-$1.parquet" ] || todo=$((todo + 1)); done
log "step 1 episodes: ${#slices[@]} slices, $todo to do, $PAR parallel"
printf '%s\n' "${slices[@]}" | while read -r a b; do
  [ -f "$EP/episodes-$a.parquet" ] && continue
  echo "$a $b"
done | xargs -P "$PAR" -L 1 bash -c '
  a=$0; b=$1
  if nice -n 10 '"$PY"' -m wallets.episodes --from-slot "$a" --to-slot "$b" --out "'"$EP"'" > "'"$LOG"'/episodes-$a.log" 2>&1; then
    echo "$(date +%FT%T) episodes $a-$b ok: $(tail -1 "'"$LOG"'/episodes-$a.log")"
  else
    echo "$(date +%FT%T) episodes $a-$b FAILED (see log)"; exit 1
  fi' | tee -a "$LOG/nas_step1.log"
for s in "${slices[@]}"; do set -- $s; [ -f "$EP/episodes-$1.parquet" ] || { log "step 1 incomplete: $1"; exit 1; }; done
log "step 1 done"

# ---------------------------------------------------------------- 2. candidates
if [ ! -s "$FO/wallets.csv" ]; then
  log "step 2 candidates: >= $MIN_N episodes, $CONTROL controls with 2..$((MIN_N - 1))"
  $PY -I - <<EOF 2>&1 | tee -a "$LOG/nas_step1.log"
import duckdb
con = duckdb.connect()
con.execute("SET memory_limit='2GB'; SET threads TO 4; SET temp_directory='$LAB_SPILL_DIR/cand'; SET max_temp_directory_size='$LAB_SPILL_MAX'")
con.execute("""COPY (
  WITH c AS (SELECT wallet, count(*) AS n FROM '$EP/episodes-*.parquet' GROUP BY wallet),
  cand AS (SELECT wallet, false AS control FROM c WHERE n >= $MIN_N),
  ctrl AS (SELECT wallet, true FROM c WHERE n BETWEEN 2 AND $MIN_N - 1 ORDER BY hash(wallet || 'control-v1') LIMIT $CONTROL)
  SELECT * FROM cand UNION ALL SELECT * FROM ctrl) TO '$FO/wallets.csv.tmp' (HEADER)""")
print("candidates:", con.sql("SELECT control, count(*) FROM read_csv('$FO/wallets.csv.tmp', header=true) GROUP BY 1 ORDER BY 1").fetchall())
EOF
  mv "$FO/wallets.csv.tmp" "$FO/wallets.csv"
fi
log "step 2 done: $(($(wc -l < "$FO/wallets.csv") - 1)) wallets"

# ---------------------------------------------------------------- 3. follower replay per UTC day
# Day slot ranges from the store's blocks table (the first slot of every UTC day inside [FROM, TO)).
days=$($PY -I - <<EOF
import duckdb
con = duckdb.connect("$STORE", read_only=True)
con.execute("SET memory_limit='500MB'; SET threads TO 2")
rows = con.execute("""
  SELECT strftime(make_timestamp(block_time * 1000000), '%Y-%m-%d') AS d, min(slot) FROM blocks
  WHERE slot >= $FROM AND slot < $TO GROUP BY d ORDER BY d""").fetchall()
for (d, lo), nxt in zip(rows, rows[1:] + [(None, $TO)]):
    print(d, lo, nxt[1])
EOF
)
log "step 3 follow: $(echo "$days" | wc -l) days, $WORKERS workers, cap $CAP/wallet/day, W_half, 0.5 SOL, d=1,2"
echo "$days" | while read -r d lo hi; do
  if [ -f "$FO/run-follow-$lo-$hi.json" ]; then log "follow $d already done"; continue; fi
  if nice -n 10 $PY -m wallets.follow --episodes "$EP" --from-slot "$lo" --to-slot "$hi" --out "$FO" \
       --wallets "$FO/wallets.csv" --sizes 0.5 --delays 1,2 --variants W_half --max-per-wallet "$CAP" \
       --workers "$WORKERS" > "$LOG/follow-$d.log" 2>&1; then
    log "follow $d ok: $(tail -1 "$LOG/follow-$d.log")"
  else
    log "follow $d FAILED (see log)"; exit 1
  fi
done
log "step 3 done"

# ---------------------------------------------------------------- 4. scores per day
clusters_opt=()
[ -d "$CL" ] && clusters_opt=(--clusters "$CL")
echo "$days" | tail -n +2 | while read -r d lo hi; do
  if [ -f "$SC/leaderboard-$d.parquet" ]; then log "score $d already done"; continue; fi
  if nice -n 10 $PY -m wallets.score --day "$d" --episodes "$EP" --follow "$FO" "${clusters_opt[@]}" --out "$SC" \
       > "$LOG/score-$d.log" 2>&1; then
    log "score $d ok: $(tail -1 "$LOG/score-$d.log")"
  else
    log "score $d FAILED (see log)"; exit 1
  fi
done
log "step 4 done; outputs: $EP $FO $SC"
