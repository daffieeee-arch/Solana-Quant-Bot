# Analyst reference: lab development data

Query with `lab/bin/q` (read-only, SELECT only, 500 rows, 5 min, 1 GB). Tables live in the attached
`dev` database (the default schema); `analytics.duckdb`, once built, is attached as `a`.

## Window

- Development data only: slots `450,144,000 … 452,303,999` (epochs 1042–1046, 2026-09-24 21:00 → 2026-10-01 13:40 UTC).
  The backfill target is epoch 1033; check `SELECT min(slot), max(slot) FROM blocks`.
- Hold-out: slots `452,304,000 … 454,463,999` (epochs 1047–1051). Never queried here. Fixed protocol markers may
  appear as labels only: first fee sweep in chunk `452,520,000–452,736,000`, first v3 curve trades at slot `453,800,004`.
- Epoch = `slot // 432000`. Time = `to_timestamp(blocks.block_time)::TIMESTAMP` (UTC; ~0.267 s per slot here).
- Chain order is `(slot, tx_index, outer_ix, inner_ix)`.

## Units

| Quantity | Raw unit | Divide by |
|---|---|---|
| SOL / wSOL amounts, reserves, fees | lamports | 1e9 |
| pump.fun token amounts, reserves | raw token | 1e6 (6 decimals) |
| fees in bps | basis points | 1e4 |

Quote amounts are in the token's quote mint. Not every token is SOL-quoted (USDC and others exist), so filter
on `quote_mint` before summing SOL.

## Tables (`dev`)

- `mints` — one row per token created in the window: `create_slot`, `creator`, `mayhem`, `quote_mint`, `cashback`,
  `holder_reward`, `name`, `symbol` (creator-supplied text: escape, never render as HTML), `complete_slot`
  (curve full), `migrate_slot`, `pool`.
- `curve` — bonding-curve trades. `t`, `q` = token and quote amount of the trade (quote net of fees);
  `fee`, `creator_fee` charged on top; `vt`, `vq`, `rt`, `rq` = virtual/real reserves **after** the trade;
  `variant` from the instruction (`buy`, `sell`, `buy_v2`, `sell_v2`, `buy_exact_sol_in`, `buy_exact_quote_in_v2`);
  `trader` = user, `arg_*` = slippage arguments (epochs ≤ 1043 only).
- `pool` — PumpSwap events for pools created in the window. `kind` ∈ `buy`, `sell`, `boost` (a boost row repeats
  its buy; exclude it from volume). `b` = base reserve and `e` = Q_eff **before** the trade; `base`, `quote_gross`,
  `quote_net`; fee bps `lp_bps`, `protocol_bps`, `creator_bps`; `limit_quote`, `limit_base` = slippage limits.
- `pools` — one row per pool: `mint`, `quote_mint`, `create_slot`, `coin_creator`, `mayhem`, initial `b0`, `q0`.
- `blocks` — `slot`, `block_time` (unix seconds). Missing slots were skipped by the leader.

## Prices

- Curve: price (quote per token) = `(vq / 1e9) / (vt / 1e6)` on the post-trade reserves. Market cap in SOL =
  price × supply / 1e6 (`mints.supply`, usually 1e15 raw = 1e9 tokens). Graduation happens when `rt` reaches 0
  after a buy (~85 SOL raised); no curve fills after `complete_slot`.
- Pool: price = `(e / 1e9) / (b / 1e6)` on the pre-trade reserves, with `e = Q_eff = quote vault + virtual_quote_reserves`
  (signed). Fees are per event; never hardcode them.

## Universe filters (backtests)

- Quote mint wSOL `So111…112` or `111…111`; exclude `mayhem` and non-SOL quotes; report cashback and
  holder-reward coins separately; creator identity = `CreateEvent.user` (`mints.creator`).
- Graduations in the create slot (`complete_slot = create_slot`, 44% in this window) are never tradeable.
- `token_eligibility`: created inside the loaded data and after the 1-day burn-in.
