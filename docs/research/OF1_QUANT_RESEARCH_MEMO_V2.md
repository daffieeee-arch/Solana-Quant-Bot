# OF1 Quant Research Memo V2

> **Document status: ACTIVE.** First-run research memo from the project OF1
> quant-research agent. This is not an acquisition authorization, not a
> `RESEARCH_READY` claim, and not proof of a tradable edge.
>
> Access date for official documentation cited here: **2026-09-05**.
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

**Answer now:** OF1 can later yield that tape. A winning edge is not
provable from current evidence. The next honest package is the offline B4
remainder, then a separately approved `ENGINEERING_VALIDATION_ONLY` lease.
See [`B4_OFFLINE_REMAINDER.md`](B4_OFFLINE_REMAINDER.md).

## 2. Option map

Only these OF1-shaped paths are in scope. Forbidden substitutes (public
Solana RPC, Helius, QuickNode, Alchemy, Birdeye, DexScreener, GeckoTerminal,
public Jupiter, or any secondary provider fallback) are out.

| Option | What it is | V2 status | Official docs |
| --- | --- | --- | --- |
| Direct official OF1 CARs | Epoch objects at `https://files.old-faithful.net/{EPOCH}/` with `epoch-{EPOCH}.car`, `.car.sha256`, `.car.cid`, `epoch-{EPOCH}-slots.txt`, recap, and indexes | Allowed host **only** under an explicit immutable `ACQUISITION_LEASED` run plan. First slice is not a full epoch. | [of1-files](https://docs.old-faithful.net/references/of1-files.md), [of1-indexes](https://docs.old-faithful.net/references/of1-indexes.md) |
| Pinned Jetstreamer 0.7.x over official OF1 | Selected *initial* V2 acquisition route **iff** HTTP/S3/backend overrides remain default-deny | Repo pin is a **candidate**, not a reviewed live path. `JETSTREAMER_V0_7_0_GIT_SHA` = `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24` is recorded in `rust/old-faithful-pump-reducer/src/lib.rs` as a callback-type snapshot, not as a blessed live acquisition crate. KNOWN_ISSUES #6 remains open. | [docs.rs/jetstreamer](https://docs.rs/jetstreamer/latest/jetstreamer/) |
| Hosted Old Faithful gRPC / RPC | `docs.old-faithful.net` still describes RPC/gRPC; Triton docs currently say Old Faithful gRPC is retired | **Not selected.** Do not probe availability or spend credits. | [old-faithful RPC](https://docs.old-faithful.net/old-faithful/rpc.md); contrast Triton [old-faithful](https://docs.triton.one/chains/solana/old-faithful) |

**Not an option:** flipping B4A `networkEnabled` to `true`, adding an HTTP
client to `scripts/b4a-offline-range-recorder.mjs`, or treating the frozen
V1 paper scanner as research truth.

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
- unit: the smallest closed slot window that still contains at least one
  authentic Pump `buy` + `TradeEvent`, **not** a full epoch CAR
- hard stop: named request / byte / disk / runtime budget in the plan
- expected outcome: `PASS`, `FALSIFIED`, or `INSUFFICIENT_SAMPLE`

`[422506000, 422506128)` is still a **provisional example**. It sits in
epoch 978 by Old Faithful's documented `floor(slot / 432000)` rule
([epochs](https://docs.old-faithful.net/references/epochs.md)). It is not
an approved plan and must not be fetched because it appears in docs.

Epochs 0–156 are a poor first-slice choice: Old Faithful documents
incomplete or low-quality coverage there. Compute-unit fields being `0`
before epoch 450 means "field not populated", not "no compute was used".

## 5. Falsifiers

The later research question is falsified, or at best
`INSUFFICIENT_SAMPLE`, if any of these hold:

1. The leased window has no authentic Pump `buy` + `TradeEvent` after
   decoder agreement.
2. Required instruction, CPI, log, or balance pieces from the same
   transaction are missing, so the atomic observation package cannot be
   closed.
3. Jetstreamer or the CAR reader cannot reconstruct a slot-contiguous
   window without silent holes. `UNAVAILABLE`, `GAP`, and `QUARANTINED`
   stay distinct; missing is never zero.
4. The only "price" is an event field or a same-transaction reserve, and
   no later independent `execution_opportunity_at` exists. That is not an
   edge. It is a causality violation.
5. Coverage, CID, or SHA-256 identity does not replay.

A failed first slice does not authorize a larger download.

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

Implement the offline B4 remainder specified in
[`B4_OFFLINE_REMAINDER.md`](B4_OFFLINE_REMAINDER.md). That package is
**not** live B4B. Live leased acquisition stays reserved for a later
approved plan after this remainder exists.

## Citations

Official documentation hosts, accessed **2026-09-05**,
`DOCUMENTATION_ONLY`:

- https://docs.old-faithful.net/references/of1-files.md
- https://docs.old-faithful.net/references/of1-indexes.md
- https://docs.old-faithful.net/references/epochs.md
- https://docs.old-faithful.net/old-faithful/rpc.md
- https://docs.triton.one/chains/solana/old-faithful
- https://docs.rs/jetstreamer/latest/jetstreamer/

Repo evidence (not dataset proof):

- `rust/old-faithful-pump-reducer/src/lib.rs` (`JETSTREAMER_V0_7_0_GIT_SHA`)
- `docs/research/B4A_OFFLINE_RANGE_RECORDER.md`
- `docs/KNOWN_ISSUES.md` items 6, 7, 8, 25
- GitHub #83 (B4), Project #4
