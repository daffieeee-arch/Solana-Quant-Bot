# Triton cost-safety architecture (ONTWERP — niet geïmplementeerd, geen top-up)

## 1. Zero-cost mode (geïmplementeerd 2026-08-15)
- `TRITON_LIVE_ENABLED=false` (default) — géén Dragon's Mouth subscriptions, Titan,
  of Triton RPC. Bot draait OFFLINE (dashboard/ledger/scanner-logica). Triton-code
  blijft aanwezig; reactivatie = expliciete env unlock.
- Zie commit `54f637d` (main.ts + deploy-script).

## 2. Toekomstige reactivatie cost-safety (ontwerp vóór élke top-up)
- TRITON_LIVE_ENABLED=false standaard in development; expliciete manual unlock
- Maximum test duration (hard timer)
- RPC counters per method + bytes received/sent per service waar meetbaar
- Dragon's Mouth bytes/events; Titan bytes/requests
- Daily/test budget estimate + warning threshold + hard stop threshold
- Automatic disconnect bij budgetlimiet
- Grafana metrics/alerts
- Top-up activeert live consumers NOOIT automatisch (env-only unlock)

## 3. Cost postmortem (alleen lokale logs/counters/config)
Classificatie: PROVEN / ESTIMATED / UNKNOWN

| item | value | status |
|---|---|---|
| Triton prepaid balance start (eerder geconstateerd) | $125 → $0 | PROVEN (account-bericht) |
| Triton RPC calls (jsonRpcCalls in bot-debug) | 0 recent; eerder ~1.836/uur (getAccountInfo-dominant) | ESTIMATED (uit lokale usage-counters) |
| DAS calls (getAsset) | ~134-183/h eerder | ESTIMATED |
| Dragon's Mouth runtime | 9 subs pending; 0 bytes bewezen na cutoff | UNKNOWN (bytes niet gemeten) |
| Titan runtime | ~0 verdiend in observatie | UNKNOWN |
| Reconnect/subscriptions | 9 actieve program-filters eerder | PROVEN (config-key) |
| Old Faithful → Jetstreamer → ClickHouse | BACKFILL (gepauzeerd) | NOT Triton PAYG-cost (geen bewijs) |

**BELANGRIJK**: $125-attributie blijft afhankelijk van Triton Billable Items/account-
portal. Old Faithful publieke archive + lokale ClickHouse wordt NIET als Triton
PAYG-cost geklasseerd zonder billingbewijs.

## 4. Reactivatie-voorwaarden (absolute gates)
1. Prepaid balance > $0 (Billable Items-portal bevestigd)
2. TRITON_LIVE_ENABLED=true expliciet en gemeten max-duration timer
3. Gratis-plane test op nieuw/lager endpoint óf endpoint-cut-off bevestigd
4. Budget- en stopp-thresholds actief (metrics/alerts)
5. first-event bewezen op slot-subscription met minimale bytes
6. Cost-metering per methode/byte actief vóór élke live prestatietest