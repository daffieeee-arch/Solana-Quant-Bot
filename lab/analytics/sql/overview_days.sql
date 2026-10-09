-- Per UTC day (block_time of the slot): new tokens, graduations, trades and SOL volume.
-- Volume counts only SOL-quoted tokens/pools (quote mint wSOL or the system program); fees excluded.
-- The first and last day are partial (the store starts and ends mid-day).
WITH d AS (SELECT slot, CAST(to_timestamp(block_time)::TIMESTAMP AS DATE) AS day FROM blocks),
     sol AS (SELECT * FROM (VALUES ('So11111111111111111111111111111111111111112'),
                                   ('11111111111111111111111111111111')) v(quote_mint)),
     created AS (SELECT d.day, count(*) AS tokens_created,
                        count(*) FILTER (WHERE m.mayhem) AS mayhem_created
                 FROM mints m JOIN d ON d.slot = m.create_slot GROUP BY 1),
     grads AS (SELECT d.day, count(*) AS graduations,
                      count(*) FILTER (WHERE m.complete_slot = m.create_slot) AS grad_in_create_slot
               FROM mints m JOIN d ON d.slot = m.complete_slot GROUP BY 1),
     ct AS (SELECT d.day, count(*) AS curve_trades,
                   sum(c.q) FILTER (WHERE m.quote_mint IN (SELECT quote_mint FROM sol)) / 1e9 AS curve_sol
            FROM curve c JOIN d USING (slot) JOIN mints m USING (mint) GROUP BY 1),
     pt AS (SELECT d.day, count(*) AS pool_trades,
                   sum(p.quote_gross) FILTER (WHERE ps.quote_mint IN (SELECT quote_mint FROM sol)) / 1e9 AS pool_sol
            FROM pool p JOIN d USING (slot) LEFT JOIN pools ps USING (pool)
            WHERE p.kind IN ('buy', 'sell') GROUP BY 1)
SELECT day, tokens_created, mayhem_created, graduations, grad_in_create_slot,
       curve_trades, round(curve_sol) AS curve_sol, pool_trades, round(pool_sol) AS pool_sol
FROM created FULL JOIN grads USING (day) FULL JOIN ct USING (day) FULL JOIN pt USING (day)
ORDER BY day;
