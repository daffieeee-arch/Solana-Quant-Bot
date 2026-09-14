# B5 — afzonderlijke 16-account sell-profielen

> **Document status: ACTIVE.** Begrensde lokale offline vervolgstap vanaf
> `162225d085703fa9bd45492b4989157c12c8fb11`. B4/B5 en Project-evidence worden
> niet gepromoveerd. Geen acquisitie, bron-download of GitHub-verzoek.

## Onderzochte gevallen en bronbesluit

De [vorige volledige pilot](B5_PILOT_RESOURCE_CORRECTION.md) blijft behouden:
3.224 gedecodeerde transactiepackages, 223 on-chain ERROR en twee toegelaten
sell-pakketten. Deze stap onderzoekt precies de vijf succesvolle
`ACCOUNT_MISMATCH`-gevallen, eerst slot 422669518/transactie 1062. De drie
`MISSING_EVENT`-gevallen zijn mislukte transacties; buy-diagnoses vallen buiten
deze wijziging. Accountaantal alleen is geen toelatingscriterium.

De [nieuwe bronreceipt](../../rust/of1-bronze-decoder/sources/pump-sell16-evidence.json)
bindt uitsluitend eerder bewaarde officiële documentatie en SDK-bronnen.
De Pump-documentatiepin blijft `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`,
`idl/pump.json` met SHA-256
`b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.
`BREAKING_FEE_RECIPIENT.md` beschrijft 16/17 accounts; de bewaarde officiële
SDK 1.36.0, `src/sdk.ts`, bouwt veertien IDL-accounts gevolgd door
`bonding_curve_v2(mint)` en de buybackrecipient wanneer de accumulator ontbreekt.
De SDK-tarballhash is
`e3e612d9bf0be5f1a443d321aee51b044b5e98408170c3eb8baebf6346f569aa`.
Het SDK-Git-head is registry-declared, niet onafhankelijk Git-geverifieerd.
De volledige niet-gelicentieerde documentatie-IDL wordt niet opnieuw vendored.

| Geval | Onderzoeksprofiel | Brononderbouwde conclusie |
|---|---|---|
| 422669518 / 1062 | Tokenkeg, event `mayhem_mode=false`, geen accumulator | Eén begrensde nested-height2-route; alle zestien adres-/message-capacitychecks vereist |
| 422669517 / 4, 843, 1022; 422669518 / 441 | Token-2022, event `mayhem_mode=true` | Afzonderlijk herkend maar niet toegelaten: afwijkend account14 én ontbrekend message-signerschap / onbekende CPI-flags |

De nieuwe toegelaten kandidaat heet
`pump-sell-9c82f61-tokenkeg-noaccumulator16-nested-height2-v1`.
Het is geen bewijs dat `is_cashback_coin=false`: de cashbackdocumentatie staat
fallback zonder accumulator toe. De daadwerkelijke coin-accountinhoud blijft
onbekend. Er wordt alleen de waargenomen instructie-/eventvorm ondersteund.

## Volledige controles, geen permissieve parser

- De sell is exact 24 bytes: discriminator en twee raw u64-velden; geen
  prefixacceptatie of trailing-byteverwijdering. Het eigen event-CPI is exact
  367 bytes met volledige Borsh-consumptie en dezelfde gepinde TradeEvent-vorm.
- De eerste veertien IDL-rollen blijven ongewijzigd. Positie 14 moet de
  readonly `bonding_curve_v2`-PDA zijn; positie 15 een writable recipient uit
  de gepinde buybacklijst. Alle zestien adressen moeten onderscheidbaar zijn.
- PDA/ATA-afleiding gebruikt de werkelijk opgenomen mint, user en tokenprogram.
  Creator-vault gebruikt de event-gerapporteerde creator; dit bewijst geen
  gelezen bonding-curve-accountinhoud of token-accountbezit.
- Fee-recipient correspondeert met het event en de mode-specifieke officiële
  lijst; buybackrecipient heeft een afzonderlijke lijst. Feevelden worden niet
  bij elkaar opgeteld tot verzonnen netto-opbrengsten.
- De eigen nested instructie en event volgen de werkelijk opgenomen volgorde
  en stack heights. De volledige groep, subtreegrens en eventautoriteit worden
  gecontroleerd; het event van een naburige aanroep wordt niet geleend.
- Mint, user en raw tokenhoeveelheid corresponderen; de transactie moet
  opgenomen OK-status hebben. De nieuwe kandidaat vereist non-mayhem,
  geen shareholders/track-volume en daadwerkelijk gerapporteerde nulwaarden
  voor beide cashbackvelden. Deze begrenzing leest geen defaults in.
- Kandidatenselectie volgt de observatiepredicaten. Er is geen slot/index-
  allowlist, caller-supplied count of universele historische variantclaim.

### Waarom de vier mayhem-gevallen afgewezen blijven

Hun user is exact de officiële Mayhem `sol-vault`-PDA, bump 253, en de
reserved fee-recipient plus buybackrecipients passen de bronlijsten. De zeven
overige gedocumenteerde PDA/ATA-adressen passen eveneens. Maar hun positie 14
past **niet** bij `bonding_curve_v2(mint)` uit de gepinde bron; ook de bekende
`mayhem-state`-PDA verklaart het adres niet. De werkelijke rol blijft onbekend.

De user is bovendien geen message-signer. PDA-afleiding toont een mogelijke
programmasignercapaciteit, niet de feitelijke CPI AccountMeta-flags of gebruikte
`invoke_signed`-seeds. Het oude message-minimumcontract wordt niet omzeild.
Ook in toegelaten nested feiten blijven `cpi_signer`, `cpi_writable` null en
`cpi_privileges_verified=false`. Transactiesucces vult deze informatie niet in.

## Bewijsgrenzen en vervolgdekking

Alle Bronze-records, mislukkingen, buy-afwijzingen en oude sell-feiten blijven
behouden; uitsluitend de vijf geselecteerde sell-diagnoses kunnen veranderen.
Nieuwe Silver-feiten zijn atomaire instructie-/eventwaarnemingen met hun
volledige Bronze-, Raw-, receipt-, source- en decoderhashbinding.
Geen accountwrites, launchdatum, naam/ticker, economische mintidentiteit,
decimals, uitvoerbare prijs of historische activatierange worden afgeleid.
`RESEARCH_SAMPLING` blijft de oorspronkelijke sampleklasse;
**Research Ready blijft false** en root-to-slot membership `UNAVAILABLE`.

Voor een volgende onderzoekssample is eerst een expliciete vereiste
waarnemingsdekking nodig: ondersteunde volledige buy/sell-context voor een
flowvraag, juiste mint-/eenheidsidentiteit en de vooraf gekozen tijdscontext.
Lifecyclevragen vereisen daarnaast create/migratie en voldoende aaneengesloten
historie. Een naam of ticker is niet noodzakelijk voor elke vraag. De vier
onopgeloste mayhem-aanroepen blijven in de noemer; een groter sample verhelpt
die decoder-/bronlacune niet vanzelf. Eerst ontbreekt bronbewijs voor hun
account14-rol en een afzonderlijk passend CPI-privilegebewijscontract. Er is
geen nieuwe selectie, acquisitie of verruiming van de buy-parser geautoriseerd.

De bestaande resourcegrenzen blijven gelden: 2 GiB per proces, 4 GiB nieuwe
artefacten samen, 5.000 records / 64 MiB per fysiek Parquetbestand. Rust schrijft
de canonieke feiten; DuckDB telt uitsluitend de manifestgebonden Parquet en
toont ook de afgewezen profielen. Python implementeert geen protocoldecode.
