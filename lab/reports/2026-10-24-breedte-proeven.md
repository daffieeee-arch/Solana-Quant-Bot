# Breedte-proeven E71–E74: vier vooraf vastgelegde stop/go-proeven op ongeteste families (24 oktober 2026)

Opdracht van de coördinator (24 okt, audit-actie 5): per ongeteste familie één vooraf vastgelegde stop/go-proef op dev 1033–1046, beoordeeld op de eigen nettomarge per trade na kosten bij d 3 en drain-bewuste prijspaden, niet tegen "< 20 SOL/dag". Plan **v0.2** `research/breedte-proeven-plan-2026-10-24.md` sha256 7bbd1aa112bdad00bba8ff504749d6bb04cf1af99b4ee9fd3ae9d70b590490b1 (v0.1 503b0346…), bevroren vóór het eerste getal; coördinator en LAB review akkoord. Code: `lab/wallets/probes/` + `lab/wallets/nas_probes.sh` (#546 gemerged 4064111: harnas, E74, E71; #547 gemerged 5cbdf23: E72, E73). Deze noot groeit per proef; nu E74. E71 draait op commit 03bd9a7, E72/E73 op 5cbdf23 (NAS zaterdagavond).

## 0. Kort

- **E74 niet-SOL-quote (PUMP-curves): STOP (feit).** Nettomarge per trade bij 0,5 SOL **−58,9 % [−61,2; −56,5]** (dag-geclusterd, 17 dagen); 262 trades/dag; 52 % van de posities verliest ≥ 90 %; **−77 SOL/dag bij 10 SOL**, −213 bij 25, −1.098 bij 100. De PUMP→SOL-omrekening is niet de oorzaak: in PUMP-eenheden (zonder omrekening) is de marge −58,1 %. Het verlies zit in de curves zelf: twee derde van de posities eindigt op de stop en op curves ≥ 10 SOL diep is de marge nog −21 %. 1 blik, geen vervolg.
- E71 (Mayhem-curve), E72 (ticker-clusters), E73 (goede wallets samen): volgen na de NAS-runs van zaterdagavond.

## 1. Raamwerk (plan §1, kort)

Instap op de eerste vulbare trade ≥ t + 3 slots met eigen impact, venue-fees uit de rij en base-netwerkkosten (`wallets.runners`); marks = netto van een volledige verkoop op de toestand ná elke latere trade (pool-marks incl. Withdraw/Deposit, gecapt op de echte SOL); uitstap trail30 (mark ≤ 70 % van de hoogste eerdere) of stop (≤ 50 % van de kosten), verkoop op de eerste mark ≥ triggerslot + 3, anders de laatste mark binnen 6 u; positie = kapitaal/20 (0,5 / 1,25 / 5 SOL), 5 plekken, één positie per key, signalen in triggervolgorde; primaire maat = Σ netto / Σ kosten bij 0,5 SOL met dag-bootstrap (10.000×, seed 7, nuldagen meegeteld); GO = ondergrens > 0 én ≥ 3 trades/dag. Optelbaarheid: Pearson van de dag-netto met H5 (E63-parts, flow300_tp100_H6 organic d 3) en hold300 (geen arm-filter) en het aandeel triggers binnen 60 s op dezelfde key; beschrijvend.

## 2. E74 — curves met PUMP als quote (plan §5): STOP

**Bron (feit):** NAS-job 99zzzzz62 op commit 03bd9a7, rc 0 (03:49Z), `wallets/probes-e74-03bd9a7/` (SHA256SUMS 1ff8b367… over 110 bestanden; `report-e74.json` 0ede9370…, `report-e74.md` 3e5612e8…); diagnose `research/breedte-proeven-2026-10-24/e74_diag.py` 0ff4f265… / `e74_diag.out` c18abe9a… (SHA256SUMS 6e71fec8…). Universum: PUMP-quote mints, niet-Mayhem, create vanaf slot 447.266.123 (epoch 1035, de creatie van de referentiepool): 12 epochs, 17 UTC-dagen, 6.282 e10-signalen op 6.282 mints; 0 prijs-fallbacks, 0 rijen zonder kosten.

| cel | kapitaal | signalen | genomen | trades/dag | marge (SOL) | marge (PUMP-eenheden) | CI 95 % | SOL/dag | ≥ 90 % verlies | mediaan hold |
|---|---|---|---|---|---|---|---|---|---|---|
| e10 | 10 SOL | 6.282 | 4.450 | 261,8 | **−58,9 %** | −58,1 % | [−61,2; −56,5] % | **−77,3** | 52,4 % | 115 slots |
| e10 | 25 SOL | 6.282 | 4.638 | 272,8 | −62,5 % | −62,0 % | – | −213,1 | 54,2 % | 98 |
| e10 | 100 SOL | 6.282 | 5.239 | 308,2 | −71,3 % | −71,1 % | – | −1.098,3 | 49,6 % | 52 |

Zonder capaciteit (alle 6.282 signalen, 0,5 SOL): −57,8 % in SOL, −57,1 % in PUMP. Dagblokken 1–9 / 10–20: −494 / −819 SOL netto; per epoch −52 … −68 %, geen enkele epoch boven −50 %. Afgekapt aan de hold-out: 0.

**De omrekening is niet de oorzaak (feit, e74_diag.out):** de referentieprijs (mid-prijs van pool 5r9N5yEc…, mediaan 0,0358–0,0483 lamports per PUMP-eenheid per epoch, min/max binnen een epoch ±8 %) staat bij uitstap gemiddeld op 1,0001× die bij instap (mediaan 1,0000); de referentie is gemiddeld oud (p50 8.718 slots ≈ 39 min, p90 35.786) en de pool is 0,005 SOL diep (modelaanname: mid-prijs + 0,30 % per kant), maar het verschil tussen SOL- en PUMP-marge is 0,8 punt op −58.

**Waar het verlies zit (feit):** per uitstapreden (0,5 SOL, alle signalen): stop 2.466 × −74,1 %, end_after_stop 1.614 × −99,2 % (de stop viel en er kwam geen trade meer: de positie staat op de laatste mark), trail 2.052 × −11,8 %, end_after_trail 57 × −93,9 %, end 93 × +146 %. Slechts 34,7 % van de instappen ziet ooit een mark boven de kosten; 9,6 % krijgt na onze koop geen enkele trade meer. Diepte van de PUMP-curve bij instap (in SOL via de referentieprijs): p10 0,07, p50 3,2, p90 14,0 SOL; in 25,8 % van de instappen is ons budget (13,5 k PUMP ≈ 0,5 SOL) groter dan de hele echte reserve. Marge per dieptebucket: < 0,1 SOL −83 % (820), 0,1–0,5 −87 % (811), 0,5–2 −75 % (963), 2–10 −49 % (2.508), **≥ 10 SOL −21 % (1.180)**. Ook de diepe curves verliezen dus; de dunne maken het extreem. 96,5 % van de posities is binnen een uur gesloten (mediaan 96 slots ≈ 26 s).

**Optelbaarheid (beschrijvend):** Pearson dag-netto met H5 0,16 en met hold300 0,35 over 17 dagen; 0 van de triggers binnen 60 s op dezelfde key als een H5- of hold300-instap (andere munten). Niet relevant bij STOP.

**Oordeel:** STOP — de ondergrens ligt ver onder nul in elke lezing (SOL, PUMP, met en zonder capaciteit, elke epoch, elke dieptebucket). Geen vervolg-pre-reg. 1 blik (de primaire cel; kapitaal 25/100 en de diagnose zijn beschrijvend). Lezing in één zin: PUMP-quote-curves verliezen op dev ongeveer 58 % per trade met trail30/stop, de helft van de posities is een drain.

**Wat dit niet zegt:** niets over USDC- en overige quotes (geen SOL-koers in de store, plan §5.1) en niets over Meteora DLMM (nooit geëxtraheerd, plan §6); niets over een andere instap dan e10 of een andere uitstap dan trail30/stop (één regel vooraf).

## 3. E71 — Mayhem-munten in de curvefase: volgt

NAS zaterdagavond op commit 03bd9a7 (`STEP=e71`); eerst de pariteitspoort (`parity_ok`), dan §2.0 basisrates over alle Mayhem-mints, dan de cellen e10 en e10_agentfree.

## 4. E72 — ticker-/naamhypes: volgt

NAS op commit 5cbdf23 (`STEP=e72`); cellen copy (primair), copy2–5, orig.

## 5. E73 — meerdere goede wallets samen: volgt

NAS op commit 5cbdf23 (`STEP=e73`); goede set (plandrempel 5 SOL = 5e9 lamports) ≈ 8–14,5 k wallets per dag (feit, sizing-query 24 okt, niet gepind); cellen k2n30 (primair), k3n30, k2n10, k2n120. Lezing vooraf: "hun gelijktijdige kopen volgen", niet "kopiëren".

## 6. Blikken

Conservatief geteld (plan §1 v0.2): E74 1 (primaire cel), E71 2, E72 2, E73 4 = 9 bij voltooiing; na E74: 1. Teller ≈ 2.303.
