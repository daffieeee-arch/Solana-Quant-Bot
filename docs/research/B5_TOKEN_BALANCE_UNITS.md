# B5 — opgenomen tokenbalansen en afzonderlijke eenhedencontext

> **Document status: ACTIVE.** Lokale offline vervolgstap vanaf
> `bb662c54402ea157e5c047d8f0542f535a04e134`. Geen nieuwe variant,
> acquisitie, dependency-installatie, GitHub-verzoek of statuspromotie.

## Bron en betekenis

De behouden officiële Agave-bronpin is
`6c1ba34691f17ac902ae3d2e1147eed5723b9cef`:

- [storage-proto/proto/confirmed_block.proto](https://github.com/anza-xyz/agave/blob/6c1ba34691f17ac902ae3d2e1147eed5723b9cef/storage-proto/proto/confirmed_block.proto),
  SHA-256 `c29a6db1c76fc1361f2d3920bdbda01f6270b6743c5bdc19997a9f4720ccdcec`;
- [storage-proto/src/convert.rs](https://github.com/anza-xyz/agave/blob/6c1ba34691f17ac902ae3d2e1147eed5723b9cef/storage-proto/src/convert.rs),
  SHA-256 `5884bda534f1824b62befe70795e4250fe1302a99acbe1ad4f0ca98856163f4b`.

Bronbestanden waren al lokaal behouden; niets wordt opnieuw opgehaald.
`TransactionStatusMeta` heeft herhaalde TokenBalance-velden 7/pre en 8/post.
De converter bewaart geen onderscheid tussen `None` en een lege lijst.
Er bestaat daarvoor geen `*_none`-vlag. Geen waarnemingen betekent dus
**leeg of niet geregistreerd**, niet nul tokenbalans of nul activiteit.

De afzonderlijke Rust-projectie behoudt alle nested protobufbytes en de
oorspronkelijke transaction-statusbytes. Een aanwezige balans behoudt zijde,
volgorde binnen die zijde, uint32-accountindex, opgeloste static/loaded key,
mint, owner, program_id, originele amount-string, gecontroleerde u64 en
decimals. Wire-aanwezigheid blijft afzonderlijk van proto3-defaultsemantiek.
Een ontbrekende decimals-tag binnen een aanwezige UiTokenAmount heeft default
0; een geheel ontbrekende UiTokenAmount levert geen verzonnen amount.
Ontbrekende/lege owner of program_id wordt geen geldig verzonnen adres.

Upstream conversie gebruikt vernauwende u8-casts en een amount-default bij
een displayfallback. Die informatieve conversies worden **niet** gebruikt
om overflow, ontbrekende amount of ongeldige accountindex te repareren.
`ui_amount` is niet canoniek: alleen de originele bytes/bits blijven bewijs.
Exacte decimale weergave wordt in Rust met integer/string-bewerkingen gemaakt,
zonder floats en alleen bij geldige eenheden-/adresbinding.

## Los van trade-toelating

Alle bestaande Pump-parsers en zeven toegelaten trades blijven ongewijzigd.
De aanvullende `token_balance_context` verbindt uitsluitend de bestaande
eventmint, benoemde accountrollen en tokenprogramma aan pre/postmetadata.
Conflicterende decimals, dubbele/ontbrekende relevante balanswaarnemingen
of een adres-/owner-/programmaverschil blijven expliciet; zij veranderen
geen eerdere trade-toelating.

De nieuwe context houdt drie grootheden apart:

1. event-gerapporteerde tokenhoeveelheid;
2. instructiegrenzen/argumenten;
3. metadata-gerapporteerde pre/postbalansen en eventueel hun **transactiebrede**
   signed delta, uitsluitend bij een unieke passende pre én post.

Een delta kan meerdere instructies, transfers of andere activiteiten omvatten;
zij wordt nooit automatisch aan één Pump-event toegeschreven. Tokenbalansen
zijn geen volledige historische accountsnapshot. CPI-privileges, ontbrekende
accountinhoud, quote-economie, fees/nettoproceeds en uitvoerbare prijzen worden
niet aangevuld. Oude `base_decimals`-/quotevelden blijven behouden; nieuw
metadata-eenhedenbewijs staat alleen in de afzonderlijke context.

## Parquet en daadwerkelijke queries

Rust Arrow/Parquet bewaart de volledige canonical records plus getypeerde
`List<Struct>`-waarnemingen op Bronze. Silver heeft eigen eenhedenkolommen,
gekoppelde waarnemingsindices en getypeerde accountrolcontext. Raw bytes,
NULL/MISSING, side-order en eventuele duplicaten blijven behouden.
De fysieke read-back vergelijkt iedere kolom met het volledige record.

DuckDB leest uitsluitend manifestbestanden en de vaste
[tokenbalansqueries](../../research/columnar-query/token-balances.sql.json).
De query onderscheidt de globale waarnemingsindex van de volgorde binnen
pre/post. Python decodeert niets en rekent geen alternatieve Silver-semantiek
of decimale tokenwaarde uit. Een oude manifestprojectie zonder deze kolommen
blijft `UNAVAILABLE_NOT_PROJECTED_BY_ORIGINAL_WRITER`, niet nul.

## Werkelijk uitgevoerd op 2026-09-14

Nieuwe outputroot, buiten Git op WSL ext4:
`/home/dmesdary/solana-quant-data/datasets/b5-token-balances-20260914.2bDA7H`.
Alle oude outputs en de 326 oorspronkelijke acquisitiebestanden blijven
ongewijzigd. `identities.json` bewaart de afzonderlijke offline binaries;
`query-integration-identity.json` bindt de latere gerichte querycorrectie.
De acquisitiebinary is niet vervangen of uitgevoerd.

| Slot | Pakketten | Mislukte transacties behouden | Pakketten met balansen | Pre/postwaarnemingen |
| --- | ---: | ---: | ---: | ---: |
| 422669516 | 1.022 | 49 | 146 | 2.456 |
| 422669517 | 1.048 | 76 | 148 | 2.367 |
| 422669518 | 1.154 | 98 | 187 | 2.689 |
| Totaal | 3.224 | 223 | 481 | 7.512 |

De overige 2.743 pakketten hebben geen opgenomen balanswaarnemingen:
leeg of niet geregistreerd, **geen** bewijs van nul saldo of activiteit.
Er zijn 3.762 pre- en 3.750 postwaarnemingen. Bij 44 waarnemingen is de
decimals-tag afwezig en geldt de expliciet gelabelde protobuf-default 0.
Alle zeven bestaande trades hebben daarentegen vier passend gebonden
waarnemingen met **expliciet opgenomen decimals=6**: gebruikers- en
curve-account, elk pre/post. Dat is 28 waarnemingsbindings / 14 accountrollen.
Alle 7.512 projecties zijn onafhankelijk tegen de oorspronkelijke protobuf
gecontroleerd; aantallen en decimals komen niet uit een allowlist.

Vier buys, drie sells, 223 mislukte transacties en alle afwijzingen blijven
ongewijzigd. Voorbeeld: het opgenomen buy-event bij 422669516/647 rapporteert
`137631740185` raw; de afzonderlijke metadata-eenhedencontext toont exact
`137631.740185`. Dit is geen quoteprijs, fill of toegeschreven balansdelta.
De zes trade-mints bevatten nog geen mint met beide ondersteunde kanten
binnen deze selectie; dat zegt niets over niet-opgenomen activiteit.

`parquet-01/manifest.json` noemt twee Bronze-bestanden met elk 1.612 rijen
(34.569.138 en 41.824.131 bytes) en één Silver-bestand met zeven rijen
(327.058 bytes). Alle fysieke bestanden blijven onder 64 MiB / 5.000 rijen.
De 58.830.898 Bronze-JSONL-bytes, 222.485 Silver-JSONL-bytes en
59.305.259 compacte quality-bytes passen binnen de bestaande B5-limieten.
Grootste Bronze-record: 166.666 bytes. Pieken bij de daadwerkelijke workers:
decoder 477.932 KiB, projector 45.420 KiB, query 174.360 KiB,
coverage 449.624 KiB; ruim binnen 2 GiB per proces.

Twee volledige canonieke decode-uitvoeren zijn gelijk. Herhaalde projectie
met dezelfde oorspronkelijke processingreceipt produceert byte-identieke
Parquetbestanden en manifests; de twee echte processingtijden blijven apart.
Query-JSON en coverage-HTML zijn eveneens byte-identiek. De 725- en
3.137-regressies behouden alle oorspronkelijke feiten, inclusief vier
engineering-sells. `fresh-artifact-review.json`,
`legacy-token-balance-result.json` en `determinism.json` bevatten het bewijs.

Belangrijkste SHA-256-identiteiten:

- offline decoder: `a97deb6af336c2f08afbf76d544a61c5edee60852e7063ffd985a98bcdb734fb`;
- offline projector: `f45d2c34921e9b278900532a34bccb5369fd073d52414c4cfc892bf3433ffc08`;
- Parquetmanifest: `29bca68b7f25af38c3a800a353aeb58d926f08090c192fb4dbf66099b90ce4ce`;
- Bronze-JSONL: `623d877957d3343f87f8056eb870cdc5d652885dff5e093783caf483cb388a6d`;
- Silver-JSONL: `4b02618ed0f5008336ee3a2ad86e705e10f0668712459a3388d0947b0df86424`;
- coverage-queryresultaat: `ea6e79e51983e334586c0aa82a8056d00c5418365dc32460038de49e5d389f5b`;
- onafhankelijke artefactreview: `fa3f607066419fd190152b05e0508cee6166041b201072175b6b10c6b6f28b06`.

De queryrun vond één echte integratiefout: de oude `rejected_buy`-query hield
de volledige grotere parent-JSON vast tijdens `json_each` en raakte de
256-MB-DuckDB-cap. De gerichte materialized diagnose-subtreeprojectie behoudt
alle rijen, velden en volgorde. Onafhankelijke oude/nieuwe querypariteit over
alle 3.224 Parquetrijen is bewezen; de memory-cap is ongewijzigd.
De oorspronkelijke foutlogs blijven aanwezig. Eveneens behouden: één
linker-threadresourcefout (opgelost met één bouw-CPU) en de eerste volledige
Bronze-fixturetesttimeout op één CPU. De finale Bronze-gate slaagt met normale
CPU-paralleliteit en dezelfde timeout/asserties, zonder protocolwijziging.

### Lokaal bekijken

Browser: `http://localhost:7035`. Zo nodig opnieuw starten:

```bash
/home/dmesdary/solana-quant-data/datasets/b5-parquet-20260912.NO4c2L/duckdb-venv/bin/python -B -m http.server 7035 --bind 127.0.0.1 --directory /home/dmesdary/solana-quant-data/datasets/b5-token-balances-20260914.2bDA7H/coverage-01
```

`coverage-01/index.html` toont de uitgevoerde DuckDB-tabellen, zeven tradecards,
balansgaten en bronbindingen; `query-01/index.html` toont de afzonderlijke
controlequeries. Elf echte Windows-Chrome-screenshots en HTTP-/bestandhashpariteit
staan onder `browser/expanded/`. De rapportgegevens zijn authentieke opgenomen
metadata; grenswaarde-, corruptie- en geheugenregressies zijn synthetisch.
Native `RESEARCH_SAMPLING`-identiteit blijft leidend; oudere statische
engineering-verifierlabels zijn geen nieuwe sampleclassificatie.
Research Ready blijft false. Geen GitHub- of providerverzoek is uitgevoerd.

### Uitgevoerde lokale verificatie

- Volledige Node/Vitest-suite: 105 bestanden / 1.585 tests; kritieke suite 101 tests.
- Volledige Bronze-gate: 135 Rust-tests, fmt, clippy, graph/build en socket-denial.
- Finale Parquet-gate: 36 Rust-tests en 52 Python-tests, fysieke round-trip,
  multishards, DuckDB, fmt, clippy, graph/build en socket-denial.
- De bestaande reducer-/Pump-gates, TypeScript en default build zijn groen.
- Onafhankelijk herhaald: 13 nieuwe Bronze-tests, zes Parquet-tests, zeven
  Python-tests en de volledige authentieke bron-/artefactvergelijking.
- `gates-general/`, `gates-bronze-final/`, `gates-parquet-query-final/`,
  `logs/` en de `fresh-*-review.json`-receipts bewaren commands en hashes.

De testcase met 64 MiB synthetische parent-inhoud schrijft acht expliciet
genoemde fixturebestanden en toetst de smalle SQL onder 256 MB. Deze
SQL-vormtest is geen alternatieve canonieke Bronze/Silver-writer.
Geen installatie of remote CI is uitgevoerd; npm ci is bewust niet herhaald.

## Voorstel kleinste tijdsvervolg — niet uitvoeren

Het [machineleesbare voorstel](../../research/columnar-query/token-balance-horizon-proposal.json)
definieert één beschrijvende vraag met **16 volgende slots per oorspronkelijke
pilotwaarneming**. De ontbrekende context-unie is `[422669519,422669535)`;
de bestaande `[422669516,422669519)` blijft onveranderd. Er wordt geen vaste
slotduur in seconden aangenomen. Zestien is een expliciete voorgestelde
ontwerpkeuze, niet bewezen universele toereikendheid. Prebalansen bij de
transactie volstaan als lokale uitgangscontext voor deze vraag, niet als
beginsaldo/lancering van een coin.

Stop bij dat vaste eindpunt, ook zonder buy/sell-paar. Geen selectie op
rendement, herloting, vervanging of voortgezette uitbreiding. Dit zou
post-hoc beschrijvende context zijn, **geen** nieuwe uitkomstonafhankelijke
researchsample en geen wijziging van de oorspronkelijke sample-identiteit.
Een toekomstige echte onderzoekssteekproef moet het horizonrecept vooraf
vastleggen, los van reeds bekeken uitkomsten.

Voor uitvoering ontbreekt nog een kleine run-/manifestgebonden batchroute:
de huidige Bronze-CLI heeft drie-slot- en 16-MiB-Raw-selectiegrenzen.
Zestien nieuwe slots vereisen minimaal zes batches, mogelijk meer door
byte-/recordcaps; geen fictieve gekopieerde een-slotruns. Een collectie moet
de oorspronkelijke selectie en alle expliciete batchmanifests binden.
Behoud 24-MiB-slotoutput, 64-MiB-selectie/invoer en 5.000 records / 64 MiB
per Parquetbestand. Concrete toekomstige byte-/request-/runtimebudgetten zijn
nog niet gemeten of goedgekeurd. Geen toekomstige uitvoerbaarheid claimen
op basis van alleen het aantal slots. Research Ready blijft false.
