"""Check the integer fill formulas against every historical trade event.

pump bonding curve (TradeEvent reserves are post-trade; pre-trade state follows from amounts):
  sell:                q = floor(t * Vq / (Vt + t))
  buy (exact out):     q = floor(t * Vq / (Vt - t)) + 1
  buy (exact in):      t = floor((q - 1) * Vt / (Vq + q - 1))
  fees:                fee = ceil(q * bps / 10000), per fee component
PumpSwap (Buy/SellEvent reserves are pre-trade; E = quote vault + virtual quote):
  buy (exact out):     quote_amount_in = ceil(E * b / (B - b))
  sell:                quote_amount_out = floor(E * a / (B + a))
Prints match counts per instruction variant. Usage: python -m backtest.validate_math
"""

import json
import sys

from . import data

H = "::HUGEINT"


def pump(con):
    src = data.parquet("pump/TradeEvent")
    disc_names = " ".join(f"WHEN '{d}' THEN '{n}'" for d, n in data.PUMP_IX.items())
    q = f"""
    WITH t AS (
      SELECT CASE parent_ix_disc {disc_names} ELSE 'other:' || COALESCE(parent_ix_disc, 'null') END AS ix,
             is_buy, token_amount{H} AS t, COALESCE(quote_amount, sol_amount){H} AS q,
             virtual_token_reserves{H} AS vt, COALESCE(virtual_quote_reserves, virtual_sol_reserves){H} AS vq,
             fee{H} AS fee, fee_basis_points{H} AS fbps, creator_fee{H} AS cfee, creator_fee_basis_points{H} AS cbps,
             "user" = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s' AS mayhem_agent
      FROM {src}
    ), p AS (
      SELECT *, CASE WHEN is_buy THEN vt + t ELSE vt - t END AS pvt,
                CASE WHEN is_buy THEN vq - q ELSE vq + q END AS pvq FROM t
    )
    SELECT ix, is_buy, mayhem_agent, count(*) AS n,
      count(*) FILTER (WHERE NOT is_buy AND pvt + t > 0 AND q = (t * pvq) // (pvt + t)) AS sell_formula,
      count(*) FILTER (WHERE is_buy AND pvt - t > 0 AND q = (t * pvq) // (pvt - t) + 1) AS buy_exact_out_formula,
      count(*) FILTER (WHERE is_buy AND pvq + q - 1 > 0 AND t = ((q - 1) * pvt) // (pvq + q - 1)) AS buy_exact_in_formula,
      count(*) FILTER (WHERE fee = (q * fbps + 9999) // 10000) AS fee_ceil,
      count(*) FILTER (WHERE cfee = (q * cbps + 9999) // 10000) AS creator_fee_ceil
    FROM p GROUP BY ALL ORDER BY n DESC"""
    cols = ["ix", "is_buy", "mayhem_agent", "n", "sell_formula", "buy_exact_out_formula", "buy_exact_in_formula",
            "fee_ceil", "creator_fee_ceil"]
    return [dict(zip(cols, r)) for r in con.execute(q).fetchall()]


def amm(con):
    eff = f"(pool_quote_token_reserves{H} + COALESCE(TRY_CAST(virtual_quote_reserves AS HUGEINT), 0))"
    buy = data.parquet("pump_amm/BuyEvent")
    sell = data.parquet("pump_amm/SellEvent")
    out = {}
    out["buy"] = con.execute(f"""
      SELECT count(*),
        count(*) FILTER (WHERE pool_base_token_reserves > base_amount_out AND
          quote_amount_in{H} = ({eff} * base_amount_out{H} + pool_base_token_reserves{H} - base_amount_out{H} - 1)
                               // (pool_base_token_reserves{H} - base_amount_out{H})),
        count(*) FILTER (WHERE lp_fee{H} = (quote_amount_in{H} * lp_fee_basis_points{H} + 9999) // 10000),
        count(*) FILTER (WHERE protocol_fee{H} = (quote_amount_in{H} * protocol_fee_basis_points{H} + 9999) // 10000)
      FROM {buy}""").fetchone()
    out["sell"] = con.execute(f"""
      SELECT count(*),
        count(*) FILTER (WHERE quote_amount_out{H} = ({eff} * base_amount_in{H}) // (pool_base_token_reserves{H} + base_amount_in{H})),
        count(*) FILTER (WHERE lp_fee{H} = (quote_amount_out{H} * lp_fee_basis_points{H} + 9999) // 10000),
        count(*) FILTER (WHERE protocol_fee{H} = (quote_amount_out{H} * protocol_fee_basis_points{H} + 9999) // 10000)
      FROM {sell}""").fetchone()
    return {k: dict(zip(["n", "curve_formula", "lp_fee_ceil", "protocol_fee_ceil"], v)) for k, v in out.items()}


def main():
    con = data.connect()
    report = {"chunks": len(data.complete_chunks()), "pump": pump(con), "pump_amm": amm(con)}
    json.dump(report, sys.stdout, indent=1, default=str)
    print()


if __name__ == "__main__":
    main()
