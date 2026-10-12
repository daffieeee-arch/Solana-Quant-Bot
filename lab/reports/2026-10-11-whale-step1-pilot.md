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

### Full development period (NAS, 2026-10-12 → 13; `episodes-v1`, `follow-v1`, `scores-v1`; report `lab/reports/2026-10-13-whale-step1-full-tables.md` from `step1_report.py`)

**Pre-registered bar** (comment on #181, 2026-10-12T19:16:47Z, before the scores were seen): whale step 1 counts as useful if the top-20 of the leaderboard of day D (scored on data before D), followed on D itself with W_half, 0.5 SOL, d = 2, has a net return per position > 0 over dev days 10–18 (2026-09-20 … 09-30) with the 95 % day-bootstrap CI (whole days, 10,000×) above 0, while all candidates without selection do not and the control wallets are ≤ 0. **The bar is not met. Whale step 1 is closed; there is no step 2 (no watchlist of followed wallets in paper).**

| group (W_half, 0.5 SOL, d = 2) | days 10–18: positions | mean net | 95 % CI (days) | days 1–9: positions | mean net | 95 % CI |
|---|---|---|---|---|---|---|
| **top-20 of the day's leaderboard, followed that day** (the bar) | 418 | **−7.3 %** | **−35.2 … +4.5** | 398 | −9.4 % | −18.6 … −1.6 |
| FDR top (q < 0.10) and copyable, followed that day (secondary) | 740 | +0.6 % | −4.3 … +4.9 | 531 | −5.5 % | −10.4 … +1.5 |
| copyable and positive shrunk score, followed that day | 84,346 | −6.1 % | −8.8 … −2.9 | 36,425 | −2.9 % | −5.0 … −1.1 |
| all candidates (no selection) | 1,387,571 | −13.2 % | −14.1 … −12.4 | 950,363 | −14.4 % | −16.6 … −11.3 |
| control wallets (2–19 episodes) | 1,302 | −10.1 % | −14.2 … −5.8 | 868 | −16.4 % | −21.1 … −12.5 |

What the full run shows (all 21 days, tables in the companion file):

- **Scale.** 7,100,202 leader episodes in 21 days from 1,168,542 wallets (138,292 with ≥ 5 episodes); the leaders' own return is +3.3 % marked and **−14.3 % realized**; 84.6 % of the episodes close inside 4 h.
- **Following loses on every variant and every day.** W_half d = 2: candidates −13.7 % per position (CI −14.8 … −12.4), control −12.5 % (−15.7 … −9.2); W_full is 1 point worse; d = 1 is not better than d = 2. 21 of 21 days are negative for the candidates. Per venue: curve −8.9 %, normal pools −15.8 %, reversed pools −13.4 %. The leader's own marked return is positive on the curve (+9.9 %) and in reversed pools (+19.9 %) while the follower loses there too: the leader's edge is in his entry price, not in the direction.
- **The selection does not transfer.** The point-in-time score ranks 57–66 k wallets a day on 7-day windows, finds 59–110 FDR-top wallets a day, and the top-20 followed the next day lose −7.3 % on days 10–18 and −9.4 % on days 1–9. The per-wallet correlation between a leader's own return and his follower's return is +0.29 across 69,633 wallets with ≥ 10 positions, but only 19 % of those wallets have a positive follower mean, against 40 % with a positive own return. The top-12 of the last day are wallets with 5–33 episodes and shrunk scores of +160 to +320 % at q = 1.00: flukes the FDR does not let through, and the ones it does let through (q < 0.10) do not repeat.
- **Exits.** 75 % of the positions end because the leader sold (−13.8 %), 18 % on the 4 h time stop (−18.1 %), 7 % never entered (slippage). The 1.4 % that exit at a graduation (+83.6 %) are the only profitable exit: a leader buying on the curve of a token that then graduates. That is a token property seen afterwards, not a wallet property seen before.
- **Overfitting check (standard C, `overfit.py`, trials = 4 variants × delays, days 10–18):** the best trial W_half d = 2 has a daily Sharpe of −9.3; DSR 0.00 both ways, PBO 0.64 over 70 CSCV splits. Nothing to deflate: every trial loses every day.

**Farmer signal (subsection promised on 2026-10-12).** On the F7-reversed tournament (K1 = tp4_T600, 0.5 SOL, base costs, dev days 09-12 … 10-01) organic pools return +1.67 % per trade at d = 1 against flagged +1.09 % (difference +0.58 points, day-bootstrap CI +0.18 … +0.92, organic better on 15 of 20 days), but the difference fades: days 1–9 +1.1 points, days 10–18 +0.36, and on the last 5 days organic is better on 2 of 5. Per regime 270 ms +1.69 vs +1.29, 320 ms +1.41 vs +0.43. The seller's own farmer flag changes little inside each class (flagged pools 1.08 vs 1.19, organic 1.52 vs 1.69): the pool's farming hour carries the effect, not the trigger seller. The hold-out flip (flagged +0.68 vs organic −0.08 at d = 1, #185) fits a fading advantage, not a stable sign. At wallet level in the full whale run (days 10–18, W_half d = 2) the followers of **farmer-labelled leaders lose less: −6.6 % over 144,299 positions (6,137 wallets) against −13.9 % for the others**; sniper-labelled leaders −15.0 %, creators −10.7 %, bundlers −14.6 %, high-frequency bots −7.7 % (3,234 positions). Less bad is not good: no label group is positive, and the label is applied to the leader, not to the pool.

**Conclusion.** Copying wallets that look good on their own closed trades does not earn after costs on this market, for any of the pre-registered variants, on any day of the development period, and not for the FDR-selected or the top-ranked subset either. The leaders' realized return is itself negative; what looks like skill is the entry price of snipers and creators, which a follower at d = 1 or 2 slots never gets. The pre-registered bar (top-20, days 10–18, CI > 0) is missed by a wide margin. Whale step 1 is closed; the only descriptive leads (graduation exits, farmer-labelled leaders) are token or pool properties, not reasons to follow a wallet.

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

### Full development period (added later the same day; `runners-v1/`, 106 slices up to slot 451,964,100 so that every 24 h outcome window stays before the hold-out)

14,391 graduations: 8,910 in the create slot (dev bundles, apart), **5,481 outside**. Of those, 24.8% peak ≥ 3× the open in 24 h and 4.8% ≥ 10×; median end 0.06×. Forward from the decision: at +5 min 15.2% still make ≥ 3× and 3.6% ≥ 10× (median end 0.10×); at +15 min 9.6% and 2.1% (median end 0.68×: by then most dumps have happened).

| top quintile vs base | +5 min, ≥ 3× | +5 min, ≥ 10× | +15 min, ≥ 3× |
|---|---|---|---|
| price at decision / open | 1.2× (bottom quintile 0.3×) | 1.1× (bottom 0.4×) | **2.2×** (21.4%; bottom 0.1×) |
| net SOL flow into the pool | 1.2× (bottom 0.3×) | 1.0× (bottom 0.4×) | **2.2×** (20.6%; bottom 0.1×) |
| minutes to graduation (slow) | 1.1× | 0.9× | 1.5× (fast graduations 0.4×) |
| unique curve buyers (hype) | 0.7× | 0.5× | 1.0× (few buyers 0.4×) |
| insider share (O7) | 1.0× | 1.1× | **0.2×** |
| share of buyers ≥ 5 SOL | 1.0× | 0.9× | 0.5× |
| creator sells | 0.9× | 0.8× | 1.3× |
| split-buyer events | 0.8× | 0.6× | 0.9× |

Walk-forward per day over 18 days (ridge logistic on all features, fitted on earlier days): mean AUC 0.61 at +5 min and **0.74 at +15 min** for "≥ 3× after the decision"; top decile of the predictions 22% at +5 min (base 15%) and 21% at +15 min (base 9.5%). For "≥ 10×": AUC 0.60, top decile 7.4% vs 3.6%.

So over 18 days the picture of the two-day pilot holds: at +5 min nothing separates runners from the rest; at +15 min early momentum and net inflow, a slower graduation and a low insider share give a 2× lift, and the bottom quintiles (price below open, insiders still holding > 0.5) almost never run. Hype at the create (buyers, big buyers, bundles) carries no positive signal. A 2× lift on a 10% base is not a strategy; it is a filter candidate (coordinator: this is the M1 idea plus a hype veto).

Limitations: the insider share can exceed 1 when sells exceed visible buys (transfers are invisible) — it is a noisy feature; features are at fixed minutes after the pool opened, not at a trading decision of a tested strategy; one regime split not yet applied (316 ms vs 267 ms epochs).

## 3. Case selection from our own data

`cases.py` / `cases_report.py`; the Dutch report with the tables is `lab/reports/2026-10-11-casussen-trending.md`. Per day the top-5 "trending 24 h" (return × volume, ≥ 300 SOL, ≥ 100 traders, ≥ 5 SOL real liquidity at the end) and top-5 pumpers/dumpers (volume, ended ≤ 10% of the peak); over the period the top-10 stayers and collapsed tokens 7 days after graduation; each with a timeline, the +5/+15 min features, insider share, creator sells and the five biggest sells.

Finding: the "trending" winners are a self-funded structure — create-slot graduation by one buyer (the whole curve, 85 SOL), then 8–27k SOL deposited into the pool by the same party (price ×10⁴–10⁵ over the open), dumped within a day in one 10.5–10.8k SOL sale, often by the creator; the same names (AROS, VSOF, USDF, WOFI, WSOS) recur with new mints. The organic hype tokens (2,900–13,900 traders, 33–66k SOL in a day) are the dumpers: peak 90–450× the open, end at 0–10% of the peak. Real 7-day survivors are a handful (PAID with 88k traders, JEANPHIL, MCAT, WEPE, MINESTOCK). At +15 min the two groups differ only in *who* buys (1 buyer vs a crowd; insider share 0.79 vs 0.78, same inflow, no creator sells, no split-buyer bundles), which separates the two kinds of failure, not success from failure. A trending list from our data needs a self-funded filter first (create-slot graduation and ≤ 2 curve buyers).

## Reproducibility note (NAS, 2026-10-11 evening)

The coordinator computed episode slice 446256000 twice with the same code (11b081c), store and memory limit and got 64,907 vs 64,902 rows: 261 rows only in the first run and 256 only in the second (about 0.4%), 251 of them the same (wallet, key, t0) with different boundaries (merged or split episodes, another end slot), mostly in reversed pools (169) and on the curve (21). Cause: `legs_sql` ordered by (wallet, key, slot, tx_index) only, so two legs of one wallet and key inside one transaction (a buy and a sell in one tx, several events) tied, and `walk()` is order-sensitive. Fixed in `episodes.py` by ordering on the instruction position in the transaction (outer_ix, inner_ix) and then the leg's content, and the same for the mark price's "last trade of the slot"; a test runs `legs_sql` on a mini store with the legs inserted out of order. The full-period episodes-v1 and the follow replay that is running on them were built before the fix; the 0.4% boundary noise is far below every effect reported here, so that run is not repeated. The next episode run (and the hold-out mechanics) uses the fixed order.

## Resource notes

Pilot on the VPS, every step in its own scope (2 GB, 2 cores): episodes 13 slices of 4 h, 1–5 min each, ~1.1 GB; follower replay 12 slices, ~2–3 min each, 1.5 GB, 46,000 positions in total; runners 12 slices of ~12 s; the score for one day ~1 min. The session was OOM-killed once when queries ran inside the session scope; since then nothing runs outside a scope.

## Next

- NAS: `nas_step1.sh` (episodes → candidates → follower replay with 7 workers → daily scores); then the same tables over 18.7 days, with the leaderboard page fed from `scores-v1/leaderboard-<D>.parquet`.
- Runners over the full period, walk-forward per day, with the forward targets; then the wallet score of the early buyers as a feature once the scores exist.
- The owner's Phantom examples as cases once epochs 1052+ are in a store (forward data: illustration only).
