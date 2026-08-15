# Decimals-source matrix + cost-safety matrix (OFFLINE hardening, 2026-08-15)

## 1. Decimals source matrix

Classificatie: EVENT_DECODED / LOCAL_CACHE / STREAM_STATE / RPC_RESOLVED /
HARDCODED_DEFAULT / UNKNOWN

| Decimals pad | Bron in code | Technisch aanwezig | Getest | Toegestaan voor ENFORCE |
|---|---|---|---|---|
| genericLaunch.baseDecimalsForPrice (pump via Vixen) | triton.ts L489/L681 (dec = baseDecimalsForPrice ?? 6) | EVENT_DECODED (uit event als aanwezig) | ja (parser-fixtures) | ja — alleen als daadwerkelijk in event |
| poolDepth.baseDecimals/quoteDecimals (klassieke pump/AMM/CPMM) | triton.ts L539/L593/L638 | RPC_RESOLVED (uit reserve-reader/RPC) | mock-only (zero-cost) | ja (met mock-testbewijs) |
| Fallback 6/9 (pump/AMM/CPMM) | triton.ts `?? 6`/`?? 9` | HARDCODED_DEFAULT | nee | NEE — fail-closed in ENFORCE |
| Fallback quote 9 (generic) | triton.ts L689 quoteDecimals: 9 | HARDCODED_DEFAULT | nee | NEE |
| decimals-resolver (event→cache→RPC) | src/decimals-resolver.ts | STREAM_STATE/LOCAL_CACHE/RPC_RESOLVED per laag | mock-only | ja (moet ge-wired) |

**Beleid ENFORCE (toekomst)**: alleen EVENT_DECODED / LOCAL_CACHE / STREAM_STATE /
RPC_RESOLVED (bewezen per identity) mogen door de entry-gate. HARDCODED_DEFAULT en
UNKNOWN → fail-closed reject (geen entry).

## 2. Cost-safety matrix

| Capability | Status | Bewijs |
|---|---|---|
| Live default OFF (TRITON_LIVE_ENABLED default false) | IMPLEMENTED_AND_TESTED | main.ts + zero-cost.ts + zero-cost.test (9) |
| Manual unlock (expliciete env=true) | IMPLEMENTED_AND_TESTED | isTritonLiveEnabled strict parse ('true' case-ins) |
| Max test duration | NOT_IMPLEMENTED | ontwerp in triton-cost-safety.md §2 |
| Request counters per method | NOT_IMPLEMENTED (usage-counters bestaan alleen in debug) | ontwerp §2 |
| Bytes per service | NOT_IMPLEMENTED | ontwerp §2 |
| Cost estimator | NOT_IMPLEMENTED | ontwerp §2 |
| Warning threshold | NOT_IMPLEMENTED | ontwerp §2 |
| Hard stop threshold | NOT_IMPLEMENTED | ontwerp §2 |
| Automatic disconnect bij budgetlimiet | NOT_IMPLEMENTED | ontwerp §2 |
| Grafana metrics/alerts | NOT_IMPLEMENTED (Grafana draait, geen CH/Triton-metrics) | ontwerp §2 |

**Deze ronde**: alleen de noodzakelijke zero-cost-guards geïmplementeerd en getest;
geen grote nieuwe observability infra (bewust, conform scope).

## 3. Zero-cost guards (implemented)

- `requireLiveTritonOrThrow()` op de centrale clientconstructie-grens (geyser/vixen-
  factory, TritonProvider, TitanQuoteProvider, reserve-reader) — gooit in offline mode
- `isTritonLiveEnabled`: strict — alleen exact 'true' (case-ins) → live; alles anders offline
- Status: `offline: "OFFLINE_ZERO_COST"` in /api/status; `providerHealth[0].status =
  "DISABLED_OFFLINE_ZERO_COST"` (nooit ok/degraded/down in offline)
- Top-up activeert live consumers NOOIT automatisch (env-only unlock)