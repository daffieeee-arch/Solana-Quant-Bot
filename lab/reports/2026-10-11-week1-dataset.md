# Week 1: pump.fun + PumpSwap event dataset from Old Faithful, 2026-10-11

The checkpoint for week 1 (issue #159): every pump.fun and PumpSwap event of epochs 1033–1051, decoded from Old Faithful with the lab extractor (Jetstreamer plugin, official IDL), checked for continuity and summarized.

Data and evidence, outside Git, under `data-old-faithful-one/lab/`:
- `events/v1/chunks/`: one directory per half epoch, Parquet per event type plus `_manifest.json`;
- `reports/continuity-446256000-451008000.json` (1033–1043) and `reports/continuity-451008000-454464000.json` (1044–1051);
- `reports/summary-2026-10-11.json`;
- `store/notes/dev2-validation-2026-10-11.md` (the backtest store built from the chunks).

## Dataset

- **Range.** 38 complete chunks, epochs 1033–1051: slots 446,256,000–454,464,000 (8.2M slots), block times 2026-09-11 23:12 to 2026-10-08 08:28 UTC, about 26.4 days.
  - Development period: 1033–1046.
  - Hold-out: 1047–1051. It is extracted, but no backtest has read it.
- **Coverage.**
  - Every chunk is `complete` and passed the coverage audit: 0 missing slots, 0 anomalies, 0 sink errors.
  - 327 HTTP 429 responses and 644 firehose errors were retried by the extractor.
- **Volume.**
  - 717M decoded events in 1.95B transactions that touch the two programs, plus 823M failed transactions.
  - 193.5 GB of Parquet.
  - Extraction took a median of 38 minutes per chunk with 6 threads. Four chunks took longer: three took 3–4 h because of slower downloads, and 446904000 shows 12.6 h because it was paused during a disk write stall on 10 October.
- **Extractor builds.** Four builds over the weeks (`3b8b45ff` 14 chunks, `0cfc4db2` 6, `71db26c3` 2, `eac48884` 16). Later builds add columns; readers treat a missing column as NULL.
- **Slot time.** Epochs 1033–1036 have about 316 ms per slot, 1037 onwards about 267 ms. The backtest converts every time window per epoch (`meta.ms_per_slot`).

## Continuity

Per mint and per pool, in chain order, the state after event n must equal the state before event n+1 (method in `lab/checks/continuity.py`).

| range | chunks | pump pairs | pump bad | PumpSwap pairs | bad (base) | bad (base + effective quote) | unverifiable | duplicates |
|---|---|---|---|---|---|---|---|---|
| 1033–1043 | 22 | 52,928,517 | 0 | 355,011,161 | 100 | 182 | 471,968 | 0 |
| 1044–1051 | 16 | 48,322,537 | 0 | 221,925,742 | 36 | 51 | 376,544 | 0 |

The PumpSwap rows count base reserves, and base plus effective quote (vault plus virtual quote).

- **pump.fun.** Every verifiable pair matches. The virtual quote matches as well, apart from pairs that involve the Mayhem agent (40.6M ok, 0 bad on 1033–1043).
  - Chains on 1033–1043: 632,904 mints, 526,439 of them anchored by their create event.
- **PumpSwap.**
  - 99.99995% of verifiable pairs match on base and effective quote.
  - The 282 bad pairs (both ranges) are spread over the chunks: 0 to 29 per chunk, almost all between two trades. That points to rare single events, not to a lost block range.
  - Unverifiable pairs involve an event without a full pre-state: boost buy-and-burn exposes only its post-state, and deposits and withdrawals only their base reserves.
- **Method change this week.** The check now reads each chunk straight from Parquet and carries the last event of every chain across chunk boundaries, instead of first writing all events to an on-disk database (tens of GB of writes).
  - On two mini-chunks around the 1033b/1034a boundary, the new and old versions give identical reports in every field.

## Summary

From `summary.py --bounded`. Counts are exact; quantiles are t-digest estimates. Rows are not de-duplicated, because continuity found 0 duplicate events.

| | |
|---|---|
| pump.fun trades | 101.45M (53.4M buys), 8.48M with a non-SOL quote, 26.6M by Mayhem-mode tokens |
| tokens | 1.06M created by 398k creators; 1.14M traded; 31,445 graduations |
| traders (curve) | 1.75M wallets |
| SOL volume on the curve | 30.65M SOL |
| trades per token | median 12, p90 193, p99 1,379 |
| PumpSwap | 257.4M buys and 318.9M sells in 189,914 pools |
| failed transactions | 823.45M, paying 30,382 SOL in fees (mostly program errors) |
| costs per curve trade | priority fee median 0.000025 SOL, p90 0.00048 SOL; 10.8% pay a Jito tip (median 0.00001 SOL); 24.6% are v1 transactions |

## Backtest store (development, 1033–1046)

- **Build.**
  - Built from the chunks on 2026-10-11 (`store.py`, 53 min): 42.3 GB.
  - 707k mints, 69.5M curve events, 155,867 PumpSwap pools (95,495 SOL-quoted, 54,027 reversed with WSOL as base, 6,345 other), 425.6M pool events.
- **Validation: PASS.**
  - Epochs 1040–1046 match the previous store: the same blocks, the same curve events of mints created in that period, and pool events +2 in 1040.
  - Tables are sorted per insert.
  - All 47 backtest and paper tests pass on it, including the exact replay of reversed-pool fills.
- **Disk writes.**
  - A first attempt wrote 386 MB/s of DuckDB spill churn to disk and was stopped.
  - With 16 pool slices and spill in RAM (capped at 2 GB), the build writes only the store itself: about 1.7 GB per chunk, 42.4 GB in total.
  - A per-chunk write limit (3.5 GB) and a disk health gate now guard every chunk.

## Week-1 goals

| goal | status |
|---|---|
| one full epoch extracted, coverage `complete`, continuity rate published | done (since 2026-10-09; now 19 epochs) |
| 2–4 weeks of recent history | done: 26.4 days (1033–1051) |
| week-1 report in `lab/reports/` | this file |

Cost: Old Faithful files only. No paid Triton service was used ($0).

## Open points

- **Epoch 1052.** It is extracted next with the account columns of the newest extractor, for the mechanical replay validation after the hold-out. It runs after the families v2 tournament (the shared heavy-job lock).
- **Bad PumpSwap pairs.** The 282 pairs are not explained one by one. At about 5 per 10M pairs they do not affect backtests. The replay checks its own fills against the recorded events.
- **VPS limits** (netcup RS 2000 G12).
  - The network is throttled to 300 Mbit/s after 3 TB per 24 h.
  - Disk writes stalled twice (cause not known).
  - Heavy jobs now keep their spill in RAM and pass the disk health gate.
