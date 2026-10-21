"""The venue census report (wallets.venue_census, v0) on a mini chunk: a user swap and the arb that backruns it on its
pool, an arb-like transaction with a liquidity withdrawal (no arb: E17's lesson), one not flat and one at a loss, a
failed attempt of the arb's bot, a pump launch, a LaunchLab graduation into a new CPMM pool, and on day 2 an arb by an
unlabelled bot program without a preceding swap (no backrun) and a routed user swap."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wallets import venue_census as V

SOL = 10**9
E = 1045
LO = E * 432_000
S = LO + 1_000
T0 = 1_790_000_000 - 1_790_000_000 % 86_400 + 3_600  # day 1, 01:00 UTC
BOT_PROGRAM = "BotProgram1111111111111111111111111111111111"
COLS = [("slot", pa.uint64()), ("tx_index", pa.uint64()), ("failed", pa.bool_()), ("fee_payer", pa.string()),
        ("tx_fee", pa.uint64()), ("priority_fee", pa.uint64()), ("jito_tip", pa.uint64()), ("cu_consumed", pa.uint64()),
        ("venues", pa.string()), ("n_dex", pa.uint64()), ("top_programs", pa.string()), ("dex_ixs", pa.string()),
        ("signer_sol_delta", pa.int64()), ("signer_token_deltas", pa.string()), ("mints", pa.string()), ("n_dex_ixs", pa.uint64())]


def tx(slot, idx, payer, ixs, sol, tokens=None, failed=False, fee=5_000, tip=0, top=None):
    venues = sorted({x.split(":")[0] for x in ixs})
    return {"slot": slot, "tx_index": idx, "failed": failed, "fee_payer": payer, "tx_fee": fee, "priority_fee": fee - 5_000,
            "jito_tip": tip, "cu_consumed": 100_000, "venues": ",".join(venues), "n_dex": len(venues),
            "top_programs": top or venues[0], "dex_ixs": ";".join(ixs), "signer_sol_delta": sol, "signer_token_deltas": tokens,
            "mints": None, "n_dex_ixs": len(ixs)}


@pytest.fixture
def chunks(tmp_path):
    c = tmp_path / "chunks" / f"{LO}-{LO + 216_000}"
    os.makedirs(c / "venue_census")
    os.makedirs(c / "blocks")
    rows = [
        tx(S, 0, "U1", ["raydium_cpmm:swap_base_input:P1"], -2 * SOL - 5_000, "MintX:500"),
        tx(S, 1, "B1", ["raydium_cpmm:swap_base_input:P1", "meteora_dlmm:swap:L1"], 50_000_000, None, tip=1_000_000),
        tx(S + 1, 0, "B2", ["meteora_dlmm:swap:L1", "meteora_dlmm:remove_liquidity:L1", "raydium_cpmm:swap_base_input:P1"], 80_000_000),
        tx(S + 2, 0, "B3", ["raydium_cpmm:swap_base_input:P1", "meteora_dlmm:swap:L1"], 10_000_000, "MintX:7"),
        tx(S + 3, 0, "B4", ["raydium_cpmm:swap_base_input:P1", "meteora_dlmm:swap:L1"], -1),
        tx(S + 4, 0, "B1", ["raydium_cpmm:swap_base_input:P1"], -5_000, failed=True),
        tx(S + 5, 0, "U2", ["pump:create:BC1", "pump:buy:BC1"], -SOL // 2 - 5_000, "MintP:1000"),
        tx(S + 6, 0, "U3", ["raydium_launchlab:migrate_to_cpswap:PS1", "raydium_cpmm:initialize:PC1"], -5_000),
        tx(S + 100, 0, "B5", ["orca_whirlpool:swap:W1", "raydium_amm_v4:swap_base_in:A1"], 10_000_000, top=BOT_PROGRAM),
        tx(S + 101, 0, "U4", ["orca_whirlpool:swap:W1"], -SOL // 2 - 5_000, "MintW:3", top="jupiter_v6"),
    ]
    pq.write_table(pa.table({n: [r[n] for r in rows] for n, _ in COLS}, schema=pa.schema(COLS)), str(c / "venue_census" / "part-00000.parquet"))
    slots = list(range(S, S + 102))
    times = [T0 + (s - S) if s < S + 100 else T0 + 86_400 + (s - S) for s in slots]
    pq.write_table(pa.table({"slot": pa.array(slots, pa.uint64()), "block_time": pa.array(times, pa.int64()),
                             "skipped": pa.array([False] * len(slots))}), str(c / "blocks" / "part-00000.parquet"))
    with open(c / "_manifest.json", "w") as f:
        json.dump({"status": "complete", "slot_start": LO, "slot_end_exclusive": LO + 216_000}, f)
    return str(tmp_path / "chunks")


def test_the_census_counts_venues_and_finds_the_backrun_arb_only(chunks, tmp_path):
    out = tmp_path / "out"
    V.main(["--chunks", chunks, "--epoch", str(E), "--out", str(out)])
    df = __import__("pandas").read_parquet(out / "venue_day.parquet")
    d1, d2 = sorted(df["day"].unique())
    row = lambda v, d: df[(df.venue == v) & (df.day == d)].iloc[0]
    cpmm = row("raydium_cpmm", d1)
    # A, B, C, D, E, F (failed) and H invoke CPMM; only A is a one-venue swap, 2 SOL by the proxy.
    assert (cpmm.txs, cpmm.failed, cpmm.volume_sol_proxy) == (7, 1, pytest.approx(2.0))
    # B is the only arb (C withdraws liquidity, D is not flat, E loses), and a backrun of A on P1; 1/2 to each venue.
    assert (cpmm.arbs, cpmm.arb_net_sol, cpmm.backruns, cpmm.backrun_net_sol) == (1, pytest.approx(0.025), 1, pytest.approx(0.025))
    assert (row("meteora_dlmm", d1).arbs, row("meteora_dlmm", d1).arb_net_sol) == (1, pytest.approx(0.025))
    # F: the bot's failed attempt costs its fee.
    assert (cpmm.failed_attempts, cpmm.failed_attempt_cost_sol) == (1, pytest.approx(5_000 / SOL))
    assert cpmm.tips_sol == pytest.approx(0.0005) and cpmm.pool_creations == 1
    assert (row("pump", d1).launches, row("raydium_launchlab", d1).graduations) == (1, 1)
    # Day 2: the unlabelled bot's arb has no preceding swap on its pool: no backrun.
    orca = row("orca_whirlpool", d2)
    assert (orca.arbs, orca.arb_net_sol, orca.backruns, orca.txs) == (1, pytest.approx(0.005), 0, 2)
    assert row("raydium_amm_v4", d2).arb_net_sol == pytest.approx(0.005)
    summary = json.loads((out / "venue_census.json").read_text())
    assert summary["rows"] == 10 and [p[0] for p in summary["unknown_programs"]] == [BOT_PROGRAM]
    # Coverage by block time: day 1 spans 100 s, day 2 2 s; per-day columns divide by the covered days, not by 2.
    assert [c["hours"] for c in summary["coverage"]] == [pytest.approx(100 / 3600), pytest.approx(2 / 3600)]
    assert summary["covered_days"] == pytest.approx(102 / 86_400, rel=0.01)
    assert (cpmm.hours, orca.hours) == (pytest.approx(100 / 3600), pytest.approx(2 / 3600))
    md = (out / "venue_census.md").read_text()
    per_day = 7 / summary["covered_days"]
    assert BOT_PROGRAM in md and f"| raydium_cpmm | {per_day:,.0f} |" in md


def test_hold_out_and_clean_epochs_are_refused(chunks, tmp_path):
    for e in (1047, 1051, 1055, 1063):
        with pytest.raises(SystemExit):
            V.main(["--chunks", chunks, "--epoch", str(e), "--out", str(tmp_path / "x")])
