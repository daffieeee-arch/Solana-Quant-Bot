# E20 op schone data: wash-trigger-toets H1/H2 op K3 (2026-10-24)

Sessie LAB research (#167). Hypothese sha256 `35bdaa0d…`, aanvulling v5 `94d09d9f…`, uitsluitingen `ccfaed98…`; engine-pin be47fc4 (`engine_commit` be47fc425fc46942884db256e5c9d809a11d57a9); store clean sha256 `e8db6e8ddf0bd88cab038ef46d9dff34049635ec5e2fca4efd7c11afcf79a7df`; extractor `f5dd11df1aadd2f5d1da1ede39bf919b27b980eb9d656eea7c9a6fdb2f32a1b5`. Labels: **feit** / **reconstructie** / **modelaanname** / **onbekend**.

## 0. Uitslag

**Beslissing (vooraf vastgelegd): risicofilter zonder bewezen winst.** (reconstructie met het kostenmodel)

| toets | n | waarde | 95 %-interval (dagbootstrap 10.000, seed 7) | geslaagd |
|---|---|---|---|---|
| H1: K3 zonder rt_tx, gemiddeld nettorendement per gevulde trade | 3557 | -0,11 % | -0,86 % … +0,32 % | nee |
| H2: zonder − met rt_tx | | +2,76 % | +0,70 % … +5,19 % | ja |
| (K3 met rt_tx) | 867 | -2,88 % | -5,18 % … -1,21 % | |

- Gevulde K3-trades 4424 op 10 handelsdagen (minimum 5); met rt_tx 867, zonder 3557, zonder trigger-leg 0; mislukte instappen 425 (netto -0,2146 SOL).
- Venster slots [455760000, 459216000), warm-up-epochs 1; d per epoch {'1055': 3, '1056': 3, '1057': 3, '1058': 3, '1059': 3, '1060': 3, '1061': 3, '1062': 3}.
- Gelijkheid toets-run en matrix-run per epoch: 1055: d=3, 644 = 644, 1056: d=3, 709 = 709, 1057: d=3, 748 = 748, 1058: d=3, 721 = 721, 1059: d=3, 794 = 794, 1060: d=3, 608 = 608, 1061: d=3, 290 = 290, 1062: d=3, 335 = 335.
- Uitsluitingen: 219 mints, 16 pools; 0 triggers, 0 van 4849 K3-posities vervallen.
- wash_1h (beschrijvend): wash_1h n=897 -2,99 % [-5,22 % … -1,35 %]; geen wash_1h n=3527 -0,06 % [-0,79 % … +0,37 %]; geen rt_tx en geen wash_1h n=3512 -0,05 % [-0,78 % … +0,38 %].
- a4c op de matrix-run tegen backtest.wash: equal.

## 1. DSR en PBO (voorbehoud, geen beslisregel)

- DSR H1-reeks (N = 42, T = 10): dev-variantie 0,031, 1/T 0,004; PSR 0,324; K3 bij de Clock-d (beschrijvend) 0,000 / 0,000.
- PBO (CSCV, 43 kolommen, T = 10, 10 blokken): 0,000.

## 2. Kanttekeningen

1. De clean-data zijn pre-Alpenglow; de stopregel-klok van K3 (E14) loopt op paper en wordt hier niet beïnvloed.
2. E20 zegt niets over H3/H4/H5 (eigen vastleggingen); een positieve H bij een negatieve E20 is geen redding van K3.
3. Reconstructie met het kostenmodel; DSR/PBO zijn voorbehoud, geen beslisregel.
4. Dagdefinitie: E20 telt per UTC-dag van de trigger-slot (`tournament._days`), H3/H4/H5 per UTC-dag van de entry-slot (trigger + d ≈ 0,6 s); rond middernacht reproduceert de beschrijvende tp4_T600-dagreeks hier die van E20 dus niet exact.

## 3. Reproductie

`research/clean-test/<E20-run>/result.json` en `result.md` (clean_test, Lab Backtest); deze noot leest alleen die bestanden.
