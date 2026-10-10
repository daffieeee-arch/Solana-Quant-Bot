# Overdracht: zwaar lab-werk naar de pc (2026-10-11)

Dit document is voor de nieuwe Claude-sessie **"LAB pc"** op de Windows-pc van de eigenaar (WSL2), en voor iedere sessie die later instapt. `AGENTS.md` in de root blijft leidend. Bij twijfel: vraag het de coördinator.

Deze repository is **publiek**. Zet hier nooit sleutels, tokens, IP-adressen, e-mailadressen of andere persoonsgegevens.

## Waarom

De netcup-VPS knijpt het schrijven van de schijf sinds 2026-10-10 af tot ~2 MB/s. Lezen gaat wel normaal (~900 MB/s). In 2,7 dagen is daar ~1,3 TB geschreven en ~5,6 TB gelezen.

De pc van de eigenaar is geschikt voor het zware historische werk:
- Ryzen 7 7800X3D, 32 GB RAM;
- NVMe-SSD van 2 TB;
- WSL2;
- in het Tailscale-netwerk als `terrapc`.

De VPS houdt alle 24/7-taken.

## Rollen

| sessie | waar | taak |
|---|---|---|
| **Solana Coördinator** | VPS | coördinatie, specs, reviews, roadmap (Project #4), statuspagina; blijft op de VPS |
| **LAB pc** (nieuw) | pc, WSL2 | data, store-builds, toernooien en backtests; neemt het zware werk én het code-eigenaarschap van `lab/backtest/` over van LAB backtest |
| LAB backtest ("Week 1 beginning") | VPS | pusht zijn lopende werk naar PR #171 en stopt zodra LAB pc draait |
| LAB ui | VPS | web-app en pagina's; leest resultaten op de VPS |
| paper-bot (vanaf ~14 okt) | VPS | Triton-livestream, 24/7; nooit op de pc |

Gaat de pc uit, dan stopt LAB pc. Lange runs zijn hervatbaar (bevroren engine-kopie per run, resultaten per batch). De coördinator op de VPS draait door.

## Harde regels (kort; zie AGENTS.md)

- **Alleen paper:** geen private keys, geen signing, geen transacties, geen live geld.
- **Alleen Triton One als bron van chain-data:** Old Faithful (OF1) voor historie, Yellowstone voor live. Geen public RPC, Helius, QuickNode, Birdeye, DexScreener of andere providers als databron. DexScreener mag alleen als link waar de eigenaar op klikt.
- **Hold-out:** slots [452304000, 454464000) = epochs 1047–1051 worden **één keer** gebruikt voor strategie-uitkomsten. Mechanische validatie is toegestaan; ontwikkel daarvoor op epoch 1052.
- **Elke zware job in een geheugen-scope met de heavy.lock.** Op de pc:

  ```
  systemd-run --user --scope -p MemoryMax=14G -p CPUQuota=1000% -- flock -n "$LAB_DATA_ROOT/locks/heavy.lock" <cmd>
  ```

- **DuckDB:** zet altijd `max_temp_directory_size` (≤ 15GB). Lichte queries zijn read-only met `memory_limit` ≤ 2GB.
- **Geen geheimen** in git, logs, prompts of rapporten.

## Besluiten van de eigenaar

- **Stopregel:** netto P&L per trade na alle kosten, 95%-dag-bootstrap-CI.
  - GO: ondergrens > 0. ADJUST: gemiddelde > 0. STOP: gemiddelde ≤ 0.
  - Minstens 200 trades; de klok loopt hoogstens 6 weken.
  - De klok start uiterlijk 2026-11-02, met als doel 2026-10-26.
- **Kapitaal:** 10 SOL is het startkapitaal, niet de grootte per trade.
  - Gate en stopregel gelden bij 0,5 en 1 SOL per trade, voor d=1 én d=2.
  - Elk kandidaat-rapport bevat een kapitaalsimulatie: 10 SOL, vaste grootte, maximaal 5 posities tegelijk.
- **Vroege Triton-top-up** (~2026-10-14): een begrensde, gefilterde live-meting met auto-stop.
- **Retentie:**
  - ruwe live-data 3 dagen;
  - oude OF1-chunks mogen pas weg nadat de herbouwde store geverifieerd is, en alleen als de schijf dat vraagt.

## Stand van zaken

**Data (op de VPS):** `/home/chupa/Solana-project/data-old-faithful-one/lab`
- `events/v1/chunks/`: Parquet per 216.000 slots, ~4,7 GB per stuk, ~166 GB totaal.
  - Compleet: 446688000 t/m 454464000 (epochs 1034a–1051).
  - 446904000–447120000 (1034b) is half af; de extractor op de VPS staat bevroren.
  - Epoch 1033 (446256000–446688000) ontbreekt nog.
  - Epoch 1052 (alleen voor mechanische validatie) moet nog geëxtraheerd worden.
- `store/dev2.duckdb`: store v2 voor 1040–1046, met alle pools, reversed pools en farmer-vlaggen.
- `research/`: alle specs en besluiten:
  - `week2-strategy-specs.md`
  - `week2-defaults.yaml` plus `week2-defaults-addendum-2026-10-10.md` (het addendum gaat voor)
  - `week2-specs-S1-O7-2026-10-10.md`
  - `week2-specs-M1-2026-10-10.md`
  - de reviews `week2-tournament-v1-review.md` en `week2-tournament-v1.1-review.md`
- `backtests/`: de runs v1 en v1.1, met een bevroren engine per run.

**Resultaten:**
- Toernooi v1 en v1.1 geven STOP voor alles vanaf 2 SOL; de eigen prijsimpact domineert.
- Het enige lichtpunt: F7 in reversed pools bij 0,5 SOL en d=1, met +0,4…+0,7% per trade. Dat moet eerst de checks a–e uit de v1.1-review doorstaan:
  - a: farming-split;
  - b: exacte replay met fee-trede;
  - c: latency d=1/2/3;
  - d: concentratie;
  - e: mechanisme.

**Volgende stappen** (PR #171, lokale branch `lab/w2b-store-all-pools`):
1. Eén volledige store-build voor 1033–1046.
2. F7-reversed checks a+b.
3. Families v2:
   - F6 en S1: diepe pools;
   - O7: insider-overlay;
   - M1: snelle runner na graduatie, het idee van de eigenaar;
   - N3 en N4: controles.

## De pc inrichten

1. **WSL-limieten.** Maak in Windows `C:\Users\<naam>\.wslconfig` aan:

   ```
   [wsl2]
   memory=20GB
   processors=12
   swap=8GB
   networkingMode=mirrored
   ```

   Daarna `wsl --shutdown` in PowerShell. Met `networkingMode=mirrored` bereikt WSL de VPS via de Tailscale van Windows.
2. **systemd in WSL.** `systemctl --user status` moet werken. Werkt het niet, zet dan `[boot] systemd=true` in `/etc/wsl.conf` en herstart WSL.
3. **Python 3.13 met de pinned pakketten van de VPS-toolchain:** duckdb 1.5.5, pyarrow 26.0.0, numpy 2.5.3, pyyaml 6.0.3, pytest 9.1.1. Bijvoorbeeld via `uv`:

   ```
   uv venv --python 3.13 ~/solana-lab/lab-py
   uv pip install --python ~/solana-lab/lab-py duckdb==1.5.5 pyarrow==26.0.0 numpy==2.5.3 pyyaml==6.0.3 pytest==9.1.1
   ```

4. **Repo.** Log in met `gh auth login` (door de eigenaar) en clone naar `~/solana-lab/Solana-bot`. Zet `LAB_DATA_ROOT=~/solana-lab/data-old-faithful-one/lab` en `LAB_PY=~/solana-lab/lab-py/bin/python`.
5. **Data van de VPS halen.** Alleen trekken, de pc start de verbinding:
   - Maak in WSL een sleutel aan: `ssh-keygen -t ed25519 -f ~/.ssh/solana_vps -C lab-pc`.
   - De eigenaar geeft de publieke sleutel aan de coördinator. Die zet hem met akkoord van de eigenaar op de VPS, beperkt tot het tailnet-adres van de pc.
   - Daarna haal je de data op:

     ```
     rsync -a --info=progress2 -e "ssh -i ~/.ssh/solana_vps" \
       chupa@chupa:/home/chupa/Solana-project/data-old-faithful-one/lab/{events/v1/chunks,research,backtests} \
       ~/solana-lab/data-old-faithful-one/lab/
     ```

     Lezen op de VPS is niet afgeknepen. Er komt niets bij op de VPS.
   - Laat de half afgemaakte chunk 446904000-447120000 weg. Die heeft geen `_manifest.json` met status `complete`.
6. **Resultaten terug naar de VPS.** Alleen kleine bestanden: `report.md`, `summary.parquet`, `trials.parquet`, de P&L-curves en compacte `parts/` voor LAB ui. Overleg eerst met de coördinator over omvang en pad, zodat de VPS-schijf niet weer wordt afgeknepen.

## Open punten

- **Netcup-ticket** over de schrijflimiet: de eigenaar, met de tekst van de coördinator.
- **Backfill van 1034b en 1033:**
  - op de pc via het thuisinternet: elke epoch is ~900 GB stream, waarvan ~10 GB bewaard wordt;
  - of op de VPS zodra de limiet weg is.
  - De coördinator beslist met de eigenaar.
- **Snelle CI** (#172/#173): lab-only PR's zijn nu in ~2 min groen. De meting op de eerste lab-PR volgt nog.
- **Coördinator:** de VPS-sessie "Solana Coördinator". Statuspagina: de privé claude.ai-artifact "Lab status".
