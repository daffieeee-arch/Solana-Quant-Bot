# B5 — afzonderlijke buy_exact_quote_in_v2, opgenomen feiten

> **Document status: ACTIVE.** Lokale offline vervolgstap vanaf
> `952dc0000b961d429ba19aa074eca477cdeaf327`. Geen acquisitie, GitHub-verkeer,
> installatie, merge of delivery-/Evidence-promotie. Research Ready blijft false.

## Afzonderlijke bron en profiel

De bronreceipt [pump-buy-exact-quote-v2-evidence.json](../../rust/of1-bronze-decoder/sources/pump-buy-exact-quote-v2-evidence.json)
bindt uitsluitend reeds behouden officiële bronbestanden. De primaire
[Pump-IDL](https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json)
heeft commit `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`, blob
`062e66f032bb9f295353b573be3400070bd55e5b` en SHA-256
`b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.
De behouden officiële SDK 1.36.0-tarball heeft SHA-256
`e3e612d9bf0be5f1a443d321aee51b044b5e98408170c3eb8baebf6346f569aa`;
leden, relevante accountbouw en PDA-definities zijn afzonderlijk gehasht.
De SDK Git-head blijft registry-declared; de gehele ongelicenseerde IDL
wordt niet in Git gevendord. Deze bronidentiteit bewijst geen activatierange.

Het nieuwe profiel is
`pump-buy-exact-quote-v2-9c82f61-token2022-native-sol-direct27-v1`.
Discriminator `c2ab1c46684d5b2f`, exact 24 bytes, twee u64-argumenten:
`spendable_quote_in` en `min_tokens_out`. Er is **geen** instructieargument
`track_volume`. Geen legacy-buy/sell-argumenten of accountposities worden
overgenomen, geen bytes verwijderd of toegevoegd.

Alle 27 rollen zijn expliciet gecontroleerd: vier vaste adressen,
vijftien IDL-PDA-afleidingen en één aanvullende SDK-user-ATA-correspondentie.
De begrensde route ondersteunt alleen directe Pump-aanroepen met bewezen
message-signercapaciteit en vereiste writable-capaciteit. Order en stack
heights verbinden het unieke eigen event aan die aanroep, niet aan een
andere outer of een geneste sibling. Onbekende werkelijke event-CPI-flags
blijven UNAVAILABLE. Dit is geen nieuwe algemene routerarchitectuur.

## Broneis, opname en onbekende accountstaat

| Onderdeel | Wat wordt gecontroleerd en behouden | Wat niet wordt bewezen |
|---|---|---|
| Creator-vault | PDA-adres correspondeert met de opgenomen `event.creator`; IDL verwacht `bonding_curve.creator` als seed | Ontbrekende bonding-curve-accountinhoud of gelijkheid daarvan met het event |
| Fee-/buybackrecipient | Opgenomen adressen, eigen eventrecipient en behouden officiële adreslijsten corresponderen | Historische Global-/feeconfig-inhoud, feitelijk saldo of juiste afrekening |
| Quote | De instructie noemt WSOL als quote-interfaceaccount; het event bevat letterlijk een nuladres | Een stilzwijgende vervanging van eventquote, bewezen decimals of economische normalisatie |
| Argumenten | Aangevraagde quotegrens en minimum tokens, exact als u64 | Dat de limiet gelijk is aan werkelijk betaald bedrag of fee-inclusieve kosten |
| Event | Volledige brongepinde TradeEvent-layout, beide discriminators en volledige consumptie; eigen mint/user en ruwe grensconsistentie | Account-reserves, gecommitteerde toestand, prijs, fill of nettoproceeds |
| Status | Oorspronkelijke transactie-status blijft aanwezig; alleen opgenomen OK kan toelaten | Nieuwe cryptografische signaturecontrole of historische activatie |

De volledige events zijn 381 bytes inclusief event-CPI-prefix. Het opgenomen
`ix_name` is letterlijk `buy_exact_quote_in`. De IDL definieert dit als string;
de oude beschrijvende opsomming van drie namen is geen wire-enum. Het label
wordt niet hernoemd naar `buy` en geen oude parser wordt verruimd.
`track_volume` wordt alleen als afzonderlijk eventfeit behouden.
Ruwe tokenhoeveelheid versus `min_tokens_out` en gerapporteerde SOL-accounting
versus `spendable_quote_in` zijn consistentiecontroles, geen handler-/prijsbewijs.

## Canonieke route en query

Rust registreert de uitgevoerde controles onder
`transaction.pump_buy_exact_quote_v2_analysis`. Kandidaatselectie komt uit
de volledige instructie/account/eigen-event/status-predicaten, niet uit een
caller-supplied count of slot-/indexallowlist. Een passende opname krijgt
schema `PUMP_SILVER_RECORDED_BUY_EXACT_QUOTE_V2_1`, afzonderlijk van het
bestaande sell-schema. Beide behouden het oorspronkelijke Bronze-parent,
Raw/receipt/planbinding, bytes, volgorde en native sample-identiteit.

De Rust-Parquetprojectie accepteert uitsluitend de twee benoemde Silver-
schemas. `spendable_quote_in_raw_u64` en `min_tokens_out_raw_u64` zijn eigen
getypeerde kolommen; sell-argumentkolommen zijn voor buys afwezig, niet nul.
Gerapporteerde token-, SOL- en quotehoeveelheden blijven afzonderlijke velden.
Alle oorspronkelijke JSON-recordbytes, NULL/MISSING en bronvelden blijven
verliesloos behouden en worden na schrijven tegen de kolommen gecontroleerd.

DuckDB leest uitsluitend de manifestlijst. `buys` en `sells` tellen de kanten
afzonderlijk; `exact_quote_buy_details` houdt ook de mislukte kandidaat zichtbaar.
Python telt alleen Rust-feiten en voert geen Pump-decode of Silver-toelating uit.
Het bestaande browserrapport toont instructiegrenzen, gerapporteerde waarden,
eigen context, bronhashes en bewijsbeperkingen. Een mint met slechts één kant
bewijst niet dat op de keten de andere kant ontbreekt.

De andere Buy320-, 26-byte-buy-, nested25- en Mayhem-afwijzingen blijven
afzonderlijk. De selectie, seed en algoritme veranderen niet. Het vervolg
is dekking per mint en vereiste aaneengesloten tijdscontext meetbaar maken,
niet automatisch extra data ophalen of een nieuwe selectie kiezen.

Grenzen blijven maximaal 2 GiB procesgeheugen, 4 GiB nieuwe artefacten en
5.000 records / 64 MiB per fysiek Parquetbestand. Oude runbestanden,
acquisitiebinary, rapporten en stop-evidence worden niet herschreven.

## Uitgevoerd lokaal resultaat — 2026-09-14

Codecommit `4420bd25ec18c2edbb3ef11a95aef5906a019025` is tweemaal uitgevoerd
op de ongewijzigde pilot. Dit zijn authentiek opgenomen instructie-/eventfeiten,
niet synthetisch ingevulde transacties. Negatieve grensgevallen in tests
blijven afzonderlijke Fixture-evidence.

| Slot | Bronze-pakketten | Opgenomen OK / ERROR | Nieuwe buys | Behouden sells |
|---|---:|---:|---:|---:|
| 422669516 | 1.022 | 973 / 49 | 1 | 2 |
| 422669517 | 1.048 | 972 / 76 | 1 | 0 |
| 422669518 | 1.154 | 1.056 / 98 | 2 | 1 |
| Totaal | 3.224 | 3.001 / 223 | 4 | 3 |

Alle pakketten zijn gedecodeerd en verantwoord; dat betekent niet dat iedere
Pump-variant wordt ondersteund. Vier nieuwe buys komen uitsluitend uit
422669516/647, 422669517/841, 422669518/406 en 422669518/982.
De mislukte 422669516/941 blijft ERROR / MISSING_EVENT, zonder Silver-toelating.
Zeven van de 22 Pump-verwijzende pakketten bevatten nu toegelaten feiten.
Er zijn zes verschillende mints, maar **geen** mint met beide toegelaten
kanten in deze selectie. Dat is onvoldoende dekking voor de concrete
same-mint buy/sell-vraag, geen bewijs van nul andere ketenactiviteit of edge.

De oorspronkelijke native `RESEARCH_SAMPLING`-identiteit blijft intact:
`[422669516,422669519)`, algoritme `SHA256_MIN_CENTER_1`, seed
`solana-quant-epoch978-pilot-v1-20260912`, selectieplan-SHA-256
`df930707d0ece9915744aec7cf771c60e92f35298f2a6b4251aeb30d5a6d85a1`.
Research Ready blijft false; root-to-slot membership blijft UNAVAILABLE.

### Artefacten en reproduceerbaarheid

Nieuwe outputroot buiten Git:
`/home/dmesdary/solana-quant-data/datasets/b5-buy-exact-quote-v2-20260914.3xBoLz`.
`identities.json` bindt de afzonderlijk behouden decoder, meetbinary,
projector en querycode. De decoderbinary heeft SHA-256
`37063054052b93bc8ea00b946a840d30d796482e63f4e8d7d54a0b88b5fbe8c4`;
de projector `599630349633166843bf0883f5d8e8ec8f41eb0a85528e64c2e389b6431018ac`.
De oorspronkelijke acquisitiebinary is niet vervangen.

| Artefact onder outputroot | SHA-256 |
|---|---|
| `decode-01/bronze.jsonl` | `52b2dd428efb1376fe1fa8ee9c6b32b8f14b8d9c4e912504666a509238487802` |
| `decode-01/silver.jsonl` | `2b4ab58eaaa40760e5399438352315b15a02e3082504e94591886f7d333eae0a` |
| `parquet-01/manifest.json` | `b7973d9048c476cae03dd1fdf1b0794f65cf557de01d1d1b0481dd49c18f9082` |
| `coverage-01/query-results.json` | `e1d9cfc280c21e5a65b9fe58fce057624218699bf44753a8190113c8da9fa5ad` |

`decode-01` en `decode-02` hebben identieke canonieke JSONL/quality-inhoud;
werkelijke procesklokken blijven afzonderlijk in `execution.json`.
Beide projecties gebruiken dezelfde oorspronkelijke decode-01-executiereceipt
voor identieke fysieke provenance. Alle Parquetbestanden en manifests zijn
byte-identiek; DuckDB-resultaten en het dekkingsrapport eveneens.
Dit is vastgelegd in `determinism.json`, niet beweerd over procesmetingen.
DuckDB voerde tien basisqueries en 28 dekkingsqueries daadwerkelijk uit op
de manifestlijst. De onafhankelijke review herhaalde alle queries en
controleerde ook de oorspronkelijke recordbytes uit Parquet.

Gemeten data-/auditpiek: 408.704 KiB per proces; nieuwe output circa 0,41 GB.
Bronze-JSONL is 47.563.682 bytes, Silver-JSONL 210.669 bytes, quality.json
48.025.951 bytes. Grootste Bronze-/Silver-record: 117.561 / 39.701 bytes.
De bestaande caps hoefden niet te veranderen. `logs/` bewaart procesmetingen.

### Lokale gates en review

- Volledige Bronze-gate: 122/122; Parquet: 30/30 Rust-tests.
- Python: 27 dekkings-, drie exact-quote- en vijftien manifesttests groen.
- Volledige Node-suite: 105 bestanden, 1.585 tests; kritieke suite: 101 tests.
- Policy, research-citations, TypeScript, normale build, relevante Rust
  fmt/clippy/build-, dependency- en offline gates groen.
- Authentieke regressies: 725 pakketten onveranderd; 3.137 pakketten met
  dezelfde vier engineering-sells. Alle 3.224 pilotpakketten, eerdere drie
  pilot-sells, 223 fouten en eerdere afwijzingen afzonderlijk vergeleken.
- Onafhankelijke bron-, code- en artefactreview: geen resterende bevindingen.
  Receipts en alle oorspronkelijke tussenfouten staan onder de outputroot;
  GitHub-verificatie is niet uitgevoerd. Geen gates afgezwakt.

De eerste releasebuild raakte een te kleine lokale buildbestandcap; alleen
die lokale buildcap is gecorrigeerd. Een extra socketverbod blokkeerde de
vereiste cockpit-loopbackcheck; de normale build met loopback is daarna
geslaagd. Beide oorspronkelijke fouten blijven bewaard en zijn geen
provider-/acquisitieretries. De 326 originele runbestanden, bewaarde binaries,
eerdere outputs, lockfiles en eerdere branchrefs zijn onveranderd.

### Bekijken

Open **http://localhost:7034/** vanuit de Windows-browser. Herstart zo nodig
uitsluitend deze read-only lokale bestandsserver vanuit WSL:

```bash
/home/dmesdary/solana-quant-data/datasets/b5-parquet-20260912.NO4c2L/duckdb-venv/bin/python -B -m http.server 7034 --bind 127.0.0.1 --directory /home/dmesdary/solana-quant-data/datasets/b5-buy-exact-quote-v2-20260914.3xBoLz/coverage-01
```

`browser/expanded/` bevat zeven werkelijk gemaakte Windows-Chrome-screenshots
en `browser-proof.json`: HTTP-HTML/JSON komt byte-identiek overeen met de
bestanden. `buy_cards.png` toont aangevraagde grenzen versus gerapporteerde
waarden; `question.png` toont ontbrekende dekking. Geen acquisitiebediening.

De kleinste vervolgstap is de resterende bron-/variantgaten en vereiste
aaneengesloten context voor **één** same-mint raw buy/sell-onderzoeksvraag
gericht prioriteren. Geen naam/ticker als universele toelatingseis; geen
stilzwijgende decimals, uitvoerbare prijs, coin-lifecycle of nieuwe selectie.
