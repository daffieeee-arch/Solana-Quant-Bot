# E76 Simfee-probe: rekent paper's fill-model goed op de live staat? Eén evaluatie onder regel v3 (2026-10-24)

| | |
|---|---|
| vraag | Krijgt een echte swap op de live staat precies wat paper's engine uitrekent, en hoe vaak mislukt hij door de markt? Elke live paper-in- en uitstap van Terugveer snel (K3, F7 tp4_T600) en groep H (flow300_H6, flow300_tp100_H6) werd gesimuleerd met simulateBundle, zonder sleutel, zonder ondertekenen en zonder iets te verzenden. |
| regel | `data-old-faithful-one/lab/research/simfee-probe-rule-2026-10-23.md` v3, sha256 **48ac7ed3706acfe273ff5ddf721bd449355e35aa460e3cc03ffc7e56c6172cce**. Het bestand staat buiten Git en is vastgelegd vóór de eerste betaalde aanroep (LAB review van #538). De beslisregel komt woordelijk uit `research/triton-services-edge-2026-10-20.md` §2.5. |
| code | `lab/paper/simfee_probe.py` (#538, gemerged als 6226928). De volgrun liep op main e1733e9 (`lab-simfee-probe.service`, van 23 okt 19:17Z tot de stop op 24 okt). De evaluatie (`--summary`) draaide op main 360fb99. |
| archief | `data-old-faithful-one/lab/probe/simfee-eval-2026-10-24/` met SHA256SUMS:<br>• summary.json **a48f918d**564d1993bf7ff02e622afcad8f32aed0e70dc54e5785a98b86c23318<br>• sims-1915Z.jsonl 2e48f83f… (64 rijen, de eerste test)<br>• sims-1917Z.jsonl bd744642… (271 rijen, de volgrun)<br>• COMMIT 11e54048… |
| data | Het live paper-journal na Alpenglow (t ≥ 459.648.000), journaaldagen 23–24 okt. De simulaties draaiden op de live mainnet-staat via Triton. Geen OF1, geen schone data. |
| kosten | 1.293 aanroepen, ≈ $0,014, 0 keer HTTP 429:<br>• 23 okt: 8 (5 simulaties van de eerste test, 3 fee-aanroepen);<br>• 24 okt: 1.285 (265 simulaties, 1.020 fee-aanroepen).<br>Bron: de meter (`probe/simfee/state.json`) en `events.log`. |

Labels: feit / reconstructie / modelaanname / onbekend. M en de klassen zijn **feiten** (simulatie tegen de eigen rekensom op dezelfde staat). D is een **feit** als getal. De uitleg ervan is een **hypothese**.

## Kort

- **De rekenkern van het fill-model is exact; de probe meet daarmee geen modelfout meer, alleen drift.**
  - M is 0,000 bps op alle 255 geslaagde simulaties (137 instappen, 118 uitstappen). Het bootstrap-95 %-interval is [0; 0].
  - Op elke rij is de gesimuleerde uitkomst gelijk aan de eigen som van de engine op dezelfde staat (`sim_out` = `eng_same_state`).
  - Regel v3: geen systematische afwijking (|mediaan M| ≤ 25 bps) en een uitval van 0 % (≤ 3 %). **Het fill-model bevestigt de aanname. Het krijgt geen correctie en er komt geen nieuwe trial voor.** Paper blijft ongewijzigd.
- **D (alleen rapport): drift tussen de vulling in paper en de simulatie.** De simulatie liep een mediaan 329 slots (≈ 70 s) na de vulling.
  - **Instap:** mediaan **−317 bps** (q25 −438, q75 −173; n = 133 met een D).
    - 9 van de 137 instappen (6,6 %, in 8 van de 37 pools) kregen bij de simulatie vrijwel niets: D ≤ −9.999 bps, waarvan 6 precies −10.000.
    - Bij 28 van de 137 instappen (20,4 %) zou de simulatie onder de instaptolerantie van 5 % vallen (would_miss_tol).
  - **Uitstap:** mediaan **+227 bps** (q25 +133, q75 +338; n = 118). Hier is D het model op de nieuwste staat **vóór** onze acquire, tegen de vulling in het journal. Onze acquire zit er dus niet in. would_miss_tol bestaat alleen voor de instap.
  - |mediaan D| > 25 bps terwijl M niet systematisch is. Volgens de leesregel (§2) is dat **een timing- of driftbevinding. Die hoort in een eigen, vooraf vastgelegde trial**, niet in een correctie van het fill-model.
- **Uitval, structureel en rpc: alle drie 0 %** aan beide kanten.
- **Dekking** (ok / (ok + buiten de steekproef), zonder de 5 test_late-rijen van de eerste test):
  - instap 137 van 173 (79 %), uitstap 118 van 157 (75 %);
  - de summary telt de 2 test_late-uitstappen mee bij acquire_market, wat 118 van 159 (74 %) geeft: de 74 % uit LAB's lezing;
  - op de verse rijen van 24 okt: 137 van 141 (97 %) en 118 van 130 (91 %);
  - het verschil is de backlog van 23 okt: 59 te late rijen (geen aanroep) plus de 5 test_late-rijen (zie §1).

## 1. Wat gemeten is (regel v3 §2)

- **M** (beslisgrootheid), per kant: 1e4 × (gesimuleerde uitkomst − de eigen som van de engine op dezelfde staat) / engine.
  - Instap: tokens uit `sell(size)` in een reversed pool, tegen `backtest.venues.rev_buy_token`.
  - Uitstap: WSOL uit `buy_exact_quote_in(T)` op de staat ná onze eigen acquire, tegen `rev_sell_token`. De betaler heeft geen tokens en een simulatie kan de staat niet overschrijven.
- **Systematisch** = |mediaan M| > 25 bps en het bootstrap-95 %-interval (10.000 trekkingen, seed 7) sluit 0 uit, met ≥ 30 simulaties per kant.
- **Uitval** = mislukkingen die de marktstaat verklaart / (geslaagd + die) > 3 %.
- **Buiten de steekproef** (geteld per klasse en reden):
  - te laat: de vulling ligt meer dan 1.500 slots achter de recorder;
  - overgeslagen;
  - `acquire_market`: de acquire van de uitstap mislukte, dus de uitstap zelf is niet geprobeerd.
- **Eén rij per positiekant**, telkens de eerste. De eerste test (`sims-1915Z`, 23 okt) probeerde 5 te late rijen: 3 instappen (ok) en 2 uitstappen (acquire_market, 6004). Alle 5 zijn gemarkeerd (`test_late`) en vallen buiten de regel.

| kant | geprobeerd | ok | M mediaan [95 %] | uitval | structureel | rpc | te laat | overgeslagen | acquire_market | D mediaan | would_miss_tol |
|---|---|---|---|---|---|---|---|---|---|---|---|
| instap | 137 | 137 | 0,000 [0; 0] | 0 % | 0 % | 0 % | 35 (32 backlog 23 okt) | 1 (unsupported_remaining_accounts) | – | −317 bps | 28 (20,4 %) |
| uitstap | 118 | 118 | 0,000 [0; 0] | 0 % | 0 % | 0 % | 29 (27 backlog 23 okt) | – | 12 (budget_cap 4, model 8; waarvan 2 model test_late van 23 okt; de volgrun 10: budget_cap 4, model 6) | +227 bps | n.v.t. (alleen instap) |

Samenstelling van de ok-rijen:
- instap: tp4_T600 123, flow300_H6 9, flow300_tp100_H6 5;
- uitstap: 110, 5 en 3.

K3 en H zijn samen genomen (regel v3). De 4 instappen zonder D zijn instappen die de engine liet mislukken (de tolerantie van 5 %). Die zijn wel gesimuleerd: M is daar ook 0.

## 2. Lezing (LAB review) en wat volgt

- **Fill-model:** de rekenkern is exact. Deze probe meet daarmee geen modelfout meer, alleen drift. Er komt geen correctie en geen nieuwe trial. De lopende paper-configuratie verandert niet.
- **D** is een timing- of driftbevinding. Of de drift een eigen trial wordt, is een aparte keuze met een eigen pre-registratie. Hier is niets gestart.
- **Prioriteitsfees** zijn alleen rapport. De 1.023 fee-aanroepen (3 op 23 okt, 1.020 op 24 okt) staan in `probe/simfee-20261023-1917Z/fees.jsonl`, niet in het eval-archief, en zijn voor deze noot niet gelezen. Een ander kostenscenario zou een nieuwe trial zijn.
- **De probe is gestopt** na deze ene evaluatie (coördinator, 24 okt).

## 3. Kanttekeningen

- **D bij de uitstap bevat onze eigen acquire niet** (regel v3 §2; in de data op alle 118 rijen D = het model op de staat vóór de acquire).
  - Het prijseffect van de acquire (1,5 × de modelmaat, 0,13–1,34 SOL) zit alleen in de simulatie ná de acquire, een mediaan +93 bps boven die schatting (+19 … +145). Dat raakt alleen de staat waarop M wordt gemeten, en M rekent op die identieke staat.
  - D hangt samen met de grootte van de acquire (Spearman 0,55, n = 118). Dat past bij drift: de acquire wordt bepaald op de nieuwste staat en is dus groter als de tokenprijs sinds de vulling is gestegen (hypothese).
- **Beide D's wijzen dezelfde kant op** (hypothese, geen toets): ≈ 70 s na de vulling is de token duurder in SOL. Een instap krijgt dan minder tokens (−317 bps), een uitstap meer SOL (+227 bps). Daarbij komen pools die leeglopen (9 instappen vrijwel niets).
  - De simulatie liep ≈ 70 s na de vulling. Live koopt paper binnen ~0,6 s (d_ms 600).
  - De −317 bps is dus geen schatting van paper's eigen slippage. Het is wel een aanwijzing hoe snel de staat na een trigger wegloopt.
- **De steekproef is één live dag na Alpenglow**, met K3 en H samen. Bij beide kanten is ≈ 90 % van de rijen tp4_T600. Voor H afzonderlijk zijn de aantallen klein (14 instappen, 8 uitstappen).
- **De dekking van 79 % / 75 %** (74 % in de summary) telt de backlog van 23 okt mee: 59 te late rijen en 5 test_late-rijen van de eerste test, alle buiten de regel. De verse dekking is 97 % / 91 %.
