-- Example tokens: the 12 graduated, SOL-quoted, non-mayhem tokens with the most SOL volume
-- (curve + PumpSwap, excluding fees) in the development store. Names/symbols are creator-supplied text.
WITH d AS (SELECT slot, to_timestamp(block_time)::TIMESTAMP AS ts FROM blocks),
     cv AS (SELECT mint, count(*) AS curve_trades, sum(q) / 1e9 AS curve_sol FROM curve GROUP BY 1),
     pv AS (SELECT mint, count(*) AS pool_trades, sum(sol) / 1e9 AS pool_sol
            FROM pool_trades WHERE mint IS NOT NULL GROUP BY 1)
SELECT m.name, m.symbol, m.mint,
       dc.ts AS created_utc,
       round((m.complete_slot - m.create_slot) * 0.2673 / 60, 1) AS minutes_to_graduate,
       cv.curve_trades, round(cv.curve_sol) AS curve_sol,
       coalesce(pv.pool_trades, 0) AS pool_trades, round(coalesce(pv.pool_sol, 0)) AS pool_sol,
       m.holder_reward, m.cashback
FROM mints m
JOIN d dc ON dc.slot = m.create_slot
JOIN cv USING (mint)
LEFT JOIN pv USING (mint)
WHERE m.complete_slot IS NOT NULL AND NOT m.mayhem
  AND m.quote_mint IN ('So11111111111111111111111111111111111111112', '11111111111111111111111111111111')
ORDER BY cv.curve_sol + coalesce(pv.pool_sol, 0) DESC
LIMIT 12;
