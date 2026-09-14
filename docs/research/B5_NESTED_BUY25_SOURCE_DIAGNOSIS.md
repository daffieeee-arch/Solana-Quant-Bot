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
