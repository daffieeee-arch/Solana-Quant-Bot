# Phase 7 — Old Faithful Pilot A Readiness Package

> **Document status: HISTORICAL.** Legacy readiness evidence only. All ranges, caps and host-shaping values below are non-authoritative for V2 and authorize no call. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

## Status

**CANDIDATE_UNAPPROVED — HOLD_UNPROVEN_ACTIVATION**

Dit package ontwerpt en valideert uitsluitend de offline readinessgrens voor een toekomstige:

`OLD_FAITHFUL_EVENT_TRANSPORT_PILOT`

De flags zijn permanent fail-closed:

- `approved: false`
- `researchReady: false`
- `pilotEligible: false`

De evaluator kan deze waarden niet verhogen. Een structureel geldige kandidaat eindigt in `HOLD_UNPROVEN_ACTIVATION`; ongeldige of driftende input eindigt in `QUARANTINED`.

## Merge record

- PR #14 squash-merged this package to `main` as `a5f2edf1cba51cc350e4809b66a8b018debbf6f2`.
- Post-merge GitHub Actions run `32350736436` completed successfully with focused readiness 86/86, Node 1,197/1,197 across 72 files, Rust 68/68, TypeScript, builds, transport isolation, policy, four Rust format checks, clippy `-D warnings`, locked Rust tests/build, committed push integrity, and tracked-tree cleanliness.
- Merge records delivery only. The package remains `CANDIDATE_UNAPPROVED`, `HOLD_UNPROVEN_ACTIVATION`, `approved: false`, `researchReady: false`, and `pilotEligible: false`.
- All ten registry entries remain `STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`; accepted Silver decode for real data remains forbidden.
- No bandwidth-preflight, network shaping, CAR/range retrieval, archive stream, Pilot A, Pilot B, real slot processing, ClickHouse/backfill, strategy research, OOS, execution, or profitability work is authorized.

Deze fase deed en autoriseert:

- geen CAR- of HTTP-rangedownload;
- geen block-, transaction- of slotpayloadread;
- geen archive-stream of Jetstreamer-firehose;
- geen bandwidth-cap-preflight;
- geen echte slotverwerking of Pilot A-uitvoering;
- geen Pilot B;
- geen ClickHouse, backfill, Prometheus/Grafana-installatie, Redis, deployment of appstart.

## 1. Geïsoleerde implementatie

De packagebranch is gemaakt vanaf exact repository-main:

`16fa8fac09b3a96f1210503152f10b6ff6af5e4a`

Werk vindt uitsluitend plaats in de afzonderlijke worktree:

`/opt/data/worktrees/solana-paper-scanner-old-faithful-pilot-a`

De primaire checkout `/opt/data/solana-paper-scanner` is niet gewijzigd.

Nieuwe readinessartifacts:

- `docs/research/OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST.json`
- `docs/research/OLD_FAITHFUL_PILOT_A_PLAN.json`
- `docs/research/OLD_FAITHFUL_QUANT_DATA_SPEC.md`
- `src/research/old-faithful-pilot-a-readiness.ts`
- `tests/old-faithful-pilot-a-readiness.test.ts`
- `tests/fixtures/old-faithful-pilot-a/official-metadata-verification.json`

Er is geen callback-, reducer-, runtime-, reader-, downloader- of writerintegratie toegevoegd.

## 2. Officiële epoch-978 bronfreeze

De readinesscontrole raadpleegde uitsluitend kleine officiële sidecars en één CAR `HEAD`-request onder een totale bodycap van 50 MiB.

- Werkelijk sidecarbodyverkeer: `4.315.301` bytes.
- CAR request: `HEAD`.
- CAR-payloadbytes: `0`.
- Epoch: `978`.
- Canonieke halfopen epochrange: `[422496000, 422928000)`.
- CID: `bafyreidv5xfqynnope3ul23a7qhcl4csf52ts2wcxdeoi5gqlbkwsn3k4m`.
- CAR-size: `709264399796` bytes.
- CAR-SHA-256: `4ceef2830882036491f2f5a0fbc9ba5ccb94e7783a6723c42336969a50cb212c`.
- Inventory-size: `4313880` bytes.
- Inventory-SHA-256: `1de4b8689cfe7362b6129e8123f53fe3863616d7bb7c08338d157bb6d4754e97`.
- Recap-SHA-256: `2666bac343424fa6d18b3a01e1512a6231eac6a1e6bdd40f8d0f5d9157d121db`.

Inventorysemantiek:

- totaal `431388` entries;
- uitsluitend extra grensslot `422495999` buiten de canonieke range;
- `431387` entries binnen de canonieke range;
- `613` slots niet aanwezig binnen de 432.000-slotrange;
- filtering van het extra grensslot gebeurt vóór pilotrangeselectie;
- dit verschil wordt niet stilzwijgend als corrupt of compleet geïnterpreteerd.

Machineleesbare bronnen:

- source candidate: `docs/research/OLD_FAITHFUL_EPOCH_978_SOURCE_MANIFEST.json`;
- exacte auditfixture: `tests/fixtures/old-faithful-pilot-a/official-metadata-verification.json`.

## 3. Kandidaatpilotrange

Gepind bereik:

- epoch `978`;
- `[422506000, 422507000)`;
- exact `1.000` slots;
- alle 1.000 exact en opeenvolgend aanwezig in de gepinde inventory;
- volledig binnen de canonieke epochrange;
- extra grensslot `422495999` wordt niet gebruikt.

Selectiegrond:

`SOURCE_AND_ENGINEERING_PROPERTIES_ONLY`

De range is niet gekozen op winst, tokenperformance, eventdichtheid of bekende winnaars.

Een eventuele vervolgrange volgt uitsluitend:

`FIRST_ASCENDING_SUBSEQUENT_1000_SLOT_HALF_OPEN_WINDOW_WITH_COMPLETE_PINNED_INVENTORY_WITHOUT_EVENT_OR_OUTCOME_INSPECTION`

Eventafwezigheid mag de range niet achteraf wijzigen.

## 4. Transportcoverage versus eventsemantiek

De package houdt twee statussen strikt apart:

- `TRANSPORT_COVERAGE_PASS`: alle 1.000 inventoryslots sluiten zonder slot-, callback-, retry- of identityconflict.
- `EVENT_SEMANTIC_COVERAGE_PASS`: eventcategorieën zijn afzonderlijk geteld en iedere geaccepteerde decode heeft bewezen slotactivatie.

Transportcoverage kan slagen wanneer een eventcategorie nul keer voorkomt.

Een latere run rapporteert afzonderlijk:

- succesvolle creates;
- failed create-attempts;
- buys;
- sells;
- failed Pump-transacties;
- unknown discriminators;
- top-level Pump-instructies;
- inner CPI-Pump-instructies;
- legacy transacties;
- v0-transacties;
- completion-events;
- migrations.

## 5. Slot-effectieve registry

Registryclassificaties:

- `PROVEN_AT_SLOT_RANGE`
- `STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`
- `UNKNOWN`

Alle kandidaatentries zijn momenteel:

`STRUCTURALLY_SUPPORTED_UNPROVEN_ACTIVATION`

Gepinde structurele bron:

- Pump-programma: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`;
- officiële repository: `https://github.com/pump-fun/pump-public-docs`;
- docscommit: `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`;
- IDL-pad: `idl/pump.json`;
- IDL-SHA-256: `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.

De officiële IDL-bytes bevestigen de in de planregistry opgenomen instruction- en eventdiscriminators/layouts. Een Git/IDL-commit bewijst echter geen activatieslot. Binnen de toegestane primaire bronnen is geen voldoende on-chain deployment-/upgradebewijs vastgelegd dat de kandidaatlayouts over `[422506000, 422507000)` bewijst.

Daarom:

- geen `PROVEN_AT_SLOT_RANGE` entry;
- geen accepted Silver-decode;
- structurally supported/unknown evidence blijft raw Bronze plus quarantine;
- packageverdict blijft **HOLD_UNPROVEN_ACTIVATION**.

Er wordt geen activatieslot verzonnen of geëxtrapoleerd.

## 6. Source-, adapter-, reducer- en toolchainfreeze

Gepind:

- repository-main: `16fa8fac09b3a96f1210503152f10b6ff6af5e4a`;
- Jetstreamer `v0.7.0`: `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`;
- upstream `firehose.rs` SHA-256: `572ec56122e898f2318adc34d5998296b4fa0d31cf4bcab40950f5615a526a00`;
- Node `22.23.2`, ABI `127`;
- Rust `1.97.1`, compilercommit `8bab26f4f68e0e26f0bb7960be334d5b520ea452`;
- target `x86_64-unknown-linux-gnu`;
- exacte `package-lock.json`, `Cargo.lock`, `tsconfig.json`, Cargo-manifest-, adapter-, Bronze-, event-, Silver- en reducersourcehashes in het plan.

De bestaande lokale Jetstreamer-workspace met pluginwijzigingen/untracked bron blijft:

`INSUFFICIENT_PROVENANCE_NOT_ALLOWED`

Hij mag geen pilotbinary of source-of-truth leveren.

Een toekomstige binary mag uitsluitend uit een schone pinned checkout, Node `npm ci`, Rust `--locked`, exacte compiler-/targetidentiteit en exact gereviewde sourcebytes worden gebouwd. Deze fase bouwt of start geen pilotbinary.

## 7. Outputcontract

Een latere afzonderlijk goedgekeurde Pilot A moet produceren:

- immutable per-slot Bronze;
- immutable per-slot Silver event-only output;
- raw unknown/quarantine output;
- coverage ledger;
- retry-/duplicate-/conflictledger;
- WAL;
- checkpoints;
- aggregate outputmanifest;
- source-, config- en schemahashes;
- per-slot contenthashes;
- aggregate contenthash;
- deterministic rerunhash;
- wirebytes, ranges en retries;
- gesloten bounded reasoncodes.

Alle subcontracts, de registry, source manifest, observability, frontend en packagecore hebben domain-separated SHA-256-bindings in `OLD_FAITHFUL_PILOT_A_PLAN.json`.

Accountstatebeleid:

- raw historical accountstate: `UNAVAILABLE_OF1`;
- instruction-exact state: `UNAVAILABLE_OF1`;
- autoritatieve curve-/mintstate: `UNAVAILABLE_OF1`;
- geen nulvulling;
- geen eventreservebackfill;
- geen transaction-wide balancebackfill;
- geen huidige accountstate terugschrijven naar geschiedenis.

## 8. Bandwidth-cap-preflightplan — niet uitgevoerd

Ontwerpwaarden:

- verbinding: `1.000.000.000 bit/s`;
- hard cap: `799.000.000 bit/s`, strikt lager dan 800 Mbit/s;
- application target: `85 MiB/s`;
- absolute application max: `90 MiB/s`;
- wire-ratevensters: 1s, 10s, 60s;
- limiterheartbeat en stale-metric abort;
- burstcontrole;
- één sequentiële stream;
- nul parallelle rangeworkers.

Het plan beschrijft router/WAN-shaping, TrueNAS IFB-ingressshaping, interface-identificatie, backup, rollback, crash-safe cleanup en de handmatige huishoudelijke controle. Geen enkele netwerkconfig is gewijzigd.

`JETSTREAMER_NETWORK_CAPACITY_MB`, threadcount of payloadmeting geldt niet als limiterbewijs.

## 9. Hard kandidaatbudget

- requested slots: `1.000`;
- max wire: `8 GiB`;
- max verified range cache: `8 GiB`;
- max Bronze/Silver output: `10 GiB`;
- max WAL/checkpoints/manifests: `2 GiB`;
- minimum vrij vóór start: `40 GiB`;
- max runtime: `60 minuten`;
- cgroup memory hard limit: `16 GiB`;
- abort bij langdurig RSS boven `12 GiB`;
- één reader;
- één writer;
- geïsoleerde immutable outputmap.

Het plan bevat alle verplichte abortcondities, inclusief bandwidth, limiter, stale metrics, source drift, identity/inventory/coverageconflict, budget, WAL/checkpoint, disk, rerunmismatch, onverwachte netwerkcapability en iedere approvalescalatie.

## 10. Observability side-channel

Er is geen Prometheus-, Grafana- of Loki-infrastructuur toegevoegd.

Het plan definieert uitsluitend:

1. download/network safety;
2. pipeline health;
3. coverage/data quality;
4. latere aggregate ClickHouse-research;
5. strategievalidatie uitsluitend na goedgekeurde Gold-data.

Toegestane labels:

- `stage`
- `result`
- `quarantine_reason`
- `source`
- `schema_version`
- `run_mode`

Ieder toegestaan label heeft in het machineplan tevens een gesloten waardedomein. `quarantine_reason` is exact gelijk aan de gesloten bounded-reasonlijst; `stage`, `result`, `source`, `schema_version` en `run_mode` accepteren uitsluitend de gepinde lage-cardinaliteitswaarden. Een ontbrekend, leeg, uitgebreid of vervangen domein faalt gesloten.

Verboden labels omvatten mint, signature, wallet, pubkey, slot, transaction/event/run ID en vrije errortekst.

Metrics beïnvloeden nooit datasetbytes, ordering, hashes, acceptance of quarantine.

## 11. Frontendcontract

Toekomstige querycontracten:

- dataset/run selector;
- provenance/data-quality;
- lifecycle explorer;
- trade tape;
- event/instruction inspector;
- quant feature explorer;
- coverage/quarantine explorer.

Evidencebadges:

- `OBSERVED`
- `DERIVED`
- `INFERRED`
- `UNKNOWN`
- `QUARANTINED`
- `SYNTHETIC`
- `REAL_UNAPPROVED`
- `RESEARCH_READY`

Iedere toekomstige frontendrow moet exact `datasetId`, `runId`, `asOfSlot`, `asOfTime`, `provenanceId`, `approvalStatus`, `evidenceBadge`, `completeness` en `uncertainty` dragen. De zeven API-routenamen en templates zijn exact gepind; arbitraire of lege vervangingen falen gesloten.

Pilot A mag liquidity, holderstate, raw accountstate, executable fills of profitability niet als bewezen tonen.

## 12. Readiness evaluator en adversarial boundary

`evaluateOldFaithfulPilotAReadiness()`:

- neemt uitsluitend het machineleesbare source manifest en plan;
- valideert exact bronwaarden, range, registry, freeze, outputs, caps, budgets, aborts, side-channel en frontend;
- verwerpt self-approval en iedere uitvoeringstoestemming;
- verifieert domain-separated hashes;
- vergelijkt de berekende packagecore bovendien met een trusted SHA-256 die in de evaluatorcode is gepind, zodat caller-controlled rebinds geen onbekend planveld kunnen verzwakken;
- retourneert uitsluitend `QUARANTINED` of `HOLD_UNPROVEN_ACTIVATION`;
- heeft geen reader/downloader/streamer/callback/writer/runtimecaller.

## 13. Resterende HOLD/NO-GO

- Activatiebewijs epoch 978: **HOLD**.
- Bandwidth-cap-preflight: **HOLD**, aparte expliciete toestemming nodig.
- Pilot A-uitvoering: **HOLD**, aparte expliciete GO nodig.
- Pilot B: **NO-GO**.
- Volledige epoch: **NO-GO**.
- Drie-maandenrun: **NO-GO**.
- OOS, execution en profitability: **NO-GO**.

Phase 7A read-only activation research is complete. PR #16 merged the persisted evidence and offline deterministic citationgate as `784192a675e31d78da82852e91ceb254eccae982`; post-merge run `32397224604` executed that separate step successfully. Historical ELF/source mapping and raw candidate semantics remain unproven; 0/10 entries meet `PROVEN_AT_SLOT_RANGE`. Bandwidth preflight and Pilot A remain behind separate future explicit authorization.

## 14. Lokale gate-evidence vóór staged-byte review

Onder exact Node `22.23.2` en Rust `1.97.1`:

- focused Pilot-A-readiness: `86/86`;
- volledige Node-suite: `1.197/1.197` over 72 testbestanden;
- volledige Rust-suite: `68/68`;
- TypeScript: schoon;
- backend/frontend-build: groen, met alleen de bestaande niet-blokkerende Vite chunk-waarschuwing;
- Rust fmt, clippy `-D warnings`, locked tests en locked build: groen;
- repository policy, compiled research transport isolation, `git diff --check`, citationgate en tracked-secretsscan: groen.
- Phase-7B post-merge main-CI run `32397224604`: groen op `784192a675e31d78da82852e91ceb254eccae982`; citationgate 33/33 and policy 21/21, met de afzonderlijke offline citationstep actief en succesvol.

Deze gate-evidence is readiness-package-evidence, geen pilot-, source-payload-, OOS- of profitabilitybewijs.

## Primaire bronnen

- https://docs.old-faithful.net/references/of1-files
- https://files.old-faithful.net/978/
- https://raw.githubusercontent.com/pump-fun/pump-public-docs/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json
- https://raw.githubusercontent.com/anza-xyz/jetstreamer/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/firehose.rs
