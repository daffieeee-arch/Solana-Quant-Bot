# B5 — nested 25-byte buy: exacte diagnose, geen Silver-toelating

> **Document status: ACTIVE.** Begrensde lokale offline vervolgstap vanaf
> `eab13b0f03290a63c461589676a48f7c7a6acde9`. Geen downloads, GitHub,
> acquisitiehervatting, installatie of delivery-/Evidence-promotie.

## Wat werkelijk past

De oorspronkelijke selectie `[422669516,422669519)` blijft ongewijzigd.
De drie onderzochte succesvolle transacties bevatten ieder de volledige
legacy-buy-discriminator `66063d1201daebea`, twee u64-argumenten en de
daadwerkelijk opgenomen booleanbyte `00` op offset 24. Geen byte wordt
verwijderd, aangevuld of uit het event afgeleid.

| Slot / transactie | Raw tokenargument | Max SOL-accountingargument | Event-mint |
|---|---:|---:|---|
| 422669516 / 974 | 537155460591 | 26305933 | `FLDe1hSQSDjt2m8WNthHT9RGq5sTw1qhirVBGAKQpump` |
| 422669517 / 1023 | 601504660440 | 32187161 | `FLDe1hSQSDjt2m8WNthHT9RGq5sTw1qhirVBGAKQpump` |
| 422669518 / 830 | 5131115368584 | 112822100 | `BYidShFB6x3VQzGznfDntGVo2omvxUXopSaa5snYpump` |

Dit zijn **diagnostische opgenomen waarden**, geen toegelaten Silver-buys.
De eigen Pump-instructie staat op outer 2 / inner 0 / height 2; het eigen
366-byte event op inner 4 / height 3. De volledige opgenomen subtree is
`[0,5)`; de aanroep op inner 5 is een sibling en wordt niet geleend.
De eventlayout wordt volledig geconsumeerd. Mint, user, tokenhoeveelheid en
`track_volume=false` corresponderen; alle drie events melden Mayhem-mode.
Event-reserves zijn geen gelezen accountstaat en transactiesucces bewijst
geen door ons gereconstrueerde gecommitteerde toestandsverandering.

## Bronidentiteit en concrete blokkade

De afzonderlijke [bronreceipt](../../rust/of1-bronze-decoder/sources/pump-nested-buy-evidence.json)
bindt de lokaal behouden officiële Pump-documentatie:
commit `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`, `idl/pump.json`,
SHA-256 `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.
De lokaal behouden officiële SDK 1.36.0 heeft tarball-SHA
`e3e612d9bf0be5f1a443d321aee51b044b5e98408170c3eb8baebf6346f569aa`.
SDK- en documentatie-IDL komen voor deze buy en TradeEvent overeen.
De SDK Git-head blijft registry-declared, niet onafhankelijk Git-geverifieerd.
Geen volledige ongelicenseerde IDL wordt in Git gevendord.
Deze bronnen zijn geen bewijs van historische on-chain activatie.

Alle 18 buy-specifieke accounts, fee-/buybacklijsten en tien PDA/ATA-
afleidingen zijn gecontroleerd. Negen afleidingen corresponderen. De
gedocumenteerde remaining **buy-account 16** correspondeert niet:

| Gevallen | Opgenomen account 16 | Bronverwachte bonding-curve-v2-PDA |
|---|---|---|
| 974 / 1023 | `27EC5c3ECSvBWUGNCSuwqeGFZndxYPz5forRAX7BCeSZ` | `CLZGdjB5x233gRXR86m8pSXgru97XJNcUwMpixJhRvw4` |
| 830 | `DmtwSgyWeWtNZ2GtsiT7uDBXzburdHN6beRaaFmpUfrb` | `8My4EuuVBPnhzU1GGXY7RDbHkcPdgYreFYaMrHceNviV` |

De werkelijke rol van dit afwijkende account blijft **UNAVAILABLE**.
Een sell-accountpositie of een vermoedelijke Mayhem-rol vult deze bronlacune
niet in. De user correspondeert met de SDK-sol-vault-PDA, maar is geen
message-signer. Werkelijke CPI signer/writable-flags zijn niet opgenomen.
Een PDA-afleiding of succesvolle transactie bewijst geen `invoke_signed`.
Dit verklaart niet dat de transactie ongeldig was; het begrenst ons bewijs.
Er wordt geen algemene nieuwe toelatingseis voor eerdere nested-sells ingevoerd.

## Begrensde implementatie en behoud

Rust produceert `transaction.pump_nested_buy_analysis` voor de geselecteerde
instructievorm, zonder slot-/indexallowlist. De aparte profielidentiteit is
`pump-buy-9c82f61-token2022-mayhem18-nested-height2-unresolved-v1`.
De diagnose bevat volledige instructiebytes, bronhash, alle accountcontroles,
eigen event/context, status, positieve layoutbevindingen en afzonderlijke
bewijsleemtes. Geen kandidaat wordt als compatibel geregistreerd:
`NOT_ADMITTED / ACCOUNT_MISMATCH`, `silver=NOT_PRODUCED`.

De bestaande [24-byte buy 320](B5_PILOT_BUY24_SOURCE_COVERAGE.md), de
26-byte engineering-buy en vier Mayhem-sellafwijzingen blijven afzonderlijk
en ongewijzigd. Er is geen buy-parserverruiming of nieuw Silver-writerschema.
Oude Raw, receipts, rapporten, stop-evidence en acquisitiebinary blijven intact.

DuckDB leest alleen de manifestgebonden Parquetbestanden en telt bestaande
Rust-feiten. De nieuwe tabellen `nested_buy_details` en
`nested_buy_account_gaps` tonen diagnoses naast de bestaande toegelaten
buy/sell- en mintinventaris. Python decodeert geen instructie of event.
Een diagnostische mint is geen mint met toegelaten buy én sell.
Ontbrekende buydekking betekent niet nul on-chain buyactiviteit.

## Kleinste volgende werkpakket

Eerst één Pump-autoritatieve verklaring voor het opgenomen remaining
buy-account 16 bij deze exacte Mayhem/Token-2022-vorm, met bronversie en
toepasselijkheid; houd vereiste accountrollen en onbekende CPI-flags apart.
De 24-/26-byte buyvarianten vereisen hun eigen bronclaims en blokkeren dit
afzonderlijke onderzoek niet. Geen grotere download om de bronlacune te
omzeilen en geen herselectie van de pilot.

Voor de beperkte vraag “welke mints hebben hier beide ondersteunde kanten
en welke raw hoeveelheden staan in ketenvolgorde?” zijn mint, hoeveelheden,
eigen context en volgorde nodig; naam/ticker zijn optioneel. Economische
normalisatie, executable prices, lifecycle, representativiteit en edge
blijven andere, onbewezen eisen. **Research Ready blijft false.**

Resourcegrenzen blijven 2 GiB per proces / 4 GiB nieuwe artefacten en maximaal
5.000 records / 64 MiB per fysiek Parquetbestand. Er wordt alleen vanwege de
nieuwe brongebonden Rust-diagnose opnieuw verwerkt, niet in de hoop door een
ongewijzigde herhaling alsnog buys toe te laten.

## Werkelijk uitgevoerd — 2026-09-14

Decoder-releasecode: `06d9d038636aac08dbcea30c30ac6d31d414c449`.
De latere browser-wordwrap is uitsluitend presentatie; de nieuwe reader is
afzonderlijk bewaard en vervangt de acquisitiebinary niet.

| Slot | Bronze / verwacht | OK / ERROR | Nieuwe nested-buydiagnose | Toegelaten buy / sell |
|---|---:|---:|---:|---:|
| 422669516 | 1.022 / 1.022 | 973 / 49 | 974: ACCOUNT_MISMATCH | 0 / 2 |
| 422669517 | 1.048 / 1.048 | 972 / 76 | 1023: ACCOUNT_MISMATCH | 0 / 0 |
| 422669518 | 1.154 / 1.154 | 1.056 / 98 | 830: ACCOUNT_MISMATCH | 0 / 1 |
| Totaal | 3.224 / 3.224 | 3.001 / 223 | 3 diagnoses, geen Silver-buy | 0 / 3 |

Alle transactiepackages zijn DECODED; package-missing/unsupported/quarantine
zijn nul. Dit zegt niets over volledige Pump-dekking: 22 packages verwijzen
naar Pump, terwijl maar drie volledige sell-profielen toegelaten zijn.
Er zijn twee mints met toegelaten sells, geen mint met beide toegelaten kanten.
De drie afgewezen buy-diagnoses noemen twee **andere** event-mints. Dit zijn
geen conclusies over ontbrekende on-chain activiteit of voldoende sampling.

De strikte vergelijkingsaudit accepteert alleen de nieuwe
`pump_nested_buy_analysis` bij de drie kandidaten. Alle oorspronkelijke
transactievelden, eerdere diagnoses, bytes, onbekenden en drie sell-feiten
zijn exact behouden. Alleen de nieuwe decoderidentiteit en opnieuw
gecontroleerde Silver-parenthashes veranderen daarnaast. De 725- en
3.137-package-regressies zijn werkelijk opnieuw uitgevoerd; alle vier
engineering-sells en de 26-byte buy-afwijzing blijven exact behouden.

Twee volledige decodes leveren identieke canonieke JSONL/quality/COMPLETE.
Twee projecties van dezelfde sealed executionbinding leveren byte-identieke
Parquetbestanden en manifests. Beide queryresultaten zijn identiek. De laatste
wordwrapverbetering is tweemaal apart gerenderd; query-JSON bleef identiek,
zonder opnieuw te decoderen. Werkelijke procesmetingen blijven afzonderlijk.

| Artefact | SHA-256 |
|---|---|
| Nieuwe offline decoder | `38e7a73b8eeb70a36cf3df40afba35ad660b583c38335cafcfc379d79f49ffcf` |
| Compiled decoder-source | `d5b697f89db61abb51d03bdaa5bd552effd386826591799a2652786c3ec5677f` |
| Datasetmanifest | `825c1f4681db8e7eeacdfdc11a2a5cb3694ead6729db10509c2aebe833522628` |
| Bronze JSONL | `f40dd4642ee5ea52a95145b578a63080f022dbf306be5cb4dc3351c44c6fe4f5` |
| Silver JSONL | `ebc4c7385209774959570696b0f8f41b864f85bac6c4201c3560468507a8c800` |
| DuckDB coverage-JSON | `26ed4bb219089cd3f433b75e06e55e4be5d0f667d6449dcc86db868feb57cb64` |
| Laatste browserrapport | `e648e0feaa5420f4b3fe5815ddec85758ec585cb29d374e179942300b3fc896c` |

Bronze Parquet: 3.224 rijen / 58.472.584 bytes; Silver Parquet: drie rijen /
156.919 bytes. Geen fysieke bestandsgrens is verhoogd. Data-/queryprocessen
hadden sockets geweigerd en een 2 GiB adresruimtecap, strenger dan RSS:
decoderpiek 348.532 KiB, querypiek onder 372.000 KiB, historische volledige
vergelijkingsaudit 408.696 KiB. Nieuwe data/evidence is circa 0,41 GB, ruim
onder de 4 GiB-cap; de eindreceipt legt de werkelijke bytes vast.

Uitgevoerde gates: 110 volledige Bronze Rust-tests, waaronder acht nieuwe
nested-buytests; 24 Parquet Rust-tests; 27 coverage- en 15 manifesttests;
1.585 Node-tests en 101 kritieke tests; fmt/clippy/build/typecheck,
repositorypolicy, citation- en offline dependency/netwerkgates.
Nieuwe negatieve tests behandelen argumentvorm/boolean, account/PDA/fee,
eigen event, heights/siblings/dubbele events, failed status en onbekende
privileges. Authentieke CAR-secties zijn apart gelabeld van synthetische
mutaties. Een verse reviewer controleerde bronbytes, code en werkelijke
artefacten onafhankelijk; GitHub-verificatie is **niet uitgevoerd**.

Reviewcorrecties en oorspronkelijke ontwikkelfouten blijven buiten Git
behouden: null/false-labelprecisie, ontbrekende controles in de query,
JSON-null als fictieve diagnose en een tijdelijke HTML-importsyntaxfout.
Logbewijs en eventuele post-fix broncaptures worden uitdrukkelijk
onderscheiden; een latere broncapture bewijst geen oude foutieve bronbytes.

## Lokaal bekijken en reproduceren

Nieuwe outputroot:
`/home/dmesdary/solana-quant-data/datasets/b5-nested-buy25-20260914.kfFPxr/`.

- `decode-01/02`, `parquet-01/02`: nieuwe volledige records en fysieke dataset.
- `query-01/02`, `coverage-01/02`: eerste werkelijk uitgevoerde queryrapporten.
- `coverage-03/04`: definitieve, inhoudelijk gelijke rapporten met woordomloop.
- `pilot-comparison-verified.json`, `legacy-regression-result.json`,
  `determinism.json`, `report-final-determinism.json`: tellingen en exact behoud.
- `independent-source-audit.json`, `independent-code-review.json` en de
  afzonderlijke artifactreview: controleerbare onafhankelijke bevindingen.
- `browser-final-v2/expanded/`: echte Windows-Chrome-screenshots en HTTP/hashbewijs.
- `baseline.json`, `identities.json`, `logs/`, `gates-*`: oorspronkelijke
  bestanden/refs/locks, nieuwe readeridentiteit en echte proces-/testmetingen.

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-nested-buy25-20260914.kfFPxr/coverage-03 7033
```

Open **http://localhost:7033/** in de Windows-browser. Het is dezelfde kleine
read-only rapportviewer, geen nieuw dashboard. `pipeline.mjs`, `legacy.mjs`,
`pilot-audit.py`, `legacy-audit.py` en `report-final.mjs` bewaren de gebruikte
offline commando's en vergelijkingen. Een herhaling gebruikt nieuwe outputnamen;
bestaande evidence wordt niet overschreven. Geen nieuwe lease of acquisitie.
