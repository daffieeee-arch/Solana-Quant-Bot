#!/bin/bash
# The write-off deploy's rehearsal (deploy-writeoff-2026-10-23 = 3a9e982 + #533's write-off lines; LAB review on #533),
# on the VPS, on COPIES only: the live journal by the sqlite backup API from a read-only connection, the recorder's
# segments as link copies (paper.restart_root). Never --catch-up on the production journal; --no-backup on every run.
# A control (CTL = 3a9e982, the running engine) and the deploy (DEP = the tag), both restarted on their copy with the
# production arguments; then the three checks:
#   1. the deploy's restart record equals the control's but for written_off_at_resume = the journal's open positions the
#      replay writes off (expected 12 on 23 Oct: 4 K3 + 8 N2), reported on a line of its own;
#   2. those positions' exits in the deploy: 'written_off', at their own exit slot (decision + d, before the HWM), the
#      decision at or after the deadline (entry_slot + cap_slots), proceeds 0, day = the restart's day, no restart_lost;
#      in the control they are still open at the HWM (closed only by the catch-up's end of data, an artefact);
#   3. their loss is booked on the restart's day: K3's sum of written_off P&L, the HWM's K3 day loss, and K3's first
#      signal after the resume on that day ('daily loss limit' in the deploy when the sum passes 1 SOL).
#
#   CTL=3a9e982 DEP=deploy-writeoff-2026-10-23 bash lab/paper/rehearse_writeoff.sh       (a 4G scope under heavy.lock)
#
# Needs: a git repository with both refs (GIT, default this checkout), LAB (the lab data root), PY, MAIN_LAB (a checkout of
# main's lab/, for paper.restart_root). Writes $LAB/tmp/rehearsal-writeoff-<UTC>/ (the copies, the logs, checks.log).
set -euo pipefail
export LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
GIT=${GIT:-$(cd "$(dirname "$0")/../.." && pwd)}
LAB=${LAB:-/home/chupa/Solana-project/data-old-faithful-one/lab}
PY=${PY:-/home/chupa/.local/share/solana-quant/toolchains/lab-py/bin/python}
MAIN_LAB=${MAIN_LAB:-/home/chupa/Solana-project/worktrees/lab-paper-writeoff/lab}
CTL=${CTL:-3a9e982}
DEP=${DEP:-deploy-writeoff-2026-10-23b}
JOURNAL=$LAB/journal/paper-k3/journal
R=$LAB/tmp/rehearsal-writeoff-$(date -u +%Y%m%dT%H%MZ)
mkdir -p "$R"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$R/checks.log"; }
log "rehearsal CTL=$(git -C "$GIT" rev-parse "$CTL^{commit}") DEP=$(git -C "$GIT" rev-parse "$DEP^{commit}") out=$R"

# The copies: the journal (read-only backup) with its files, and the recorder's segments (links).
for side in ctl dep; do
  mkdir -p "$R/j-$side"
  "$PY" - "$JOURNAL/journal.sqlite" "$R/j-$side/journal.sqlite" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
EOF
  for f in "$JOURNAL"/wallet*.json "$JOURNAL"/creation_source.json; do [ ! -e "$f" ] || cp -a "$f" "$R/j-$side/"; done
  for d in "$JOURNAL"/20??-??-??; do [ ! -d "$d" ] || cp -a "$d" "$R/j-$side/"; done
  (cd "$MAIN_LAB" && PYTHONPATH=. "$PY" -m paper.restart_root --root "$LAB/recorder" --out "$R/rec-$side" --from-seq 0) > "$R/rec-$side.log" 2>&1
  mkdir -p "$R/$side"
  git -C "$GIT" archive "$([ "$side" = ctl ] && echo "$CTL" || echo "$DEP")" lab | tar -x -C "$R/$side"
done
log "copies made: journal backup x2, recorder links x2, engine exports x2"
# The state before the restarts, from the deploy's copy (not the live journal, which moves on).
"$PY" - "$R/j-dep/journal.sqlite" "$R/before.json" <<'EOF'
import json, sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
pos = [list(r) for r in db.execute("SELECT family, variant, key, t, entry_slot, cost FROM positions")]
hwm_state = json.loads(db.execute("SELECT v FROM state WHERE k = 'hwm_state'").fetchone()[0])
json.dump({"positions": pos, "hwm_state": hwm_state}, open(sys.argv[2], "w"))
EOF

# The restarts: the production arguments (lab-paper.service + live.env), on the copies, --catch-up and --no-backup.
for side in ctl dep; do
  rc=0
  (cd "$R/$side/lab" && PYTHONPATH=. "$PY" -m paper.engine --live "$R/rec-$side" --catch-up --no-backup \
     --config paper/k3-paper-2026-10-12.yaml --journal "$R/j-$side" --store "$LAB/store/fwd1053-1054.duckdb" \
     --groups paper/h-arms-2026-10-16.yaml --watchlist-no-m1) > "$R/$side.log" 2>&1 || rc=$?
  log "$side: rc $rc, $(tail -1 "$R/$side.log")"
  [ "$rc" = 0 ] || { log "RED: the $side run failed"; exit 1; }
done

rc=0
"$PY" - "$R/before.json" "$R/j-ctl" "$R/j-dep" >> "$R/checks.log" 2>&1 <<'EOF' || rc=$?
import json, sqlite3, sys
before, ctl, dep = sys.argv[1:4]
con = lambda p: sqlite3.connect(f"file:{p}?mode=ro", uri=True)  # noqa: E731
b = json.load(open(before))
open_before = {tuple(r[:4]): r for r in b["positions"]}
hwm_state = b["hwm_state"]
def last_restart(p):
    return json.loads(con(f"{p}/journal.sqlite").execute("SELECT v FROM state WHERE k = 'restarts'").fetchone()[0])[-1]
rc, rd = last_restart(ctl), last_restart(dep)
cc, cd = rc["counts"], rd["counts"]
n_wo = cd.get("written_off_at_resume", 0)
other = {k for k in set(cc) | set(cd) if k != "written_off_at_resume" and cc.get(k) != cd.get(k)}
print(f"open in the live journal before: {len(open_before)} {sorted({(k[0], k[1]) for k in open_before})}")
print(f"1. restart records: control {cc}; deploy {cd}; written_off_at_resume {n_wo}; other counts different: {sorted(other)}")
ok1 = n_wo > 0 and not other
db = con(f"{dep}/journal.sqlite")
db.row_factory = sqlite3.Row
xs = [dict(r) for r in db.execute("SELECT * FROM exits WHERE exit_reason = 'written_off'")]
at_resume = [x for x in xs if (x["family"], x["variant"], x["key"], x["t"]) in open_before]
bad2 = [x for x in at_resume if not (x["exit_slot"] == x["exit_decision_slot"] + (x["entry_slot"] - x["t"]) and x["exit_slot"] < rd["hwm"]
        and x["cap_slots"] is not None and x["exit_decision_slot"] >= x["entry_slot"] + x["cap_slots"] and not x["proceeds"]
        and x["day"] == rd["day"])]
lost = [dict(r) for r in db.execute("SELECT family, variant, key, t FROM exits WHERE exit_reason = 'restart_lost' AND t >= 459648000")]
lost_now = [r for r in lost if (r["family"], r["variant"], r["key"], r["t"]) in open_before]
dpos = {tuple(r) for r in db.execute("SELECT family, variant, key, t FROM positions")}
still_open = [k for k in open_before if k in dpos]
print(f"2. the {len(open_before)} positions open before = {len(at_resume)} written off at the resume + {len(still_open)} still open "
      f"+ {len(lost_now)} restart_lost (+ {len(open_before) - len(at_resume) - len(still_open) - len(lost_now)} closed otherwise); "
      f"written_off_at_resume counted {n_wo}; not as specified {len(bad2)} {bad2[:2]}")
ok2 = len(at_resume) == n_wo and not bad2 and not lost_now
k3 = [x for x in at_resume if x["family"] == "F7" and x["variant"] == "tp4_T600"]
s = sum(x["pnl"] for x in k3)
hl = hwm_state["groups"]["K3"]["day_loss"]
sig = db.execute("SELECT status, reason, t FROM signals WHERE family = 'F7' AND variant = 'tp4_T600' AND t > ? AND day = ? ORDER BY t LIMIT 1",
                 (rd["resumed_at"], rd["day"])).fetchone()
print(f"3. K3: {len(k3)} written off, their P&L {s / 1e9:.3f} SOL on {rd['day']}; the HWM's K3 day loss {hl / 1e9:.3f}; together "
      f"{(hl + s) / 1e9:.3f} (limit 1 SOL); K3's first signal after the resume that day: {tuple(sig) if sig else None}")
ok3 = all(x["day"] == rd["day"] for x in k3) and (sig is None or (hl + s) > -1e9 or (sig[0], sig[1]) == ("skipped", "daily loss limit"))
print("rehearsal: " + ("GREEN" if ok1 and ok2 and ok3 else f"RED (check 1 {ok1}, 2 {ok2}, 3 {ok3})"))
sys.exit(0 if ok1 and ok2 and ok3 else 1)
EOF
tail -6 "$R/checks.log"
[ "$rc" = 0 ] || exit 1
