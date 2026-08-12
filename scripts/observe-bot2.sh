#!/usr/bin/env bash
# Observeer solana-bot 15 min: sample + eindanalyse met de juiste container-client.
set -e
echo $$ > /tmp/observe2.pid
END=$(( $(date +%s) + 900 ))
i=0
while [ "$(date +%s)" -lt "$END" ]; do
  i=$((i+1))
  sleep 90
  # oude sample regularmente + lit eind
  cd /opt/data/solana-paper-scanner/scripts
  dl=$(node /tmp/cd_obs.mjs 2>/dev/null | grep -oE '/_download/[0-9]+\?auth_token=[^"]+' | head -1)
  [ -n "$dl" ] && curl -sk --max-time 30 -o "/tmp/obs-sample-$i.json" "https://192.168.1.234$dl" 2>/dev/null || true
done
# finale
cd /opt/data/solana-paper-scanner/scripts
dl=$(node /tmp/cd_obs.mjs 2>/dev/null | grep -oE '/_download/[0-9]+\?auth_token=[^"]+' | head -1)
[ -n "$dl" ] && curl -sk --max-time 30 -o /tmp/obs-final2.json "https://192.168.1.234$dl" 2>/dev/null || true
echo "klaar samples=$i" > /tmp/observe2-done.txt
