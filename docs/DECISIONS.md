# DECISIONS.md — Belangrijke architectuurbeslissingen

*Korte rationale per keuze. Zie git-history voor uitgebreide context.*

## 1. TRITON-ONLY scope
**Keuze**: alle marktdata via Triton (Dragon's Mouth/geyser, DAS, Titan). Helius/Birdeye verboden.
**Rationale**: één contract-partner, minder surface area, Triton levert alles wat nodig is.

## 2. Zero-cost default (TRITON_LIVE_ENABLED=false)
**Keuze**: live Triton-consumptie standaard uit; expliciete env-unlock nodig.
**Rationale**: Triton prepaid balance = $0 → een top-up mag live consumers NOOIT automatisch starten; fail-closed voorkomt onbedoelde kosten.

## 3. Geen live top-up zonder budget-safeguards
**Keuze**: top-up niet gedaan; reactivatie alleen met max-duration, per-methode counters, warning/hard-stop.
**Rationale**: cost-attributie onbekend, reeds $125 verbruikt; moet meetbaar en gebound zijn vóór live-test.

## 4. Pump-only bewezen baseline
**Keuze**: enkel Pump.fun als SUPPORTED_AND_TESTED; andere protocollen fail-closed (IDENTITY_INCOMPLETE/NOT_YET_SUPPORTED).
**Rationale**: bewezen correctness per protocol vereist canonical pool-identity + fixtures; alles tegelijk = onbeheersbaar risico.

## 5. Fail-closed MarketIdentity
**Keuze**: geen entry zonder complete canonical identity; `gx:<mint>`-only → reject; onbekende discriminator/curve/PDA → undefined.
**Rationale**: voorkomen dat onprijsbare/verkeerde posities de paper-strategie vervuilen.

## 6. WAL/ledger als authoritative state
**Keuze**: crash-safe append-only journal = single source of truth; quarantine als WAL-event `position_quarantined`.
**Rationale**: replay/herstart consistent; geen sidecar-drift (eerdere sidecar-issues gecorrigeerd).

## 7. Git/GitHub als source-control
**Keuze**: private GitHub-repo (`daffieeee-arch/solana-paper-scanner`), deploy-key SSH, immutable tags.
**Rationale**: off-site backup, reproduceerbaarheid, provenance (runtime Git-SHA).

## 8. ClickHouse historische rol
**Keuze**: ClickHouse = historische swaps/backtest-data; niet live-bot-bron; read-only MCP (hermes_ro).
**Rationale**: scheiding operatie/historie; zuinige credits.

## 9. Grafana observability
**Keuze**: Grafana voor metrics/alerts; geen live Triton-cost-metrics zonder budget-safeguards.
**Rationale**: observeerbaarheid zonder onbedoelde kosten.

## 10. v1 TRANSACTION_NET_SWAP vs v2 event-level
**Keuze**: v1 (max 1 rij per tx, signature-ORDER BY) is de huidige backfill; v2 (Bronze/Silver/Gold, event-level + Net) = ontwerp voor later.
**Rationale**: v1 geschikt voor contract/backtest-breed; multi-event/multi-hop-informatie ontbreekt — v2 bewaart volledige provenance.

## 11. Pump-discriminatoren: officiële IDL + observed dispatchers
**Keuze**: officiële IDL-varianten (SHA256("global:<name>")) én live-geobserveerde dispatcher-bytes; observed = PROVEN (sell) / EXPERIMENTAL (rest).
**Rationale**: live binary dispatcht op custom dispatcher → beide sets nodig; experimental-claims niet overstatement.

## 12. Offline-first development
**Keuze**: alle parser/identity/accounting-wijzigingen offline testbaar (mocks/fixtures, geen netwerk).
**Rationale**: determinisme; versnelt correctheid; bewijsbaar netwerkloos (fetch-spy=0).