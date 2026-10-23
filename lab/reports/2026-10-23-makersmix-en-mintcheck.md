# Live triggermix per makersklasse en de mint-verkoopbaarheidscheck (E69, 23 oktober 2026)

Twee opdrachten van de coördinator na de onafhankelijke controle van 23 oktober (`research/onafhankelijke-controle-2026-10-23/rapport.md` sha256 28b4fae5…, `criticus.md` e06f6a99…): (1) de mint-verkoopbaarheidscheck die de eigenaar op 17 oktober goedkeurde ("mint rugcheck", spec `research/spec-mint-rugcheck-live-2026-10-17.md`) maar die nooit is uitgevoerd, en (2) de live triggermix per makersklasse, als tegenhanger van de bevinding van de criticus dat de dev-winst van Terugveer lang (H5) vrijwel geheel bij staartmakers zit. Beide zijn **tellingen zonder P&L**: geen F7/H-regel wordt live beoordeeld, geen rendement wordt berekend, niets gaat naar paper of de engine.

Archief (buiten git): `research/creator-mix-live-2026-10-23/` (mix.py 0105a613…, mix.out da9164b4…, mix.json 2f061b2c…; SHA256SUMS a85aaf19…). Mint-check: `lab/wallets/mint_check.py` (list/fetch/report, 6 tests) en `wallets/mint-check-2026-10-23/mints.parquet` (4b7f0fef…). Elk getal hieronder staat in mix.out, tenzij "(niet gepind)".

## 0. Kort

- **Feit:** van de 586 F7-triggers in het explore-venster (21 okt 15:00Z – 23 okt 00:00Z) komt **77,8 %** (456, op 17 pools) uit pools waarvan de `coin_creator` het system program is. Op dev waren dat **16,3 %** van de K3-triggers en **20,8 %** van de H5-trades. Dit is precies de klasse waar de criticus de dev-H5-verliezen (−10,7 SOL) en de daling aan het eind van dev vond.
- **Feit:** de staartklasse (makers met < 10 pools in de dev-H5-set, +146,9 SOL op dev) is live **afwezig**: 0 van de 586 triggers komt van een maker uit die set met < 10 pools; op de ruimere dev2-telling (1–9 reversed pools op dev) zijn het 6 triggers (1,0 %) op 5 pools.
- **Feit:** 17,1 % van de live triggers (100, 44 pools) komt van makers die in de hele dev-periode geen enkele reversed pool maakten ("nieuw"); 5,1 % (30, 11 pools) van herhaalde operators (≥ 10 pools). Bij de probe-pools zelf is de verdeling heel anders: 91,5 % nieuw, 6,4 % system program, 1,3 % herhaald.
- **Hypothese:** het live aanbod is niet alleen kleiner (E67: 0,29× dev) maar ook van een andere soort: system-program-pools leveren live 26,8 triggers per pool (dev 12,7), en de top 10 pools (alle system program) dragen 367 van de 586 triggers (62,6 %). Als de dev-H5-winst klassegebonden is, zoals de criticus beschrijft, dan zit de klasse die hem droeg niet in het huidige live aanbod. Of dat ook voor de schone dagen 1055–1063 geldt, is pas na zaterdag te zien (dezelfde telling, geen P&L).
- **Mint-check:** 104 H/K3-pools (K3, H4 en H5 delen dezelfde triggerset) = 104 mints, plus 324 N2-pools en 1.272 probe-pools → 1.367 mints = **14 `getMultipleAccounts`-calls** (5 voor alleen het journal). De calls zijn geteld vóór ze gemaakt worden en nog **niet uitgevoerd** (de coördinator draait de fetch-stap; §3 wordt dan aangevuld). De "~226" uit de audit was het aantal Token-2022-mints onder 283 reversed pools in segmenten 4800–5100, niet het aantal H/K3-mints.

## 1. Data en definities

- **Live:** de explore-parquets van E67 (`wallets/regime-1064-inputs/`): 1.282 probe-pools (`probe-pools-explore.parquet`) en 586 F7-triggers op 72 pools (`live-triggers-explore.parquet`; geen enkele trigger is als farm gevlagd). Daarnaast het paper-journal `signals` (F7 tp4_T600, t ≥ 459.648.000: 713 triggers op 104 pools, explore + forward, alleen pool en status). De `coin_creator` per pool komt uit de journal-tabel `learned` (wat de engine zelf uit CreatePoolEvents en de store-snapshot leerde); 10 van de 1.282 probe-pools hebben geen learned-rij ("onbekend").
- **Dev-referentie:** dev2-store 1033–1046 (verbruikt): tabel `pools` (54.027 reversed pools, 20.524 makers, 19.288 system program = 35,7 %) en de E63-parts (`backtests/capital-ceiling-d15ac45`): de H5-tradeset van de criticus (flow300_tp100_H6, organic, d 3, 0,5 SOL: 2.824 trades, 1.852 pools, 571 makers) en de K3-triggers (tp4_T600, alle farms, d 3, 0,5 SOL: 37.831 triggers op 3.341 pools).
- **Klasse A (de criticus):** per maker het aantal pools in de dev-H5-tradeset: `sys` = system program, `repeat` ≥ 10, `tail` < 10; een live maker die niet in die set zit = `nieuw`. Deze klasse kijkt op dev vooruit (telling over de hele periode) en is beschrijvend, geen filter.
- **Klasse B (point-in-time bruikbaar):** per maker het aantal reversed pools in dev2 (vóór de live periode): `sys`, `repeat` ≥ 10, `tail` 1–9, `unseen` 0.
- **System program als coin_creator (reconstructie):** bij `create_pool` van een niet-pump-token geeft de maker `coin_creator` zelf op (in het voorbeeld in segment 8093 is dat de fee-payer); het system program betekent dat er geen ontvanger van de creator-fee is gezet. Het is dus een keuze van de maker, geen eigenschap van het token.

## 2. Tellingen

Aandeel per klasse (A = criticus; n = triggers of trades, pools = distinct pools):

| verzameling | n | pools | sys | tail | repeat | nieuw / unseen |
|---|---|---|---|---|---|---|
| dev H5-trades (A) | 2.824 | 1.852 | 587 (20,8 %) | 1.435 (50,8 %) | 802 (28,4 %) | – |
| dev H5-triggers organic d 3 (A) | 3.325 | 1.960 | 609 (18,3 %) | 1.747 (52,5 %) | 908 (27,3 %) | 61 (1,8 %) |
| dev K3-triggers alle farms d 3 (A) | 37.831 | 3.341 | 6.175 (16,3 %) | 13.998 (37,0 %) | 10.996 (29,1 %) | 6.662 (17,6 %) |
| **live F7-triggers explore (A)** | **586** | **72** | **456 (77,8 %)** | **0** | **30 (5,1 %)** | **100 (17,1 %)** |
| live F7-triggers explore (B) | 586 | 72 | 456 (77,8 %) | 6 (1,0 %) | 30 (5,1 %) | 94 (16,0 %) |
| live probe-pools explore (A) | 1.282 | 1.282 | 82 (6,4 %) | 0 | 17 (1,3 %) | 1.173 (91,5 %) + 10 onbekend |
| live probe-pools explore (B) | 1.282 | 1.282 | 82 (6,4 %) | 145 (11,3 %) | 17 (1,3 %) | 1.028 (80,2 %) + 10 onbekend |
| journal F7 t ≥ 459.648.000 (A) | 713 | 104 | 543 (76,2 %) | 0 | 39 (5,5 %) | 131 (18,4 %) |
| dev2 reversed pools (B) | 54.027 | 54.027 | 19.288 (35,7 %) | 24.833 (46,0 %) | 9.906 (18,3 %) | – |

Op dev betekent `nieuw` in klasse A "maker niet in de H5-tradeset" (de K3-triggers op pools waar H5 nooit vulde); live betekent het hetzelfde, en in klasse B zijn 94 van die 100 triggers van makers zonder enige dev-pool.

Triggers per pool (feit): live sys 26,8, nieuw 2,3, repeat 2,7; dev K3 sys 12,7, tail 10,3, repeat 25,1, nieuw 6,3. De top 10 live pools naar triggers (64, 61, 47, 34, 33, 29, 28, 26, 25, 20) zijn alle system program: 367 van 586 (62,6 %). Live makers: 35 achter de triggers, 583 achter de probe-pools; de enige herhaalde operator met gewicht is HgzeyuDt… (30 triggers, 118 reversed pools op dev).

Journal-status per klasse (feit, t ≥ 459.648.000; "entered" is de paper-instap, geen P&L gelezen): K3 entered 223 = sys 108, nieuw 95, repeat 20 (skipped 490, waarvan sys 435); H4 entered 10 = nieuw 7, repeat 2, sys 1; H5 entered 4 = nieuw 3, repeat 1. De H-armen sloegen dus 699 van 713 triggers over (de gedeelde plekken en dagverlieslimiet; audit punt 5), zodat de paper-H-meting op dit venster vrijwel geen instappen heeft.

## 3. Mint-verkoopbaarheidscheck (status: lijst klaar, calls nog niet gemaakt)

Wat gecheckt wordt (spec §1), per mint via één `getMultipleAccounts` per 100 mints over het bestaande token-meta-pad (`backtest.token_meta_rpc.rpc_accounts`, Triton RPC, dezelfde dagcap van 5.000 als de sidecar, die vandaag op 995 calls stond):

- basis (SPL Token én Token-2022): `mintAuthority` gezet, `freezeAuthority` gezet, supply, decimals;
- Token-2022-extensies (TLV na byte 165, type-nummers volgens solana-program/token-2022): transfer fee (basispunten en maximum uit `newer_transfer_fee`), transfer hook (programma gezet), permanent delegate, non-transferable, default account state = frozen, pausable (en `paused`); informatief: mint close authority, metadata pointer, token metadata, interest bearing, scaled UI amount, confidential, permissioned burn, overige.
- "veto" in de tabel = één of meer van de spec-vetovlaggen; dat is een telling, geen regel: een veto in paper is een aparte vastlegging na een eigenaarsbesluit (spec §4).

Omvang (feit, `mint_check list`): journal t ≥ 459.648.000: K3/H4/H5 104 pools = 104 mints (49 makers, 21 system program); N2 324 pools (20 overlap met F7) → 408 journal-mints = 5 calls; met de 1.272 probe-pools (1.271 mints) 1.367 mints = 14 calls. De ruwe accountbytes worden bewaard (accounts.parquet), zodat de parse herhaald kan worden zonder nieuwe calls. Pump.fun-mints zijn zelf ook Token-2022 (metadata pointer + token metadata, update authority TSLvdd1p… in de recorder-tabel token_meta; reconstructie): "Token-2022" alleen is dus geen vlag; de telling splitst op de extensies.

Tellingen per vlag en per familie: **volgen zodra de fetch gedraaid is** (coördinator), in een vervolgcommit op deze noot met pins van accounts.parquet, flags.parquet en counts.json.

## 4. Wat dit niet is

- Geen beoordeling van F7/K3/H op live data, geen rendement, geen filter: de klassen zijn beschrijvend; klasse A kijkt op dev vooruit (de criticus zei dat al), klasse B is wel vooraf bekend maar laat live 80 % van de makers als `unseen` zien, zodat ze als filter weinig zou scheiden.
- Geen uitspraak over de schone dagen 1055–1063 (gesloten tot zaterdag ≥ 12:00Z) of over epoch 1064 als forward-venster: dezelfde telling kan daar na E56 herhaald worden, alleen tellingen.
- De oorzaak van het verschil (markt veranderd ná Alpenglow, of ons probe-ontwerp dat pools ≥ 120 SOL volgt en zo op zware, veelgetriggerde pools uitkomt) is niet vastgesteld; E68 stap 2 (OF1 1064, één dag) telt het aanbod zonder ons ontwerp en kan die twee uit elkaar halen.
- Blikken: 0 (geen rendement berekend); de teller blijft ≈ 2.302.
