# B5 — brongebonden buy-dekking in de vaste pilot

> **Document status: ACTIVE.** Lokale offline vervolgstap vanaf
> `8a8d3a8f494702c9986a311431661f58b8e51d4c`. Geen nieuwe acquisitie,
> bron-download, GitHub-verzoek, delivery- of Evidence-promotie.

## Geobserveerde instructie, niet één universele buy-layout

De oorspronkelijke selectie `[422669516,422669519)` en haar
`RESEARCH_SAMPLING`-identiteit blijven onveranderd. De
[voorgaande sell-resultaten](B5_PILOT_SELL16_PROFILES.md), oorspronkelijke
Raw, receipts, binaries en stoprapporten blijven afzonderlijk behouden.

Slot **422669518 / transactie 320** is succesvol opgenomen en bevat een
top-level Pump-instructie op positie 3. De volledige instructie is 24 bytes:
`66063d1201daebea0c9d5cec99060000a0b64a3100000000`, SHA-256
`dbeef7031bfa9899d24422269a6acaf752ea0cd5659095e7da0cbb43baea6237`.
De legacy-buy-discriminator is `66063d1201daebea`; de twee historische
u64-argumentposities leveren exact `7258165255436` en `826980000`.
Er wordt geen byte verwijderd, aangevuld of als impliciete boolean gelezen.

De instructie heeft 18 accounts en verwijst naar Token-2022. Het eigen
event-CPI staat op outer 3 / inner-order 6 / stack-height 2, is 366 bytes en
heeft SHA-256
`d4d648e0a8c9e4ac7a7d20807f75285279e5aeefefa0dfc121b83b839fbe30c0`.
De gepinde TradeEvent-layout wordt volledig geconsumeerd. Dit event meldt
buy/non-mayhem, dezelfde mint/user en tokenhoeveelheid, een
SOL-accountinghoeveelheid van `787600000` en timestamp-i64 `1779951226`.
Dit zijn diagnostische eventvelden, geen toegelaten Silver-buy of bewezen
account-statechange. Event-reserves zijn geen gelezen accountstaat.

## Afzonderlijke bronversies en de ontbrekende claim

De [nieuwe bronreceipt](../../rust/of1-bronze-decoder/sources/pump-buy24-evidence.json)
bindt uitsluitend lokaal bewaarde bronnen en maakt hun bewijsstatus expliciet.

| Bron | Gedocumenteerde vorm | Toepasselijkheid op de hele observatie |
|---|---|---|
| Officiële documentatie `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`, `idl/pump.json`, SHA `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49` | Legacy buy: 25 bytes, twee u64's en `OptionBool`; 16 IDL-accounts plus twee gedocumenteerde remaining accounts | De vereiste instructieboolean ontbreekt; geen volledige match |
| Behouden historische extractie `d1b721d7bf8af75cf59f87fd11271109cfb0bdd3`, `idl/pump.json`, geregistreerde SHA `d8660001ec21dbeb1f729a08c05ed5ff3bb4880192e8cb9baf8caf42c57a0ff3` | Legacy buy: 24 bytes, twee u64's; 15 accounts | Alleen argumentvorm correspondeert; volledige historische IDL-bytes zijn lokaal niet behouden en worden niet opnieuw als geverifieerde raw bron gepresenteerd |
| Officiële `buy_v2` uit dezelfde huidige documentatiepin | Ook 24 bytes, maar discriminator `b817ee6167c5d33d` en 27 accounts | Andere instructie; lengte alleen selecteert geen variant |
| Behouden officiële SDK 1.36.0 | Legacy buy bouwt 25 bytes; accepteert een tokenprogramparameter en moderne remaining accounts | Geen bewijs voor weggelaten `track_volume` of de geobserveerde versiemix |

De SDK-tarball SHA is
`e3e612d9bf0be5f1a443d321aee51b044b5e98408170c3eb8baebf6346f569aa`.
De SDK Git-head blijft registry-declared, niet onafhankelijk Git-geverifieerd.
De volledige documentatie-IDL zonder LICENSE wordt niet in Git gevendord.
Bron-documentatie is geen historische activatie- of deployed-programbewijs.

De moderne accountverwachtingen, fee-/buybacklijsten en alle tien PDA/ATA-
afleidingen corresponderen met de opname. Dat is expliciet **moderne
broncorrespondentie**, geen bewijs voor hun historische rol in een
24-byte-profiel. Tokenprogramassociatie bewijst geen gelezen mint-owner.
Message-privileges blijven afzonderlijk van ontbrekende werkelijke CPI-flags.

**Ontbreekt:** een Pump-autoritatieve deserializer of expliciete
compatibiliteitsregel die de complete 24-byte legacy-buy bindt aan de
geobserveerde moderne 18 accounts, Token-2022 en eigen TradeEvent, inclusief
de betekenis van ontbrekende `track_volume`. Het event bevat `false`, maar
het instructieargument blijft **null / NOT_ENCODED_IN_24_BYTES**. Generieke
Anchor-compatibiliteit, transactiesucces of cashback-accountdocumentatie vult
dit gat niet in.

Daarom levert Rust een afzonderlijke
`pump-buy-legacy24-modern18-source-gap-v1`-diagnose met
`NOT_ADMITTED / SOURCE_VERSION_BRIDGE_UNAVAILABLE`, **geen Silver-buy**.
De oorspronkelijke B3-layoutprobe blijft bytegetrouw behouden en wordt
zichtbaar als schema-specifieke afwijzing, niet als universeel corrupte buy.
De 26-byte engineering-buy en vier Mayhem-afwijzingen veranderen niet.

## Concreet meetbare buy/sell-vraag

> Welke mints hebben in deze vaste selectie zowel een ondersteunde buy als
> sell, en welke exacte raw tokenhoeveelheden zijn in ketenvolgorde waargenomen?

DuckDB leest uitsluitend manifestgebonden Parquet. Het rapport telt
toegelaten kanten uit Rust-Silver, afzonderlijk van programmaverwijzingen,
layoutprobes en eventdiagnoses. Alle pakketten en onbekenden blijven in de
noemer. Mints zonder beide toegelaten kanten verdwijnen niet uit de inventaris.

Nodig zijn mintidentiteit, exacte raw tokenhoeveelheden, volledige ondersteunde
instructie/account/eigen-eventcontext en slot-/transactie-/invocationvolgorde.
Naam/ticker/logo/website zijn hiervoor optioneel. Decimals zijn niet nodig om
raw integers binnen één mint te tonen; economische normalisatie, prijs en
cross-mintvergelijking vereisen aanvullend eenheidsbewijs. Het eventtimestamp
is geen onafhankelijke block-time, observation-, actionable- of executionclock.
Lancering, lifecycle en uitvoerbaarheid worden niet afgeleid.

De machineleesbare vraag onderscheidt een integriteits-/verwerkingsfout,
onvoldoende geschikte feiten, en een eventueel uitvoerbare beperkte
beschrijvende telling. Geen van deze uitkomsten is een strategietest of
edge-falsificatie. **Research Ready blijft false.**

## Reproductiegrenzen

Nieuwe decoder, Bronze/Silver, Parquet en queryrapporten krijgen eigen
identiteiten in nieuwe WSL-ext4-outputdirectories. De oorspronkelijke
acquisitiebinary en alle 326 runbestanden blijven ongewijzigd. Maximaal 2 GiB
per proces en 4 GiB nieuwe artefacten; maximaal 5.000 records / 64 MiB per
fysiek Parquetbestand. Geen dependency- of toolchainwijziging.

De authentieke regressionfixture is een exact behouden, CID-/hashgebonden
CAR Transaction-section, niet een synthetisch nagemaakt transactiepakket.
Negatieve argument/account/event/status/grensgevallen zijn afzonderlijk
synthetisch gelabeld. De 725- en 3.137-pakketregressies, vier eerdere
engineering-sells en drie pilot-sells blijven vereist. Twee volledige
uitvoeringen vergelijken canonieke recordinhoud, fysieke Parquet/manifest-
hashes en daadwerkelijk uitgevoerde queryresultaten; procesklokken staan apart.

## Daadwerkelijke herverwerking — 2026-09-14

Releasecode `a40893ae29a1a37a721b3bda1bfe1c5c48e20300`, bestaande Node
22.23.2 / Rust 1.97.1 / DuckDB 1.5.5. De decoder/projector/queryprocessen
draaiden met sockets geweigerd en maximaal 2 GiB adresruimte, een strengere
grens dan werkelijk procesgeheugen. De reader verifieerde de bestaande
Raw/receipt/plan-/samplebinding; geen oude writer werd hervat.

| Slot | Bronze / verwacht | OK / ERROR | Pump-verwijzende pakketten | Toegelaten buy / sell |
|---|---:|---:|---:|---:|
| 422669516 | 1.022 / 1.022 | 973 / 49 | 6 | 0 / 2 |
| 422669517 | 1.048 / 1.048 | 972 / 76 | 7 | 0 / 0 |
| 422669518 | 1.154 / 1.154 | 1.056 / 98 | 9 | 0 / 1 |
| Totaal | 3.224 / 3.224 | 3.001 / 223 | 22 | 0 / 3 |

Alle 3.224 transactiepackages zijn DECODED, zonder ontbrekende, unsupported
of quarantined package-uitkomst. Dat is **geen** volledige Pump-dekking.
Tien sell-diagnoses omvatten de drie bestaande feiten, vier succesvolle
maar afgewezen Mayhem-gevallen en drie mislukte missing-eventgevallen.
De nieuwe buy-diagnose geeft geen Silver-toelating. De volledige noemer van
Pump-programmaverwijzingen is 22 pakketten, niet vier of drie feiten.

De strikte vergelijkingsaudit staat slechts één inhoudelijke toevoeging toe:
`transaction.pump_buy_variant_analysis` bij 422669518/320. Alle oorspronkelijke
transactievelden, bytes, eerdere buy- en sell-diagnoses en onbekenden zijn
exact behouden. Root-decoderidentiteit en daarvan afhankelijke, opnieuw
geverifieerde Silver-parenthashes veranderen wel. De drie sell-feiten blijven
inhoudelijk exact gelijk. Er zijn nul toegelaten buys en geen mint met beide
toegelaten kanten; dit bewijst **niet** dat on-chain geen buys voorkwamen.

Twee volledige decodes leveren dezelfde canonieke JSONL/quality/COMPLETE.
Twee projecties van dezelfde sealed decode-01-executionbinding leveren
byte-identieke Parquetbestanden en manifests; beide DuckDB-resultaten en het
coverage-HTML zijn eveneens identiek. Werkelijke procesklokken blijven apart.
De manifestreader gebruikt alleen de benoemde shards, verifieert hashes en
typed-column-/recordpariteit; JSON-blobs vervangen de typed kolommen niet.

| Artefact | SHA-256 |
|---|---|
| Afzonderlijke offline decoder | `97b6f769251edc4f51c3288dfd1249310502a16eb1a2ca0161b76df53e711d5f` |
| Compiled decoder-source | `9ff1d8b1670b4db1389149a73084e6c14b2598b14fcbb6425a0bb3eeb5930461` |
| Datasetmanifest | `788864af24faa778aa1d7597702a8d151a1a96282efd3cb0b45fa4d26694949f` |
| Bronze JSONL | `7ea79110b508381e31966b1cecb2bc2b401175fd0b0793f37364592a88d6c92e` |
| Silver JSONL | `a3b565ea94d6c21fe121939250dd48aadfa5bea3729246cad84ec1e8da1c356f` |
| DuckDB coverage-resultaat | `9368d904c8883050fb9543261e42d398506fb4e1227c2c869b525ffe8916549c` |
| Browserrapport | `0b38f4375293f94ba7a88f0fa7c149b84a41d7d15d59e83d16e99613e397845f` |

Bronze Parquet bevat 3.224 rijen / 58.412.456 bytes, Silver drie rijen /
156.919 bytes. De decoderpiek was 347.732 KiB, projector 43.308 KiB en
coveragequery 371.252 KiB. De volledige historische vergelijkingsaudit bleef
op 408.776 KiB. Geen resourcegrens is verhoogd. Ook de behouden authentieke
regressies zijn uitgevoerd: 725 packages / nul sells en 3.137 packages /
alle vier eerdere engineering-sells, inclusief de ongewijzigde 26-byte
buy-afwijzing. Geen herclassificatie van engineeringdata naar researchdata.

## Lokaal zichtbaar en controleerbaar

Nieuwe outputroot, buiten Git:
`/home/dmesdary/solana-quant-data/datasets/b5-buy320-20260914.7Ur1TG/`.

- `decode-01/02`: nieuwe volledige Rust-Bronze/Silver en diagnoses.
- `parquet-01/02`: manifestgebonden fysieke dataset.
- `query-01/02` en `coverage-01/02`: daadwerkelijk uitgevoerde SQL,
  resultaten, receipts en browserrapporten.
- `pilot-comparison-verified.json`, `legacy-regression-result.json`,
  `determinism.json`: behoud/pariteit/regressies, met hashes en eigen scripts.
- `independent-source-audit.json`, `source-audit-tarball-parity.json`:
  afzonderlijke lokale officiële-broncontroles.
- `browser-final/expanded/`: vijf werkelijke Windows-Chrome-screenshots;
  `browser-proof.json` vergelijkt de via HTTP getoonde bytes met de bestanden.
- `baseline.json`, `identities.json`, `logs/`: oorspronkelijke326-bestanden,
  eerdere evidence/branches/locks, nieuwe binaryidentiteiten en procesmetingen.

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-buy320-20260914.7Ur1TG/coverage-01 7032
```

Open **http://localhost:7032/** in de Windows-browser. De bestaande read-only
viewer toont de geopende bronvergelijking, toegelaten buy/sell-inventaris,
exacte hoeveelheden/volgorde en concrete ontbrekende onderzoekseisen.
Oude rapporten blijven op hun eigen locaties behouden. `pipeline.mjs` legt
alle werkelijke offline commando's en eindige resourcegrenzen vast; opnieuw
uitvoeren vereist nieuwe outputnamen, geen overschrijven van evidence.

## Kleinste volgende werkpakket

Geen grotere steekproef om de bronlacune te omzeilen. Eerst één gerichte
autoritatieve compatibiliteitsbron voor de complete 24-byte buy / moderne
accounts / eigen event, met expliciete ontbrekende-argumentsemantiek; daarna
een begrensde profielwijziging plus negatieve tests en herhaling van dezelfde
onveranderde pilot. De bewaarde historische extractie alleen is onvoldoende.
Als die regel niet aantoonbaar is, blijft de diagnose afgewezen en moet de
vereiste buy-dekking vóór een vervolgsample expliciet worden begrensd.
Mayhem-account14 en daadwerkelijke CPI-privileges blijven afzonderlijke
open bronvragen. Geen acquisition-GO, statuspromotie of edgeclaim volgt hieruit.
