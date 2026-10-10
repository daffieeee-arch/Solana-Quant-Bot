# Tournament v1.1 (dev, epochs 1040–1046), 2026-10-10

Run `v1.1-dev-1040-1046`, engine `60c415f` (frozen copy), store `dev2.duckdb` (store v2, built from `60c415f`).
Full report: `data-old-faithful-one/lab/backtests/v1.1-dev-1040-1046/report.md`.

## What changed since v1

- **Boost cranks are applied once.** In v1 every crank counted twice, which pushed pool prices up in our favour.
- **All SOL-market pools are in the store**, not only pools created in the period. That is 68,170 normal pools, plus 28,063 "reversed" pools (base WSOL, quote = the token).
  - The 4,842 pools with a real non-SOL quote are excluded and counted.
  - Pools are classified offline from the protocol fee account, with 0 disagreements on 36,319 pools that have a create event.
- **Pool positions:**
  - sells are capped by the real vault;
  - liquidity withdrawals and deposits are replayed;
  - pool post-states come from the logged amounts.
- **Windows are in milliseconds**, converted per epoch (all of 1040–1046 is ~267 ms per slot).
- **Data:** 9.3 days instead of 6.7 days. The F7 pool universe now includes pools of any age.

1.17M → 2.50M signals; 18.8M positions; 3 h 30 min on 2 cores.

## Engine sanity

N2 loses roughly the round-trip cost beyond drift. These are medians of return minus drift (base costs, d=1); means are distorted by a few extreme historical price moves.

| N2 control | 0.5 SOL | 10 SOL |
|---|---|---|
| curve (F7) | −2.7 pts | −4.5 pts |
| curve (F1) | −3.0 | −14.1 |
| normal pools | −2.6 | −2.4 |
| reversed pools | −1.1 | −7.9 |

Deep normal pools carry 10 SOL cheaply. Reversed pools have low fee tiers but thin books at size.

## Results (base costs; range over the variants of each family)

| family / segment | d | 0.5 SOL | 2 SOL | 5 SOL | 10 SOL | 25 SOL |
|---|---|---|---|---|---|---|
| F7 curve | 1 | −4.3 … −4.0 % | −5.8 … −5.1 | −8.7 … −7.6 | −11.4 … −10.2 | −14.2 … −13.1 |
| F7 curve | 2 | −4.4 … −3.9 | −5.9 … −5.2 | −8.9 … −7.8 | −11.9 … −10.6 | −14.9 … −13.6 |
| F7 normal pools | 1 | −5.2 … −4.7 | −5.8 … −5.1 | −7.0 … −6.0 | −8.8 … −7.5 | −12.3 … −10.0 |
| F7 normal pools | 2 | −8.3 … −7.4 | −8.8 … −7.8 | −10.2 … −8.8 | −12.3 … −10.3 | −16.3 … −13.2 |
| F7 reversed pools | 1 | **+0.4 … +0.7** | −1.1 … −0.6 | −4.2 … −2.8 | −7.7 … −6.1 | −10.9 … −8.9 |
| F7 reversed pools | 2 | −0.1 … +0.1 | −1.6 … −1.2 | −4.4 … −3.4 | −7.7 … −6.3 | −11.3 … −9.1 |
| F1 curve (B40/B55/B70) | 1 | −5.0 … +0.9 | −8.2 … −1.9 | −14.7 … −7.5 | −24.3 … −14.0 | −42.7 … −3.7 |
| N1 (H150/H1500 mean) | 1 | −6.2 | −7.7 | −10.1 | −12.6 | −16.5 |

**Stop-rule measure:**
- STOP for every variant at every size of 2 SOL and up, in every segment.
- The only GO verdicts are the 9 F7 variants in reversed pools at **0.5 SOL with d = 1**: +0.4 … +0.7% per trade, day-CI above 0 (e.g. +0.49 … +0.92%), about 12k trades each. That is informative only, below the live size.
  - With d = 2 the same trades give ≈ 0 (ADJUST).
  - From 2 SOL on they lose.
- F1 B55 at 0.5 SOL: +0.9% (CI −3.1 … +4.9), ADJUST.

**Edge vs N2:**
- F7 curve: +1.2 … +1.8 pts at 0.5 SOL for both d; it turns negative from 10 SOL.
- F7 normal pools: negative everywhere.
- F7 reversed pools: +2.8 … +6.9 pts at every size, CI above 0. Their random control loses more there, from impact.

**Batch-1 gate** (edge > 0 at ≥ 5 SOL for d = 1 and d = 2):
- Formally passed by F1 B55/B70, F7 tp4_T2400 (curve) and 8 F7 variants in reversed pools.
- All of them lose 2.8–43% per trade at those sizes. As in v1: **nothing passes** in absolute terms.

## The reversed-pool result needs the farming split before anyone trusts it

- In reversed pools, F7 takes profit on 63% of trades at 0.5 SOL (normal pools: 40%). The median historical move after the trigger is +6%.
- The pattern is spread over 1,330 pools, not a few.
- These pools look like volume farming: the top pools have ~70k trades of ~7 SOL from ~5k wallets in 16 h, almost all direct PumpSwap calls.
- A plausible mechanism: a farmer's large sell is followed by its own buy-back, and F7 sits in between.
  - Whether that holds up depends on the farmers' slippage limits, which the replay applies for direct calls, and on latency. The edge is gone at d = 2.
- The coordinator's rule: an edge that exists only in farming-flagged pools does not count.
- Store v2 now computes the farmer flag per trade (24 h look-back per wallet and pool). F7 in reversed pools will be re-run with the organic / flagged split once the store is rebuilt.

## Conclusion

- No strategy is profitable after costs at the live size; the v1 conclusion stands with the boost fix and all pools.
- The only positive result is small (0.5 SOL), needs d = 1 and sits in pools that look farmed. It is a lead to test with the farming split, not a candidate.
