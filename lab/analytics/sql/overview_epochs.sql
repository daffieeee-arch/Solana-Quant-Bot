-- Coverage per epoch in the development store (slots < 452,304,000). Epoch = slot // 432,000.
WITH b AS (SELECT slot // 432000 AS epoch, count(*) AS blocks,
                  432000 - count(*) AS skipped_slots,
                  to_timestamp(min(block_time))::TIMESTAMP AS first_block_utc, to_timestamp(max(block_time))::TIMESTAMP AS last_block_utc
           FROM blocks GROUP BY 1),
     m AS (SELECT create_slot // 432000 AS epoch, count(*) AS tokens_created FROM mints GROUP BY 1),
     g AS (SELECT complete_slot // 432000 AS epoch, count(*) AS graduations
           FROM mints WHERE complete_slot IS NOT NULL GROUP BY 1),
     c AS (SELECT slot // 432000 AS epoch, count(*) AS curve_trades FROM curve GROUP BY 1),
     p AS (SELECT slot // 432000 AS epoch, count(*) AS pool_trades FROM pool WHERE kind IN ('buy', 'sell') GROUP BY 1)
SELECT epoch, blocks, skipped_slots, first_block_utc, last_block_utc,
       tokens_created, graduations, curve_trades, pool_trades
FROM b LEFT JOIN m USING (epoch) LEFT JOIN g USING (epoch) LEFT JOIN c USING (epoch) LEFT JOIN p USING (epoch)
ORDER BY epoch;
