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

## Daadwerkelijk uitgevoerd resultaat — 2026-09-14

Releasecode: `71ce9fa8dfac578126490315e16ca2a496f168ef` met de bestaande
Node 22.23.2 / Rust 1.97.1 / DuckDB 1.5.5-omgeving. De decoder en alle
dataset-/queryprocessen zijn uitgevoerd met sockets geweigerd en een
2-GiB-adresruimtecap (strenger dan werkelijk RSS). Er is niets geïnstalleerd.

| Slot | Bronze / verwacht | OK / ERROR | Pump-packages | Silver vóór → na |
|---|---:|---:|---:|---:|
| 422669516 | 1.022 / 1.022 | 973 / 49 | 6 | 2 → 2 |
| 422669517 | 1.048 / 1.048 | 972 / 76 | 7 | 0 → 0 |
| 422669518 | 1.154 / 1.154 | 1.056 / 98 | 9 | 0 → 1 |
| Totaal | 3.224 / 3.224 | 3.001 / 223 | 22 | 2 → 3 |

Alle packages zijn gedecodeerd; er is geen ontbrekende/unsupported/quarantined
transactiepackage. Dat is geen volledige Pump-dekking: tien sell-pogingen
omvatten drie toegelaten feiten, vier afgewezen mayhem-profielen en drie
mislukte transacties zonder eigen event. De niet-ondersteunde buy blijft
zichtbaar. De drie toegelaten sells zijn ook niet de volledige noemer van
22 Pump-refererende packages. Van de zeven succesvolle sell-pogingen worden
er drie toegelaten; de andere vier blijven verklaard afgewezen.

Het nieuwe feit heeft mint `7RZ6uLrxEgBgRouteBpYhZh6WWa1sVFYEgpW15Lbpump`,
raw tokenhoeveelheid `249959768138`, minimum-output `0` en event-gerapporteerde
SOL-accountinghoeveelheid `29277335`. Deze getallen zijn exacte u64-waarden,
geen genormaliseerde prijs of netto-opbrengst. Instructie outer2/inner5/height2
is gekoppeld aan het eigen event inner8/height3 binnen subtree `[5,9)`.

Twee volledige decode-uitvoeringen leveren byte-identieke quality JSON/HTML,
Bronze/Silver JSONL en COMPLETE op. Werkelijke uitvoeringsklokken blijven in
afzonderlijke execution-receipts. Twee projecties van dezelfde sealed
decode-01-executionbinding leveren exact dezelfde Parquetbestanden en manifests;
DuckDB-resultaten en coverage HTML/JSON zijn eveneens identiek. De writer
controleert typed-columnpariteit en verliesloze recordreconstructie.

| Artefact | SHA-256 |
|---|---|
| Nieuwe offline decoder | `8a41c5ee64097a23a752d54d75292efee34f3c2630132dd4f914e87d031fc04e` |
| Compiled decoder-source | `baf5bc43329b2632cf3056533f2e40c05a3e50c21294d3fc18a8fa4fc992ee83` |
| Datasetmanifest | `3c94cad33f7675bd5e4b3e241565d65a23a79e9154db4f6657fe5899000b1964` |
| Bronze JSONL | `3f336dcf3d3bdf7e68bbb9b5783856b6a49ec32efd6a80cba8f88a768955c827` |
| Silver JSONL | `68aebc838d8d8298ffa35c1757920e951603c08108774dc1b7c225662f5a71fc` |
| DuckDB coverage-resultaat | `17eff2c0d439defba6b8eb5a711bee8365bcfcdebb441b1bf20e34b91b332ee3` |
| Browserrapport | `0cfb4071ea9a495ea8a94603563407af40ccfcc33b511340f42eb856bbfc2799` |

Bronze Parquet is 58.395.446 bytes / 3.224 rijen; Silver is 156.919 bytes /
drie rijen. De pieken waren decoder 347.508 KiB, projector 45.468 KiB en
coveragequery 370.364 KiB. De volledige historische vergelijkingsaudit bleef
op 408.864 KiB. Grootste record blijft 117.561 bytes. Er is geen resourcecap
verhoogd. Beide eerdere authentieke regressies slagen: 725 packages / nul
sell-feiten en 3.137 packages / alle vier eerdere sells; de oude buy-afwijzing
blijft exact gelijk.

## Lokaal bekijken en reproduceren

Nieuwe WSL-ext4-output, buiten Git:
`/home/dmesdary/solana-quant-data/datasets/b5-sell16-20260914.P33K7M/`.

- `decode-01/` en `decode-02/`: complete nieuwe Bronze/Silver- en Rust-rapporten.
- `parquet-01/` en `parquet-02/`: manifestgebonden fysieke dataset.
- `coverage-01/` en `coverage-02/`: werkelijk uitgevoerde DuckDB-queries en
  zelfstandig browserrapport; `query-01/02` bewaart de basiscontrolequeries.
- `pilot-comparison-verified.json`: alleen vijf sell-diagnoses veranderen;
  alle overige transactievelden, bytes en buys zijn exact behouden. De eerste
  twee Silver-feiten verschillen alleen in eigen decoder-/Bronze-parenthash.
- `legacy-regression-result.json`, `baseline.json`, `determinism.json`:
  historische regressies, oorspronkelijke inputinventaris en dubbele output.
- `independent-source-audit.json`: lokale bron-/PDA-controles per kandidaat;
  `browser-final/expanded/`: echte screenshots en HTTP-hashvergelijking.

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain node \
  research/columnar-query/serve.mjs \
  /home/dmesdary/solana-quant-data/datasets/b5-sell16-20260914.P33K7M/coverage-01 7031
```

Open **http://localhost:7031/** in de Windows-browser. De viewer is read-only.
De geopende `sell_profile_details` en `sell_account_evidence_gaps`-tabellen
tonen ook afwijzingen en **verwachte**, niet verzonnen werkelijke accountrollen.
De volledige provenance en SQL blijven controleerbaar in `query-results.json`.

De behouden helper `pipeline.mjs` toont de uitgevoerde offline commando's,
binaryidentiteiten en eindige output-/geheugengrenzen. Voor een nieuwe uitvoering
is een nieuwe outputmap nodig; bestaande output wordt nooit overschreven.
Alle 326 acquisitierunbestanden, eerdere datasets/stoprapporten, lockfiles,
eerdere branches en de oorspronkelijke acquisitiebinary zijn hergehasht en
ongewijzigd. Die binary is niet hervat of vervangen.

Eén lokale controllerfout is apart behouden in `controller-observation.json`:
een gelijktijdige legacy-uitvoer werd ten onrechte aan het kleine
audit-outputbudget toegerekend. De audit zelf was geslaagd; dezelfde assertions
zijn daarna sequentieel opnieuw uitgevoerd met een nieuwe receipt. Geen
protocolassertie, data, cap of oude evidence werd aangepast. De eerdere
linker-threadallocatiefout onder een strenge adresruimtecap is opgelost door
de build op één CPU te begrenzen, niet door extra geheugen of installatie.
