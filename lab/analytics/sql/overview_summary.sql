-- Headline numbers for the development store.
WITH cv AS (SELECT mint, count(*) AS n FROM curve GROUP BY 1)
SELECT (SELECT count(*) FROM mints) AS tokens,
       (SELECT count(*) FROM mints WHERE complete_slot IS NOT NULL) AS graduated,
       (SELECT count(*) FROM mints WHERE complete_slot = create_slot) AS graduated_in_create_slot,
       (SELECT count(*) FROM mints WHERE mayhem) AS mayhem_tokens,
       (SELECT count(*) FROM mints WHERE quote_mint NOT IN ('So11111111111111111111111111111111111111112',
                                                            '11111111111111111111111111111111')) AS non_sol_quote,
       (SELECT median(coalesce(n, 0)) FROM mints LEFT JOIN cv USING (mint)) AS median_curve_trades_per_token,
       (SELECT count(*) FILTER (WHERE coalesce(n, 0) < 10) FROM mints LEFT JOIN cv USING (mint)) AS tokens_under_10_trades,
       (SELECT count(*) FROM curve) AS curve_trades,
       (SELECT count(*) FROM pool_trades) AS pool_trades,
       (SELECT count(*) FROM pools) AS pools,
       (SELECT min(slot) FROM blocks) AS first_slot,
       (SELECT max(slot) FROM blocks) AS last_slot;
