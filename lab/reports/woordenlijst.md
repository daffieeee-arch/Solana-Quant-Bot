# Woordenlijst: codes en begrijpelijke namen

Voor de eigenaar, de UI en de coördinator. Bij elke code de naam die wij in rapporten naast de code gebruiken. Bron: de specs in `data-old-faithful-one/lab/research/` en het register (`experiment-register.md`).

## Markten en momenten

| code | naam | uitleg |
|---|---|---|
| curve | de bonding curve | pump.fun vóór de migratie: prijs volgt een vaste formule, geen LP |
| pool / normale pool | PumpSwap-pool na migratie | token als basis, SOL als quote; ontstaat bij de migratie van een pump.fun-token |
| pool_reversed / reversed pool | omgekeerde PumpSwap-pool | SOL als basis, token als quote; door derden aangemaakt, met een LP die kan opnemen (drain) |
| mayhem | mayhem-curve | curve die door de Mayhem-agent wordt gereset; uitgesloten in alle universa |
| farm / flagged / organic | met of zonder volume-farmers | flagged: farmers maakten > 50 % van het SOL-volume in het laatste uur; organic: niet |
| d, d_ms | onze vertraging | slots tussen de beslissing en onze transactie; d_ms 600 = 2 slots bij 267–316 ms, 3 slots bij ≤ 240 ms |
| regime (320ms / 270ms / 200ms) | slottijd-regime | gemeten slotduur per epoch; na Alpenglow (epoch 1064) één nieuw regime |

## Signalen (instapmomenten)

| code | naam | uitleg |
|---|---|---|
| F7 | herstel na een grote verkoop | iemand verkoopt ≥ 3 SOL of ≥ 4 % van de diepte, de instroom erna is positief: wij kopen |
| F7 pool_reversed | herstel na een grote verkoop in omgekeerde pools | het enige positieve signaalmoment tot nu toe |
| N2 / N2_F7 | schaduwsignaal | zelfde moment op een willekeurige vergelijkbare pool, als controle |
| F1 | herstel na een grote verkoop op de curve | negatief |
| M1, M1+, M1-early (M1E) | instap bij de migratie | kopen vlak na de overgang curve → pool; negatief |
| c1 / c5 / c15 | curve-momenten | instap 1 / 5 / 15 minuten na de creatie van een token; negatief |
| K1–K3 | de hold-out-kandidaten | drie varianten van F7 pool_reversed; K3 (tp4_T600, alle pools) draait in paper |

## Exits (uitstapregels)

| code | naam | uitleg |
|---|---|---|
| tp4_T600 | +4 % of 10 minuten | verkopen bij +4 % netto, bij −8 %, of na 600 s |
| hold180 / hold300 | vasthouden 3 / 5 minuten | geen stop-loss, verkopen na de tijd |
| flow300_H6 | vasthouden zolang de echte instroom positief is, max 6 uur | verkopen als de netto SOL-instroom over 300 s negatief wordt |
| flow300_tp100_H6 | idem, met winstnemen op +100 % | zoals flow300_H6, maar ook verkopen bij +100 % netto |
| tp50_H6 / tp100_H6 | winstnemen op +50 % / +100 % | geen stop-loss, plafond 6 uur |
| trail20/30/50 | nalopende stop | verkopen als de koers 20/30/50 % onder zijn hoogste punt sinds de instap komt |
| sl8 | stop-loss −8 % | toevoeging aan een exit |
| busy_until | pool bezet | per pool hooguit één open positie; de realistische regel houdt de pool bezet zolang de positie onverkocht is |

## Hypothesen en toetsen

| code | naam | uitleg |
|---|---|---|
| E20 (H1/H2) | de wash-toets op schone data | vooraf vastgelegde toets of het wash-filter op K3 helpt, op clean-data |
| H3 | vasthouden 3 minuten tegen +4 %/10 min | hold180 tegen tp4_T600, op clean |
| H4 | organic, vasthouden op instroom | F7 pool_reversed, organic, flow300_H6: Σ netto SOL per dag > 0 |
| H5 | H4 met winstnemen op +100 % | flow300_tp100_H6 |
| E32 | de forward-toets | H4/H5 op data ná Alpenglow (≥ 1064), vastgelegd vóór die data bestaan |
| dev / hold-out / clean / forward | de datavensters | dev 1033–1046 (vrij verkennen); hold-out 1047–1051 (alleen diagnose); clean 1055–1063 (LOCKED tot E20/H3/H4/H5); forward ≥ 1064 (claims) |
| DSR / PBO | correctie voor het aantal proeven | Deflated Sharpe Ratio; Probability of Backtest Overfitting (CSCV) |
| dagbootstrap | onzekerheid over dagen | hele dagen opnieuw trekken (10.000 keer) voor het betrouwbaarheidsinterval |
| stopregel | GO / ADJUST / STOP voor paper | ondergrens van het interval > 0 → GO; alleen K3 telt |

## Labels in rapporten

| label | betekenis |
|---|---|
| feit | direct uit de data of een bron |
| reconstructie | afgeleid uit code of data, met een redenering |
| modelaanname | hangt af van een simulatie (fills, vertraging, kosten) |
| onbekend | niet te meten met wat we hebben |
