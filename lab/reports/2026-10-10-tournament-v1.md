# Tournament v1 (dev, epochs 1042–1046), 2026-10-10

Run `v1-dev-1042-1046`. Engine `93f344b` (frozen copy in the run directory); report regenerated with `1a8c029`. Reviewed by the coordinator: `data-old-faithful-one/lab/research/week2-tournament-v1-review.md`.
Full report, results and trials: `data-old-faithful-one/lab/backtests/v1-dev-1042-1046/`
(`report.md`, `results.parquet`, `summary.parquet`, `trials.parquet`, `config.json`).

**Setup**
- Store: dev slots 450144000–452304000, about 7 days in one regime (250 ms slots, old protocol). The hold-out was not opened.
- Families:
  - F7 (large sell reversal, 9 TP/time variants);
  - F1 (late curve, bands 40/55/70 SOL);
  - controls N1 (create-slot snipe, 3000 tokens sampled) and N2 (5 matched random entries per trigger, same venue, depth and hour).
- Sizes 0.5 / 2 / 5 / 10 / 25 SOL, delay d = 1 and 2 slots, empirical τ for router calls.
- 1.17M signals and 8.67M simulated positions, in 1 h 46 min with 2 cores and 3 GB.

## Engine sanity

N2 should lose about the round-trip cost beyond the historical drift. It does.
- F7 controls, base costs, d=1, return minus drift (mean / median):
  - 0.5 SOL: −2.8 / −2.6 pts;
  - 5 SOL: −4.9 / −2.9;
  - 10 SOL: −6.8 / −4.0;
  - 25 SOL: −11.2 / −5.7.
- Fees and network alone at 0.5 SOL are about 2.8 %.
- F1 and its controls at 0.5 SOL lose 4.0–4.5 pts beyond drift, which is consistent with those costs plus impact.

Drift excludes degenerate historical states only: a mid move above ×16, meaning near-empty pool reserves (share < 0.01 %). An earlier ±100 % clip cut real graduation moves (about +170 % from 40 SOL real to the pool) and made F1 look too good. That flag is resolved.

## Results at base costs

Mean = return per filled trade after all costs.

| family | 0.5 SOL | 2 SOL | 5 SOL | 10 SOL | 25 SOL |
|---|---|---|---|---|---|
| F7, d=1 (9 variants) | −4.4 … −4.8 % | −5.3 … −6.0 % | −7.2 … −8.3 % | −9.4 … −10.9 % | −12.2 … −14.1 % |
| F7, d=2 | −5.8 … −6.3 % | −6.7 … −7.5 % | −8.7 … −10.0 % | −11.0 … −12.8 % | −14.1 … −16.5 % |
| F1 B40, d=1 | −4.1 % | −7.5 % | −14.2 % | −23.7 % | −42.3 % |
| F1 B55, d=1 | +1.5 % (CI −4.2 … +7.1) | −1.2 % | −7.2 % | −15.6 % | −31.1 % |
| F1 B70, d=1 | −3.8 % | −6.0 % | −10.6 % | −14.2 % | (above capacity) |
| N1 H150 / H1500, d=1 | −4.5 / −8.4 % | −6.1 / −10.1 % | −8.8 / −12.3 % | −11.4 / −14.8 % | −15.6 / −18.7 % |

- **Stop-rule measure** (net SOL per trade, 95 % day-bootstrap CI):
  - STOP for every F7 and F1 variant at every size ≥ 2 SOL, and for all F7 at 0.5 SOL.
  - The only positive mean is F1 B55 at 0.5 SOL (+0.007 SOL per trade). That is ADJUST, informative only (under the live size), and its CI includes 0.
  - Optimistic costs change nothing: no F7 or N1 variant is positive. Under pessimistic costs nothing is.
- **Edge vs N2:**
  - F7 beats its random control only at d=1 and small size: +0.7 … +1.2 pts at 0.5 SOL, with the CI above 0.
  - The edge is gone at d=2 (−0.5 … 0 pts) and at ≥ 5 SOL (10 SOL: −0.4 … −1.6 pts at d=1, −1.7 … −2.9 pts at d=2).
  - So the reversal is real but small, it decays within one slot, and our own impact and stop-loss gaps eat it.
- **Batch-1 gate** (edge vs N2 > 0 at ≥ 5 SOL for both d): read as **nothing passes**.
  - F1 B55 and B70 formally pass, but their edge CI at 10 SOL includes 0 (B55 −1.0 … +5.2 pts, B70 −0.3 … +2.6).
  - Both lose 14–16 % per trade at 10 SOL in absolute terms.
  - All F7 variants fail.
- **Replay dependence:** large F1 positions often graduate the curve themselves (counterfactual graduation 21 % at 10 SOL, 40 % at 25 SOL), and router transactions get reverted. These results depend on the model, not just the data.

## Conclusion

- No strategy in v1 is profitable after costs at the live size. Nothing is claimed profitable.
- F7 and N2 remain useful as engine references for paper parity, not as paper candidates.
- Size is the core problem: on the curve a 10 SOL order costs 8–24 pts per trade in fees and impact. Only deep pools can carry the live size.
- The F7 signal is gone between d=1 and d=2, so latency decides. Every report shows d=1 and d=2 side by side.

**Limits:** 7 days in a single regime; only ~52 % of pool trades are in the store (pools created in the loaded period).

**Next (W2b):**
- Rebuild epochs 1033–1046 with all pools.
- Replay validation on epoch 1052 for the new protocol.
- Families with capacity first: F6 and S1 (deep pools), O7 as an overlay. Then F2/F4/F5.
- Controls N3 and N4 (N4 was missing in v1).
- Graduation-exit check, low priority.
