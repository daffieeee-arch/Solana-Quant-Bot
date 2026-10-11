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

Per mint and per pool, in chain order, the state after event n must equal the state before event n+1 (method in `lab/checks/continuity.py`). One run covers all 38 chunks and also checks the pairs across chunk boundaries: `reports/continuity-446256000-454464000.json`.

| range | chunks | pump pairs | pump bad | PumpSwap pairs | bad (base) | bad (base + effective quote) | unverifiable | duplicates |
|---|---|---|---|---|---|---|---|---|
| **1033–1051** | 38 | 101,295,054 | 0 | 576,992,719 | 136 | 233 | 849,580 | 0 |
| 1033–1043 | 22 | 52,928,517 | 0 | 355,011,161 | 100 | 182 | 471,968 | 0 |
| 1044–1051 | 16 | 48,322,537 | 0 | 221,925,742 | 36 | 51 | 376,544 | 0 |

The PumpSwap columns count base reserves, and base plus effective quote (vault plus virtual quote).

- **pump.fun.** Every verifiable pair matches. The virtual quote matches as well, apart from pairs that involve the Mayhem agent.
  - 1,215,109 chains, 1,059,206 of them anchored by their create event (every create in the data).
- **PumpSwap.**
  - 99.99996% of verifiable pairs match on base and effective quote.
  - Unverifiable pairs involve an event without a full pre-state: boost buy-and-burn exposes only its post-state, and deposits and withdrawals only their base reserves.
  - Every bad pair is explained or marked open below.
- **Method change this week.** The check now reads each chunk straight from Parquet and carries the last event of every chain across chunk boundaries, instead of first writing all events to an on-disk database (tens of GB of writes).
  - On two mini-chunks around the 1033b/1034a boundary, the new and old versions give identical reports in every field.

### The 233 bad PumpSwap pairs

Every bad pair is dumped (`reports/continuity-bad-pairs-446256000-454464000.parquet`, from `continuity.py --dump-bad`) and split by what changed between the post-state of event n and the pre-state of event n+1. The table is reproduced by `lab/checks/bad_pairs.py <dump> --store store/dev2.duckdb` (output: `reports/continuity-bad-pairs-by-cause.md`).
- dB is base reserves in raw units; dE is effective quote. In SOL pools dE is lamports.
- Pool class comes from the development store:
  - **sol** and **reversed** are the backtest universe;
  - **other** pools have a quote that is not SOL and are not traded in backtests;
  - **hold-out only** pools first trade after 1046.

| cause | pairs | sol | reversed | other | hold-out only | dB | dE | median slot gap | example (pool, slot, events) | status |
|---|---|---|---|---|---|---|---|---|---|---|
| A. base up by 1–4 raw units, E unchanged | 107 | 98 | 0 | 0 | 9 | +1 … +4 | 0 | 155 | `uvfLdQVy…`, 451505800, sell ← buy in the same slot | open: consistent with dust transfers to the base vault |
| B. base up by more than 10^6 raw units, E unchanged | 29 | 14 | 0 | 6 | 9 | +1,676,767 … +3.2·10^14 | 0 | 427 | `DreMAMRc…`, 453107318, sell ← sell; the same +1,676,767 recurs in other pools | open: consistent with token transfers to the base vault |
| C. effective quote up, base unchanged | 97 | 2 | 1 | 93 | 1 | 0 | +23 … +2.5·10^11 | 369 | `5axdrzk8…` (other), 447596763, sell ← sell | open: in SOL pools +0.40 and +1.67 SOL; 58 of the 93 "other" pairs are in 3 pools whose quote mints are `Xsa62P5m…`, `XsfCC9VL…` and `pumpCmXq…` |

What the pattern shows:
- **No program action can cause these mismatches.**
  - Every mismatch is an increase on exactly one side; none is a decrease, and none moves base and E together.
  - A missed swap, deposit or withdrawal changes both sides, in opposite directions for a swap. So these are not lost or mis-decoded program events.
  - All 233 pairs are between two trades (or a boost and a trade). None involves a deposit or withdrawal.
  - Only 5 are in the same slot, and none in the same transaction.
- **Backtest universe.**
  - In the development store's SOL markets (sol and reversed, 1033–1046 excluding hold-out-only pools), there are 115 bad pairs, 98 of them base dust (1–4 raw token units).
  - The 93 quote-side cases sit almost all in non-SOL pools. In SOL pools, 2 pairs saw the SOL side rise without an event: +0.40 and +1.67 SOL.
  - 22 pairs fall in the hold-out epochs (1047–1051), checked mechanically only, with no P&L.
- **Why they stay open.** A plain token or SOL transfer into a pool vault touches neither program, so it is not in this dataset; it only holds transactions that touch the pump programs.
  - Non-SOL quote mints can also change balances on their own (for example Token-2022 extensions). That is not checked either.
  - To close these causes, the transactions that write to the pools' vault accounts (the pool's associated token accounts) in the slot windows must be read from Old Faithful.
  - Planned: a targeted scan of 5 pairs per cause with short gaps, with the extractor run on those slots.
  - Until then, A–C are "consistent with" and stay open.
- **Effect on backtests.** The replay takes the pool state from each event's recorded pre-state, so a change between two events shows up in the next fill as it was on chain. The dust in A is at most 4 raw token units.

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
- **Bad PumpSwap pairs.** The 233 pairs are classified, but causes A–C stay open until the targeted Old Faithful scan of vault transfers, described under Continuity, has run.
- **VPS limits** (netcup RS 2000 G12).
  - The network is throttled to 300 Mbit/s after 3 TB per 24 h.
  - Disk writes stalled twice (cause not known).
  - Heavy jobs now keep their spill in RAM and pass the disk health gate.
