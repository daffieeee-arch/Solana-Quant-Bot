# PumpSwap exact-in: correctie en beperkte impact

**25 oktober 2026 — PAPER ONLY.** De gedeelde Python- en Rustberekening is gecorrigeerd
voor de aangetoonde `buy_exact_quote_in` / `buy_exact_quote_in_v2`-rekenregel zonder
cashback. LP-onderzoek blijft geparkeerd. Geen diensten, journals, K3-instellingen of
paperklok zijn gewijzigd. Dit rapport is geen winstclaim.

## Fout en begrenzing

De bron berekent `n0 = floor(budget * 10000 / (10000 + som(bps)))`, berekent de
ceil-fees **op n0**, en trekt daarna een eventueel budgetoverschot af van de netto-invoer.
De fees blijven staan. De oude gedeelde code maximaliseerde een betaalbare netto-invoer
en berekende de fees opnieuw op die invoer. Dat geeft soms andere fees, tokens en reserves.

De algemene `net_for_budget` en bonding curve, exact-out, sells en cashback blijven
ongewijzigd. Cashback behoudt zijn bestaande model; deze correctie valideert dat model
niet. Eventuele transferbelastingen of andere onbewezen varianten krijgen geen nieuwe
pariteitsclaim. Pricing reserves zijn niet automatisch fysieke fee-vaultsaldi.

Bronnen: [vastgepinde officiële SDK en authentieke regressies](../core/tests/data/README.md).
De 18 verwachtingen komen uit oorspronkelijke eventvelden, niet uit de private correctie.
De bestaande 50.089 exacte private swapcontroles blijven aanvullend bewijs; deze taak
herhaalt die hele LP-verwerking niet en presenteert ze niet als een nieuwe Rustmeting.

## Vooraf vastgelegde vergelijking

Plan: `lab/research/pumpswap-exact-in-impact-20261025/plan.json` onder de OF1-root,
SHA256 `531102f21fc70af856e3ba135ef5a6aadd87c1273ac7d9acbd574054d7343d58`.
Baseline `44f9da2690be7535ed3bea7967f491b2e34be899`; geteste implementatie
`649d7040f96bc8c3f587549f7f510a5a2bd87bcd`.

- Broncontrole: eerste 512 expliciete exact-in buys per SOL-pooloriëntatie in de bestaande
  DEVELOPMENT-chunk `[450576000,450792000)`, in chainvolgorde, zonder cashback.
  Geen selectie op afwijking of opbrengst. Oudere extracties hebben geen mint-accountkolommen;
  oriëntatie komt uit de bestaande store. De onvoltooide eerste query bleef bewaard.
- Economisch: eerste 32 verschillende F7 `(t,pool)`-signalen in de eerste bestaande
  DEVELOPMENT-partitie, zonder uitkomstfilter: 8 pools, slots 446531016–446568010.
  K3 blijft 0,5 SOL, TP 4%, maximaal 600 seconden, 600 ms gesimuleerde vertraging,
  q=0,5, bestaande 5%-limiet en empirische routertolerantie. Kostenscenario `base`,
  **settlementregel 2 in beide armen**. Dezelfde volledige transactiepackages worden hergebruikt.
- Dit zijn onafhankelijke positie-replays, **geen** volledig risicogestuurd K3-portfolio,
  geen nieuwe strategievariant en geen holdoutinspectie. De slice is vroeg en klein;
  nul effect hier sluit effect elders niet uit.

## Bronbedragen vóór/na

| Selectie | Exact oud | Exact Python nieuw | Exact Rust nieuw | Gewijzigde uitkomsten |
|---|---:|---:|---:|---|
| 18 eerder aangetoonde fouten, bekeken epoch 1064 | 0/18 | 18/18 | 18/18 | 18 token-, netto- en fee-bedragen; 18 base-reserves; 16 effectieve quote-reserves; 2 debetten |
| 512 normale DEVELOPMENT-swaps | 509/512 | 512/512 | 512/512 | 3 fees/debetbedragen elk +1 lamport; tokens en reserves gelijk |
| 512 omgekeerde DEVELOPMENT-swaps | 512/512 | 512/512 | 512/512 | Geen bedrag gewijzigd |

Concrete authentieke regressie: slot 459671898 / tx 77 / outer 6 / inner 7,
quote-budget 2.585.182, fees 20/5/95 bps:

| Raw bedrag | Oud | Bron = nieuw |
|---|---:|---:|
| Netto quote | 2.554.526 | 2.554.525 |
| Fees | 30.656 | 30.657 |
| Tokens | 2.078.566.227 | 2.078.565.413 |
| Debet | 2.585.182 | 2.585.182 |
| Base-reserve na | 123.303.674.018.042 | 123.303.674.018.856 |
| Effectieve quote-reserve na | 151.540.823.718 | 151.540.823.717 |

Alle 1.024 DEVELOPMENT-swaps blijven onder hun bestaande min-out geaccepteerd;
0 gewijzigde fill-/afwijzingsbeslissingen. Een gerichte synthetische grenscontrole
op authentieke invoer toont wel het relevante risico: min-out één raw token boven
het echte resultaat wordt nu geweigerd, waar de oude overfill kon slagen.

## Netto-impact en K3

| Vaste 32 K3-posities | Oud | Nieuw Python | Nieuw Rust-math |
|---|---:|---:|---:|
| Gevulde posities | 32 | 32 | 32 |
| Take-profit / tijdexit | 26 / 6 | 26 / 6 | 26 / 6 |
| Nettoresultaat, SOL | −1,776221758 | −1,776221758 | −1,776221758 |
| Verschil t.o.v. oud, lamports | — | 0 | 0 |

Alle complete replayresultaten zijn gelijk: ook marks, tokens, entry/exit,
exitpogingen en historische afwijzingen. De Rust-arm gebruikt de echte nieuwe PyO3
venuefuncties in dezelfde Pythonreplay; dit is geen opnieuw gedraaide native live-engine.
Native Rust PositionSim blijft daarnaast gedekt door de bestaande crate-tests.

**K3 heeft wel een geraakt pad:** in een omgekeerde pool koopt zijn instap tokens via
`rev_buy_token → pool_sell` (direct ongewijzigd), maar zijn marks/uitstap gebruiken
`rev_sell_token → pool_buy_exact_in`. Ook historische exact-in legs worden opnieuw
berekend. Een nuleffect in deze 32 posities is dus geen verklaring dat K3 de functie
niet gebruikt, en geen herberekening van zijn paperresultaat.

## Routes en modelovergang

| Route | Berekening / status |
|---|---|
| Python replay en paper PositionSim | `backtest/venues.py`; historische `_rerun_pool`, normale eigen buys, omgekeerde marks/exits |
| Python met `LAB_CORE=rust` | `venues_core.py → labcore → lab/core/src/venues.rs` |
| Native Rust replay/paper | `lab/core/src/positions.rs` en `lab/live/src/paper_core.rs` gebruiken dezelfde Rustvenuefuncties |
| Actieve paperdienst bij controle | worktree `paper-run`, commit `2b695731be123ea82740f41d657ec44e1454c85b`; oude exact-in-regel 1, nieuwe Pythonsettlements kostenregel 2 |

`POOL_EXACT_IN_RULE=2` identificeert de correctie, afzonderlijk van settlementkosten.
De Rustbinding exporteert die regel; nieuwe Pythoncode weigert een oude/mismatched
extension in plaats van stilzwijgend oude math te gebruiken. Rustkosten blijven regel 1;
Pythonkostenregel 2 en historische journaalbetekenis worden niet aangepast.

**Niet uitgerold.** Een latere overgang moet commit én extension/binary-hash, tijdstip en
journal-high-water-mark vastleggen, met een expliciete behandeling van reeds open posities
(bijvoorbeeld oude positie-uitgangen op de oude code afronden). De bestaande entries/exits
worden niet herschreven en de paperklok wordt niet gereset. Deze taak voert die overgang niet uit.

## Controle en reproduceerbaarheid

- 130 native Rust unit/property-tests geslaagd, inclusief source-regressies en PositionSim.
- 45 gerichte Pythontests; dezelfde 45 met de nieuwe Rustbinding geslaagd.
- 12 bestaande Python/Rust-pariteitsgroepen geslaagd (20.000 gevallen per reguliere groep).
- De kosten-dustfixture is aangepast: het oude aantal liet onder regel 2 geen dust meer over.
  Het nieuwe expliciete budget laat aantoonbaar één unit over; de oorspronkelijke rent- en
  P&L-assertions blijven intact. Geen wijziging aan kostenproductiecode.
- Eerste Cargo-offline-poging stopte door ontbrekende registry-index/cache. Geen installatie.
  NAS `rustc 1.97.1` compileerde de actuele bronnen met bestaande proptest/PyO3-artefacten;
  beide Cargo.lock-bestanden zijn identiek aan hun artefactbron. Overflowchecks bleven aan.
  Het complete Rusttestprogramma en de werkelijke Pythonextension zijn uitgevoerd.

Bewijs buiten Git: `lab/research/pumpswap-exact-in-impact-20261025/` onder de OF1-root.
`report.html` is de private leesweergave; `impact.json`, beide Pythonarmen en `new-rust.json`
bevatten de exacte vergelijking. `REPRODUCE.md`, bronhashes en NAS-logs leggen de route vast.
Impact-JSON SHA256: `b4e65d49ead2664eb4706a2b8f83cbb35540e8ed0de253b720a67d682902f718`.

**Oordeel:** echte afrondingsfout hersteld en bronpariteit aangetoond. De beperkte K3-selectie
verandert economisch niet. Andere onderzoeksresultaten zijn hiermee niet universeel
herberekend of vrijgepleit. Simulated landing, werkelijk haalbare latency en winstgevendheid
blijven aparte vragen; deze reparatie start geen nieuwe strategiezoektocht.
