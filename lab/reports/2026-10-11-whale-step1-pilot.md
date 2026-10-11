# Whale tracker step 1 (pilot) and research 1b "runners", 2026-10-11

Second result for roadmap #167. Everything here is a **pilot on two development days** (2026-09-25 and 2026-09-26 UTC, slots 450,184,450–450,829,355); the full development period runs on the NAS (`lab/wallets/nas_step1.sh`). Numbers are first looks, not rankings. Code: `lab/wallets/` (README there); output outside Git under `data-old-faithful-one/lab/wallets/*-v1-sample/`.

## 1. Step 1: point-in-time wallet score

### What was built

- **Leader episodes** (`episodes.py`): one per wallet × token position opened by a buy ≥ 1 SOL that is not flagged as volume farming; followed for at most 4 h through the wallet's own trades (curve and canonical migration pool together). Realized and marked return (remaining tokens at the last ≥ 0.01 SOL trade, capped at 50× the buy price), hold until ≤ 10% of the position is left. Two days: **632,626 episodes, 123,669 wallets**; 21,381 wallets have ≥ 5 episodes.
- **Follower replay** (`follow.py`): our 0.5 SOL buy at t0 + d (d = 1, 2) through the backtest replay (own price impact, every later historical trade re-executed, fees from the data, base costs), sold at u + d when the leader has sold half (W_half) or 90% (W_full) of the position, or after 4 h. The leader's holdings path comes from the same historical legs, whole slots ≤ u only.
- **Score** (`score.py`): per day D, window [D − 7 d, D), positions closed before D; empirical-Bayes shrinkage to the all-wallet mean, minimum 5 positions for a rank, one-sided test with Benjamini–Hochberg q-values; labels creator / sniper / hf_bot / farmer / one_shot / bundler / split_buyer. Output: `leaderboard-<D>.parquet` for the private page (columns in `lab/wallets/README.md`).

### Pilot: 300 random wallets with ≥ 20 episodes, 150 random control wallets (2–19 episodes), ≤ 8 episodes per wallet per 4 h, 0.5 SOL, base costs

| variant | d | group | positions | wallets | entry failed | mean net return | median | net SOL / position | win | leader's own return (marked) | exits by leader sell | median hold (slots) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W_half | 1 | candidates | 11,511 | 300 | 3.3% | −13.1% | −6.1% | −0.066 | 30% | +8.8% | 82% | 138 |
| W_half | 2 | candidates | 11,511 | 300 | 6.9% | −12.8% | −5.5% | −0.064 | 31% | +8.8% | 79% | 142 |
| W_half | 2 | control | 710 | 150 | 4.8% | −12.3% | −16.8% | −0.062 | 27% | +3.1% | 73% | 1,347 |
| W_full | 2 | candidates | 11,511 | 300 | 6.9% | −14.8% | −8.1% | −0.074 | 32% | +8.8% | 75% | 290 |

By venue (W_half, d = 2, candidates): curve −9.8% (entry failed 15%), normal pools −11.4%, reversed pools −17.4%. Both days negative (−14.7% and −12.9% per position).

- **Following active wallets loses about 12–13% per position at 0.5 SOL**, while the same wallets earned +8.8% themselves on the same episodes. The gap is our price impact on thin tokens, the 1–2 slot delay on both legs, and the leader selling into us (80% of our exits are triggered by the leader's sale).
- **The control group loses the same.** Active wallets are not better to follow than random active wallets.
- **Per wallet there is no copyable skill in this sample.** Correlation between a wallet's own return and its follower return: −0.05 (295 wallets with ≥ 10 positions). Score day 2026-09-27: 340 ranked wallets, prior −12.8%, shrinkage k ≈ 8–10; 40 of 340 have a positive shrunk score, **0 pass the FDR gate (q < 0.10)**. The top of the list is small-n noise (a control wallet is #1).
- Labels on 2026-09-27 (163,783 wallets active in the window): 54% one-shot; snipers (≥ half of the buys in a create slot) show the highest own returns (+137% to +160% marked, median hold ~25 s) and are by construction not followable; farmers 5.9 positions/day, 15 s hold.

What this does **not** say: the pilot covers 2 days and random active wallets, not the full population; the score needs the 18.7-day run to find rare skilled wallets (if they exist) with enough positions. It does say that "active" is not a useful pre-filter and that the follower's result is dominated by execution, not by the leader's skill.

## 2. Research 1b: can runners be recognised in advance?

`runners.py` / `runners_report.py`. Universe: 1,622 graduations on the two days into a canonical SOL pool (non-mayhem); **879 graduated in their create slot** (dev bundles) and are reported apart; 743 did not. Market-cap multiples are price multiples over the pool's opening price (supply is constant).

### Base rates

| graduated in create slot | graduations | peak ≥ 3× open in 24 h | ≥ 10× | median peak | median end (24 h) | end ≥ 3× |
|---|---|---|---|---|---|---|
| no | 743 | 24.6% | 5.5% | 1.67× | 0.07× | 1.6% |
| yes | 879 | 86.8% | 67.6% | 14.8× | 0.04× | 3.8% |

The create-slot bundles "run" from their opening price because the bundle itself sets it; they end at 4% of the open. They are not the owner's runners.

**Forward from the decision moment** (what a buyer at +5 / +15 min still gets, outside the create slot):

| decision | ≥ 3× after | ≥ 10× after | median peak after | median end after |
|---|---|---|---|---|
| +5 min | 17.1% | 3.6% | 1.33× | 0.11× |
| +15 min | 14.3% | 3.2% | 1.18× | 0.57× |

So the owner's "≥ 10× in 24 h" is ~3.5% of graduations when measured from where one could buy; the median graduation loses ~90% in 24 h.

### Lift per feature (runner rate in the top quintile over the base rate)

| feature at +5 min | ≥ 3× after | ≥ 10× after | at +15 min, ≥ 3× | ≥ 10× |
|---|---|---|---|---|
| price at the decision / open (momentum) | **1.7×** (29%) | 1.9× (6.8%) | 2.0× (29%) | 2.1× |
| net SOL flow into the pool | 1.6× (28%) | 1.7× | 1.9× (28%) | 2.3× (7.4%) |
| highest price so far / open | 1.4× | 1.9× | 1.5× | 1.9× |
| organic pool volume | 1.2× | 1.5× | 1.3× | 1.3× |
| unique curve buyers (hype) | **0.6×** (11%) | 0.2× (0.7%) | 0.6× | 0.0× |
| share of buyers ≥ 5 SOL | 1.0× | 1.5× | 0.6× | 0.6× |
| insider share (O7) | 0.9× | 1.6× | 0.3× | 0.4× |
| minutes to graduation, creator sells, split-buyer events | ≈ 1× | ≈ 1× | ≈ 1× | ≈ 1× |

- The only signals with lift are **early price and net inflow in the pool**: a token already up 1.4× or more at +5 min has a 29% chance of another 3× (base 17%). That is momentum, and the bottom quintile (down at +5 min) almost never runs (6%).
- **Hype does the opposite of what one would hope:** the quintile with the most curve buyers (700–2,000) runs least (0.6× for 3×, 0.2× for 10×). Big buyers, insider share, creator sells, time to graduation and split-buyer bundles carry no usable signal at +5 min.
- A logistic baseline on all features, fitted on day 1 and applied to day 2 (walk-forward): AUC 0.60 for "≥ 3× after +5 min", top decile 28% vs base 19%. For "≥ 10×" the one-day AUC of 0.87 comes from an outcome that included the first 5 minutes (the open-based target) and is not usable; the forward target has too few events (23) for a fit.
- Examples (two days): the five tokens with the most buyers and organic volume at +5 min that ran (IP, Desk, CateBrain, >_, TTP: 3–8× after the decision, four of five ending at 0.06–0.13×) and the five with the same hype that did not (tinycat with 2,049 buyers: 1.8×; MONETIZED, Ecat, MAXWELL, 𝕏/ACC: 1.0–1.9×, ending at 0.05–0.11×). Same signals, opposite outcomes; the survivors are rare either way.

Limitations: two days; the insider share can exceed 1 when sells exceed visible buys (transfers are invisible) — it is a noisy feature; features are at fixed minutes after the pool opened, not at a trading decision of a tested strategy. The full run (18.7 days, per-day walk-forward) is cheap: ~12 s per 4 h slice.

## 3. Case selection from our own data

`cases.py` / `cases_report.py`; the Dutch report with the tables is `lab/reports/2026-10-11-casussen-trending.md`. Per day the top-5 "trending 24 h" (return × volume, ≥ 300 SOL, ≥ 100 traders, ≥ 5 SOL real liquidity at the end) and top-5 pumpers/dumpers (volume, ended ≤ 10% of the peak); over the period the top-10 stayers and collapsed tokens 7 days after graduation; each with a timeline, the +5/+15 min features, insider share, creator sells and the five biggest sells.

Finding: the "trending" winners are a self-funded structure — create-slot graduation by one buyer (the whole curve, 85 SOL), then 8–27k SOL deposited into the pool by the same party (price ×10⁴–10⁵ over the open), dumped within a day in one 10.5–10.8k SOL sale, often by the creator; the same names (AROS, VSOF, USDF, WOFI, WSOS) recur with new mints. The organic hype tokens (2,900–13,900 traders, 33–66k SOL in a day) are the dumpers: peak 90–450× the open, end at 0–10% of the peak. Real 7-day survivors are a handful (PAID with 88k traders, JEANPHIL, MCAT, WEPE, MINESTOCK). At +15 min the two groups differ only in *who* buys (1 buyer vs a crowd; insider share 0.79 vs 0.78, same inflow, no creator sells, no split-buyer bundles), which separates the two kinds of failure, not success from failure. A trending list from our data needs a self-funded filter first (create-slot graduation and ≤ 2 curve buyers).

## Resource notes

Pilot on the VPS, every step in its own scope (2 GB, 2 cores): episodes 13 slices of 4 h, 1–5 min each, ~1.1 GB; follower replay 12 slices, ~2–3 min each, 1.5 GB, 46,000 positions in total; runners 12 slices of ~12 s; the score for one day ~1 min. The session was OOM-killed once when queries ran inside the session scope; since then nothing runs outside a scope.

## Next

- NAS: `nas_step1.sh` (episodes → candidates → follower replay with 7 workers → daily scores); then the same tables over 18.7 days, with the leaderboard page fed from `scores-v1/leaderboard-<D>.parquet`.
- Runners over the full period, walk-forward per day, with the forward targets; then the wallet score of the early buyers as a feature once the scores exist.
- The owner's Phantom examples as cases once epochs 1052+ are in a store (forward data: illustration only).
