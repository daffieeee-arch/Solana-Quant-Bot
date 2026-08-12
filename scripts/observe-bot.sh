#!/usr/bin/env bash
# Observeer solana-bot 15 min: sample + eindanalyse. Gebruikt de werkende core.download nadering.
set -e
PIDFILE=/tmp/observe-bot.pid
echo $$ > $PIDFILE
SCRIPTS=/opt/data/solana-paper-scanner/scripts

# helper: ophaal verse log via de cd2-client (die een download-token print en curl downloadt)
fetch_log() {
  local outfile="$1"
  cd "$SCRIPTS"
  local dl
  dl=$(node /tmp/cd_bot.mjs 2>/dev/null | grep -oE '/_download/[0-9]+\?auth_token=[^"]+' | head -1)
  [ -n "$dl" ] && curl -sk --max-time 30 -o "$outfile" "https://192.168.1.234$dl"
}

END=$(( $(date +%s) + 900 ))   # 15 min
i=0
while [ "$(date +%s)" -lt "$END" ]; do
  i=$((i+1))
  sleep 90
  # lichte sample: alleen tellen we het aantal scans in een verse pull; bewaar de laatste
  fetch_log /tmp/observe-sample.json 2>/dev/null || true
done

# finale analyse
fetch_log /tmp/observe-final.json 2>/dev/null || true
echo "klaar sample-$i" > /tmp/observe-done.txt
