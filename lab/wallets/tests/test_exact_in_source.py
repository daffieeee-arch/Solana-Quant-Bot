"""The wallets SQL exact-in path against source events, not merely another model."""

import csv
from pathlib import Path

import duckdb

from backtest import venues
from wallets import runners


SOURCE = Path(__file__).resolve().parents[2] / "core/tests/fixtures/pumpswap-exact-in.tsv"


def test_source_pinned_zero_cashback_pool_buys():
    with SOURCE.open(newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    assert len(rows) == 18
    con = duckdb.connect()
    changed = boundary_rejections = 0
    for row in rows:
        b, e, budget = (int(row[k]) for k in ("b", "e", "budget"))
        lp, pr, cr = (int(row[k]) for k in ("lp_bps", "protocol_bps", "creator_bps"))
        initial = f"(budget * {runners.BPS}) // ({runners.BPS} + lp + pr + cr)"
        net = runners._pool_net_from_initial("initial", "budget", "lp", "pr", "cr")
        got = con.execute(f"""WITH x AS (SELECT ?::HUGEINT AS b, ?::HUGEINT AS e,
                          ?::HUGEINT AS budget, ?::HUGEINT AS lp, ?::HUGEINT AS pr, ?::HUGEINT AS cr),
                          y AS (SELECT *, {initial} AS initial FROM x),
                          z AS (SELECT *, {net} AS n FROM y)
            SELECT n, {runners._pool_fees('initial', 'lp')},
                   {runners._pool_fees('initial', 'pr')}, {runners._pool_fees('initial', 'cr')},
                   (b * (n - 1)) // (e + n - 1) AS tokens
            FROM z""", (b, e, budget, lp, pr, cr)).fetchone()
        n, lp_fee, pr_fee, cr_fee, tokens = map(int, got)
        assert (n, lp_fee, pr_fee, cr_fee, tokens) == tuple(
            int(row[k]) for k in ("net", "lp_fee", "protocol_fee", "creator_fee", "tokens")
        )
        assert n + lp_fee + pr_fee + cr_fee == int(row["debit"])
        assert b - tokens == int(row["post_b"])
        assert e + n + lp_fee == int(row["post_e"])

        # The independently source-checked Python venue is the direct comparison.
        p = venues.Pool(b, e, lp, pr, cr, 0, virt=int(row["virt"]))
        fill = venues.pool_buy_exact_in(p, budget)
        assert fill is not None
        assert (fill.tokens, fill.curve_quote, fill.fees, fill.trader_quote) == (
            tokens, n, lp_fee + pr_fee + cr_fee, int(row["debit"])
        )
        old = con.execute(f"SELECT {runners._net_for_budget('budget', 'lp', 'pr', 'cr', '0')} "
                          "FROM (SELECT ?::HUGEINT AS budget, ?::HUGEINT AS lp, "
                          "?::HUGEINT AS pr, ?::HUGEINT AS cr)", (budget, lp, pr, cr)).fetchone()[0]
        changed += int(old != n)
        old_tokens = b * (int(old) - 1) // (e + int(old) - 1)
        boundary_rejections += int(old_tokens >= tokens + 1)
    assert changed > 0  # The regression must catch the old SQL calculation.
    assert boundary_rejections > 0  # A min-out one token above source output must be refused.
