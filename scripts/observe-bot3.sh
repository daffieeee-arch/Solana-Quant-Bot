#!/usr/bin/env bash
# Observeer solana-bot 15 min: sample de verse containerlog en schrijf een eindanalyse.
set -e
echo $$ > /tmp/observe3.pid
END=$(( $(date +%s) + 900 ))
i=0
while [ "$(date +%s)" -lt "$END" ]; do
  i=$((i+1))
  sleep 95
  cd /opt/data/solana-paper-scanner/scripts
  dl=$(node /tmp/cd_obs3.mjs 2>/dev/null | grep -oE '/_download/[0-9]+\?auth_token=[^"]+' | head -1)
  [ -n "$dl" ] && curl -sk --max-time 30 -o "/tmp/obs3-sample-$i.json" "https://192.168.1.234$dl" 2>/dev/null || true
done
cd /opt/data/solana-paper-scanner/scripts
dl=$(node /tmp/cd_obs3.mjs 2>/dev/null | grep -oE '/_download/[0-9]+\?auth_token=[^"]+' | head -1)
[ -n "$dl" ] && curl -sk --max-time 30 -o /tmp/obs3-final.json "https://192.168.1.234$dl" 2>/dev/null || true
echo "klaar samples=$i" > /tmp/observe3-done.txt
