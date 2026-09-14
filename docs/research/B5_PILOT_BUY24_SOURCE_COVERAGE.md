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
