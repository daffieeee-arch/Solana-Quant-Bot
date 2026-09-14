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
