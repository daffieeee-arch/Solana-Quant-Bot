# KNOWN_ISSUES.md — Open issues & HOLDs

*Alleen actuele open punten. Resolved zaken zijn niet opgenomen.*

## Triton-live
1. **Balance $0** — live Triton-live reactivatie geblokkeerd; endpoint cut off (vermoedelijk prepaid-cutoff; technisch UNCONFIRMED tot live-test)
2. **Cost-attributie onbekend** — $125-verbruik niet gelinkt aan Billable Items-portal; alleen lokale counters (ESTIMATED/UNKNOWN)
3. **Live Pump connectivity** — na een eventuele top-up moet first-event opnieuw bewezen worden (subs/pending 0-events-issue)

## Pump-parser
4. **Observed dispatchers deels EXPERIMENTAL** — liveBuy/liveBuyV2/liveBuyExactSolIn hebben geen primaire in-repo mainnet-txn; alleen structurele keten accepteert ze (fail-closed bij onvoldoende bewijs)
5. **Live binary gebruikt custom dispatcher** (niet sha256("global:<name>")) — de officiële IDL beschrijft de live binary niet volledig; bij reactivatie opnieuw verifiëren

## Protocol-identities
6. **Non-Pump identities incompleet** — AMMv4/CPMM canonical pool-state (≠lpMint), CLMM tick/vault, Meteora/Orca/Moonshot/Jupiter: decoder-uitbreiding nodig (separate ronde)

## Entry-gate
7. **MarketIdentity ENFORCEMENT UIT** — bewust; shadow-only tot live-connectiviteit + shadow-venster bewijst dat niet alles wordt afgewezen

## ClickHouse / TrueNAS
8. **ClickHouse autostart na NAS-reboot niet structureel opgelost** — draait als los proces; geen TrueNAS-app/auto-start (handmatige start nodig na reboot)
9. **ClickHouse default-user/LAN exposure** — `default` zonder wachtwoord, HTTP 8123 bereikbaar vanaf LAN; staged hardening pending (niet uitgevoerd tijdens deze fasen)
10. **ClickHouse MCP least-privilege user aangemaakt** (hermes_ro) — maar default-user-exposure blijft open; hardening-plan in oudere docs

## Backfill
11. **Backfill gepauzeerd** (supervisors + cron) — resume-gates nog niet doorlopen; bewuste pauze
12. **Completion-logica foutief** (390k-drempel op count i.p.v. max-slot+cursor) — open fix-ontwerp
13. **Stall-watchdog false-positives** — heartbeat/source-cursor ontwerp nodig (WRITING/SEEKING/NETWORK_RETRY/STALLED/COMPLETE)
14. **Repair-cron kan dubbele supervisors starten** + pad-fix nodig; jamlocatie-status-files onbetrouwbaar

## Data/schema
15. **v1 duplicate-ratio ~3,5% (cross-part retry overlap)** — niet gededupliceerd (geen OPTIMIZE/FINAL); v2 event-level backfill = ontwerp (Bronze/Silver/Gold)
16. **v1 slechts TRANSACTION_NET_SWAP** — multi-hop/inner-CPI/pool-detail ontbreekt; v2 nodig voor event-level

## Overig
17. **Grafana live Triton-cost-metrics ontbreken** (budget-safeguards-leeg) — NOT_IMPLEMENTED
18. **Cost-safety**: max-test-duration, warning/hard-stop-thresholds, automatic-disconnect = DOCUMENTED_ONLY — nog niet geïmplementeerd