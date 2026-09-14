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
