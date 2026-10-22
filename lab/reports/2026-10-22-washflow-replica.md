# Wash-stroom-populatiereplica (E59): M6 is gesloten voor ons

**Vraag (E57, M6):** de wash-stroom-wallets van money-v1 (farmer-pool-dagen, marge ≥ 0,5 % op de fit-helft 11–20 sep) verdienen op de test-helft 21–30 sep +5.090 SOL eigen cash. Blijft er iets over als wij hun legs met ónze maat (0,25 SOL via f_w), zónder eigen impact en mét netwerkkosten per leg nabootsen? Pre-registratie `research/prereg-washflow-replica-2026-10-22.md` v1.2 (sha256 3f2093e0…), bevroren vóór de run; pin 2de5455.

**Antwoord in één zin:** nee — de primaire maat (variant iii, k 3, base-fees, alle wallets) is **−213,06 SOL/dag [−236,38; −187,80], 0 van 10 dagen > 0, −0,001629 SOL per leg**; alle vier de poortvoorwaarden van §5 zijn gemist, dus **M6 GESLOTEN** (de uitkomst die de pre-registratie als verwachting noemde).

Labels: **feit** = berekend op de genoemde data; **reconstructie** = afgeleid; **modelaanname** = aanname van het rekenmodel; **onbekend** = niet gemeten.

## 1. Run

| | |
|---|---|
| NAS-job | 99zzzzz1-m512-washflow-e59-2de5455 (`bash lab/wallets/nas_washflow.sh`, rc 0, 2026-10-22 00:54Z, 19,6 s rekentijd); uitvoer `wallets/washflow-2de5455/` |
| tests | 4 passed (`tests/test_washflow_replica.py`) |
| selectie | fit-helft slots 446.256.000–448.886.968: **29 wallets** (`selected.parquet` sha256 d80c2bc6…); 26 ervan handelden in de test-helft 448.886.969–452.119.503 |
| legs | **1.307.925** eigen legs van de 26 op 10 volle UTC-dagen (`legs-shift.parquet` 3150a2d2…), 38.965.719 pool-legs als p_k-kandidaten; `legs_without_pools_row` 0, `legs_outside_blocks` 0 (feit) |
| verslag | `washflow.md` 30e4b7ea…, `washflow.json` 7b0c809d…, `run-washflow.json` 804941c4… |
| maat | f_w = min(1, 0,25 / p90 kooplegs fit-helft) per wallet; geschaalde koopomzet 100.605 SOL; eigen cash test-helft +5.089,7 SOL (ongeschaald), +885,4 geschaald (feit) |

## 2. Uitkomst tegen de poort (§5 pre-registratie)

| voorwaarde (iii, k 3, base-fees) | waarde | gehaald |
|---|---|---|
| netto per leg > 0 ná fees | −0,001629 SOL | nee |
| 95 %-dag-ondergrens > 0 | [−236,38; −187,80] SOL/dag | nee |
| ≥ 7 van 10 dagen > 0 | 0/10 | nee |
| zonder top 5 > 0 | −137,54 SOL/dag | nee |

Per SOL geschaalde koopomzet: −0,0212 SOL (feit, modelaanname voor impact s²/D_k en de fee per leg).

## 3. Wat de varianten zeggen

- **Beste cel waar dan ook:** k 1, variant (ii) (zonder eigen impact), min-fees 5.000 lamports, alle wallets: −25,42 SOL/dag [−40,33; −11,72], 1/10 dagen > 0. Zelfs het gunstigste model (geen impact, bijna gratis transacties, de kortste horizon) blijft negatief (feit).
- **Fees zijn niet de hele verklaring:** (ii) zonder fees bij k 3 is al −182 (base) / −88 (min) SOL/dag; de prijsbeweging ná de eigen leg is tegen de richting van de leg (reconstructie: hun cash komt uit het samenspel van eigen impact en tegenpartijen in dezelfde slot, niet uit een voorspelbare prijsdrift).
- **Eigen impact meegenomen (i)** is nog slechter (−375 bij k 3): op 0,25 SOL-maat betaalt de nabootser zijn eigen prijsbeweging en krijgt die niet terug.
- **Per wallet:** alleen Ar2Y6o1Q is +2,2 SOL op de primaire maat (25.630 legs, eigen cash +533); de grote (2tgUbS9U −306,6, FB5jAs5Q −270,7) dragen het verlies (feit). Eén positieve wallet van 26 is geen lead: de poort is populatiebreed en de wallet is ná de run aangewezen (post hoc).

## 4. Besluit en wat het niet zegt

- **M6 gesloten:** de wash-stroom-populatie is geen nabootsbare rand voor ons. Geen vervolgtoets, geen herformulering, geen paper-arm.
- Niet gezegd: dat deze wallets niets verdienen (hun eigen cash is +5.090 SOL op de test-helft, feit) — hun winst zit in iets wat de replica per constructie niet heeft: identiteit, eigen pools, timing binnen de slot en/of bundeling (onbekend).
- Looks: deze run is de ene vooraf vastgelegde toets; disclosed looks vóór de freeze staan in §8 van de pre-registratie (één wallet). Teller + 1.
- Volgende stap uit E57 blijft (c): de ontleding van Ge8ztT6V (verkennend, dev).
