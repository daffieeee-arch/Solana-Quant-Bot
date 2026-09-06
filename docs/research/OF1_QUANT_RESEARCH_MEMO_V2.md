# OF1 Quant Research Memo V2

> **Document status: ACTIVE.** First-run research memo from the project OF1
> quant-research agent. This is not an acquisition authorization, not a
> `RESEARCH_READY` claim, and not proof of a tradable edge.
>
> Initial documentation review: **2026-09-05**; active lease/evidence
> correction and source-path recheck: **2026-09-06**.
> Docs-MCP servers (`old-faithful-docs`, `triton-docs`, `solana-mcp`) were
> unavailable during that run; claims below are traced to official
> documentation hosts as `DOCUMENTATION_ONLY`. Their content is not
> canonical dataset evidence.

## 1. Question

Can official Triton Old Faithful (`files.old-faithful.net`) later yield an
authentic, content-addressed transaction tape that a later Python research
layer can use to test — or falsify — a Pump.fun microstructure edge, without
violating the Triton-only rule, point-in-time integrity, or execution
semantics?

**Answer now:** the pinned format is a plausible route, not proof that the
selected window can yield the required tape. The [Rust integration](OF1_OFFLINE_TRANSPORT.md)
proves loopback fixtures only. Official HTTPS, index acquisition and CAR/CID
verification remain unimplemented. The [corrected lease contract](B4_ENGINEERING_VALIDATION_LEASE_PLAN.md)
defines separate metadata and payload approvals and the shortest bounded path.
No authentic data, tradable edge or acquisition authority follows from this memo.

## 2. Option map

Only these OF1-shaped paths are in scope. Forbidden substitutes (public
Solana RPC, Helius, QuickNode, Alchemy, Birdeye, DexScreener, GeckoTerminal,
public Jupiter, or any secondary provider fallback) are out.

| Option | What it is | V2 status | Official docs |
| --- | --- | --- | --- |
| Direct official OF1 CARs | Epoch objects at `https://files.old-faithful.net/{EPOCH}/`; documented sidecars are `epoch-{EPOCH}.sha256`, `epoch-{EPOCH}.cid`, `{EPOCH}.slots.txt`, `{EPOCH}.recap.yaml` | Exact inventory and live existence remain unverified. Only separately approved metadata and payload leases permit requests; no full epoch CAR. | [OF1 files](https://docs.old-faithful.net/references/of1-files.md); modern slot-range format: [pinned index source](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/index.rs) |
| Pinned Jetstreamer 0.7.x over official OF1 | Selected *initial* V2 acquisition route **iff** HTTP/S3/backend overrides remain default-deny | Repo pin is a **candidate**, not a reviewed live path. `JETSTREAMER_V0_7_0_GIT_SHA` = `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24` is recorded in `rust/old-faithful-pump-reducer/src/lib.rs` as a callback-type snapshot, not as a blessed live acquisition crate. KNOWN_ISSUES #6 remains open. | [docs.rs/jetstreamer](https://docs.rs/jetstreamer/latest/jetstreamer/) |
| Hosted Old Faithful gRPC / RPC | Prior public-documentation/MCP availability claims conflict | **Not assumed available and not selected.** Any future hosted endpoint requires explicit availability/cost confirmation from Triton; do not probe. | [old-faithful RPC](https://docs.old-faithful.net/old-faithful/rpc.md); Triton [old-faithful](https://docs.triton.one/chains/solana/old-faithful) |

**Not an option:** flipping fixture `networkEnabled`, restoring the removed
JS recorder, importing Jetstreamer's broad runtime/alternate backends, or using
the frozen V1 paper scanner as research truth. Source pins specify format facts;
they do not require importing the full upstream crate. The formerly cited
`references/of1-indexes.md` returned Page Not Found on recheck; use the pinned
source for the modern index, never a guessed legacy fallback.

## 3. Class A vs later-layer fields

Class A belongs on the later authentic Bronze/Silver tape. Later-layer
fields must stay out of that tape, or stay explicitly `UNAVAILABLE` /
`EVENT_FIELD`.

**Class A (candidate, after authentic decode agreement):**

- slot, parent, block time, blockhash
- transaction signature, index, success/error, fee, compute units when
  present
- instruction program id, accounts, data, stack height / CPI order
- log messages bound to that same transaction
- pre/post balances and token balances when present in the replayed
  transaction
- Pump `buy` + `TradeEvent` fields only after the B3 decoder agrees on
  authentic bytes (`STRUCTURAL_CANDIDATE` / `FIXTURE_COMPATIBLE` is not
  that agreement)

Pump event reserve / vSOL / vToken numbers remain `EVENT_FIELD`. They are
not independently observed pool state and not an executable quote.

**Not in OF1, therefore not Class A:**

- account-write / Geyser-parity state
- CLOB / DOM (Pump has none)
- USD notionals without a point-in-time FX source
- fill, queue position, or maker/taker evidence
- observation latency or `execution_opportunity_at`

A strategy may not react to one event in a transaction and trade against a
price or reserve from that same already-executed transaction.

## 4. First-slice recommendation (not a lease)

Do not download anything on the strength of this memo.

When a later approved plan exists, the first slice should be:

- host: `files.old-faithful.net` only
- purpose: `ENGINEERING_VALIDATION_ONLY` (never a strategy or edge claim)
- unit: one preregistered bounded slot window, **not** a full epoch CAR;
  the current unapproved draft is `[422496000, 422496128)` and has no proven
  Pump activity or budget feasibility
- hard stop: named request / byte / disk / runtime budget in the plan
- result dimensions: engineering outcome, data sufficiency, and research
  admissibility separately; no B4 `FALSIFIED` market-edge result

`[422506000, 422506128)` is still a **provisional example**. It sits in
epoch 978 by Old Faithful's documented `floor(slot / 432000)` rule
([epochs](https://docs.old-faithful.net/references/epochs.md)). It is not
an approved plan and must not be fetched because it appears in docs.

Epochs 0–156 are a poor first-slice choice: Old Faithful documents
incomplete or low-quality coverage there. Compute-unit fields being `0`
before epoch 450 means "field not populated", not "no compute was used".

## 5. Engineering failure is not edge falsification

| Observation | Correct interpretation |
|---|---|
| HTTP failure, invalid index, non-replayable SHA/CID, conflicting bytes | Engineering failure or quarantine with a named reason; no market conclusion |
| Budget/deadline reached | `ABORTED_BUDGET`; retain partial evidence, no expansion |
| Payload not decoded | `UNAVAILABLE_NOT_DECODED_IN_B4`, not zero Pump events |
| Valid later decode contains no required Pump pair | `INSUFFICIENT_DATA_FOR_PUMP_MECHANICS`; no claim that an edge is absent |
| Required atomic transaction package incomplete | Inadmissible observation; quarantine/gap or insufficient data according to evidence, never a strategy negative sample |
| Event price exists but executable opportunity/cost evidence is missing | Economics unproven; filling at that price would violate the contract, but observing the price is not itself a causality violation |
| A preregistered hypothesis fails its specified threshold on valid sufficient outcome-independent PIT evidence after appropriate costs and uncertainty | Later research may report `FALSIFIED`; this engineering slice is permanently excluded |

`UNAVAILABLE`, `GAP` and `QUARANTINED` stay distinct. An index zero record
means index-reported absence, not proven skipped-slot or chain-gap evidence.
Poor data does not falsify a market hypothesis. Even later research may return
`INSUFFICIENT_SAMPLE` rather than reject a hypothesis it cannot validly test.
A failed or empty first slice authorizes neither larger budgets nor outcome-hunting.

## 6. What this memo does not authorize

- No Triton / OF1 call.
- No credit spend.
- No `ACQUISITION_LEASED` plan.
- No change to B4A `networkEnabled`.
- No hosted gRPC probe.
- No Jetstreamer HTTP/S3/backend override.
- No claim that a Pump edge exists, is profitable, or is research-ready.

B4 / GitHub #83 remains `In Progress` / `Unproven`. Acquisition remains
unauthorized.

## 7. Next package

The [lease contract](B4_ENGINEERING_VALIDATION_LEASE_PLAN.md) now separates:
bounded implementation and local verification → metadata/index GO → measured
offline feasibility → separate payload GO → immutable Raw and exact integrity
report. Neither approval currently exists. A full epoch index is metadata,
not permission for an epoch CAR. Node CID agreement, selected slot-envelope
consistency and root-to-slot membership require distinct checks; a declared
root and local partial hash are not an inclusion proof.

Authentic engineering mechanics may later earn Engineering Validation only
after review. B4 stays open until its actual acceptance criteria pass; B5 owns
Bronze/Silver and the first token lifecycle. No new infrastructure phase is added.

## Citations

Official documentation/source hosts, initially accessed **2026-09-05**;
OF1 file names and the pinned modern index source rechecked **2026-09-06**,
`DOCUMENTATION_ONLY` (no acquisition-host request):

- https://docs.old-faithful.net/references/of1-files.md
- https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/index.rs
- https://docs.old-faithful.net/references/epochs.md
- https://docs.old-faithful.net/old-faithful/rpc.md
- https://docs.triton.one/chains/solana/old-faithful
- https://docs.rs/jetstreamer/latest/jetstreamer/

Repo evidence (not dataset proof):

- `rust/old-faithful-pump-reducer/src/lib.rs` (`JETSTREAMER_V0_7_0_GIT_SHA`)
- `docs/research/B4A_OFFLINE_RANGE_RECORDER.md`
- `docs/KNOWN_ISSUES.md` items 6, 7, 8, 25
- GitHub #83 (B4), Project #4
