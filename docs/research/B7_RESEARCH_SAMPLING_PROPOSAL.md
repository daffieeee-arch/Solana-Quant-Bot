# B7 — first research and sampling decision proposal

> **Document status: ACTIVE — PROPOSED, NOT EXECUTABLE.** 2026-09-24.
> Partial #86 preparation only. No provider request, approval, acquisition or
> B7 sufficiency conclusion. #86 stays open / Unproven; Research Ready is false.

The preceding status records the proposal at publication. The owner subsequently
accepted this design and authorized native preparation plus a conditional first
DEVELOPMENT window; see the separate [dated campaign decision](B7_NATIVE_CAMPAIGN.md).
The original proposal JSON and private sealed report remain unchanged.

## One question and its limits

In what fraction of eligible **16-slot windows** does at least one mint have an
admitted buy in the first eight slots and an admitted sell in the next eight?
The estimand is recurrence in the **frozen, Rust-admitted event tape**, not all
Pump trading, a holder's roundtrip, executable exitability or profitability.
Mint equality is protocol-reported identity, not economic/account ownership.
One window is one observation, irrespective of its slot, package or mint counts.
All facts in a transaction remain atomic; first-half and second-half packages
cannot be the same transaction. Sixteen slots is an initial short chain horizon,
not seconds or a complete token lifetime. It deliberately addresses whether the
available trade tape can support a minimal recurrence baseline before harder
unit-, execution- or lifecycle-dependent questions.

A possible B8 baseline is the earlier-cohort empirical recurrence frequency,
predicting the later cohort without tuning. A registered Brier-score comparison
to always-no-recurrence could falsify that **proxy prediction**. This proposal
implements neither labels nor that baseline and cannot falsify a market edge.
No price, return, quote currency, latency or fill assumption is needed for the
binary event question. Their absence still blocks economic interpretations.

Required inputs: immutable source/receipt/manifest identity; full window slot
inventory; Bronze package identity, chain slot/transaction/instruction order and
success; Silver fact/parent hashes, mint and buy/sell kind; complete decoder
outcomes and quarantine inventory. Amounts remain original raw integer strings
with explicit available decimals, but are not needed for this binary label.
Unknown quote mint/decimals remain UNKNOWN/null. `effective_at` is chain order.
Historical `observed_at`, `actionable_at`, `decision_at`,
`execution_opportunity_at` and latency remain UNAVAILABLE here. B8 must later
register an atomic observation model; retrospective slot ordering does not
prove historic real-time information availability. Acquisition, processing and
report clocks are operational only, never features, labels or split inputs.

## Present data and actual gaps

The private report binds the existing collection and executed query outputs:

- `[422669516,422669519)`: original RESEARCH_SAMPLING, **one** sampled window,
  3,224 packages / 223 failures / seven facts (four buys, three sells). All seven
  have bound token decimals=6. This window is only three slots long and contains
  no mint with both admitted sides; it cannot supply this 16-slot target.
- `[422669519,422669535)`: sixteen engineering/context slots; never an extension
  of the original independent sample. Combined collection: 21,719 packages /
  1,898 failures / 22 facts. These data may develop/check software, never estimate
  the new research target or be promoted into untouched evaluation.
- Selected mint dossier: post-hoc 15 packages / five facts / 58 balances /
  three failed packages. It informs feasibility/known limits only.
- Earlier authentic engineering captures cover `[422496000,422496005)`.
  Retained receipts are inventoried; no other authentic range in the local run
  inventory is silently admitted. Historical provisional examples are not data.

All these outcomes have been inspected. Their immutable classes remain intact,
but their use is now explicitly development/exploration, never untouched holdout.
Collection completeness is not complete Pump coverage. Seven Mayhem rejections,
unsupported 24-/26-byte buys, missing CPI privileges and historical activation
remain unchanged. More bytes cannot repair these semantics. The selected narrow
question counts only admitted reported events; it cannot establish market-wide
recurrence or execution. MFE/MAE prices, graduation/survival and actor ownership
remain separately blocked by units, lifecycle or reconstruction evidence. This
increment does not complete #86's Cohort Explorer or those evidence-gated views.

## Frozen selection and expansion

[`b7-proposal.json`](../../research/columnar-query/b7-proposal.json) is the proposed
versioned configuration. `b7_sampling.py select` generates canonical JSON before
any index byte sizes or new payload outcomes are read.

- Population: non-overlapping, 16-slot grid cells anchored at 422496000 within
  OF1 epoch 978 `[422496000,422928000)`. This is one epoch, not all market regimes.
  The epoch boundary is the retained official OF1 format convention, not a new
  UTC catalogue or independent chain-schedule claim.
- Earlier DEVELOPMENT stratum `[422496000,422711984)`, later RESERVED_EVALUATION
  stratum `[422712016,422928000)`: an explicit 32-slot separation/embargo.
- Exclude any whole cell overlapping the inspected ranges above. Eligible cells:
  13,496 earlier / 13,499 later. No Pump presence, byte size, index presence,
  successful decode or favorable outcome participates in the draw.
- Rank each stratum by SHA-256 of UTF-8 `B7_WINDOW_1\0` + UTF-8 seed
  `solana-quant-b7-86-recurrence-v1-20260924` + u64LE(epoch) + u64LE(start_slot).
  Break ties by ascending start slot. Take eight cells per stratum. All sixteen
  exact windows, ranks and source-derived byte ranges are in the private report.
- Stage 1: ranks 1–4 from each stratum. Stage 2: ranks 5–8. Within each stage,
  DEVELOPMENT precedes RESERVED_EVALUATION, then rank order. This is acquisition
  scheduling, never chain order. Each dataset preserves chain order internally.
- Stage 2 depends on stage-one integrity/resource feasibility and a new explicit
  execution decision, **not** pair counts or statistical significance. Missing,
  expensive, failed or unsupported windows are never replaced. Engineering/cap
  failure stops for a decision; it does not unlock another seed/window.
- Absolute end: 16 windows / 256 selected slots. No follow-up draw in this task.
  The first/last eight slots are the feature/label context; no outside halo is
  fetched. Lifecycle ages are left-censored; later sells outside the horizon
  are right-censored for lifecycle questions and are not chased.
- Same-mint recurrence across windows is possible and disclosed. The unit is a
  window, not independent mints. Cohorts are chronological, not entity holdouts.
  Do not claim independence merely because the windows do not overlap.
- Evaluation outcomes remain uninspected until a later frozen B8 methodology.
  Acquisition/integrity metadata may be checked, but no outcome-bearing quality
  screenshots or summaries from that reserve are consumed for feature tuning.
  If accidental inspection occurs, register contamination; never silently call
  that data untouched or replace it. Acquisition approval alone is not B8 GO.

Sixteen windows are a **bounded feasibility allocation**, chosen to exercise
multiple earlier/later windows and one fixed expansion without a large campaign.
No minimum number of coins or statistical-power guarantee is claimed. The
256-slot ceiling matches the existing collection bound; process batches remain
one slot, not a 256-slot allocation. Group into separate per-window collections
under the unchanged four-GiB artifact guard.

## Missingness and sufficiency rule (proposed, not a verdict)

Publish an inventory row for **every assigned window**, including not attempted.
For the pair indicator: 1 requires two admitted successful source-bound facts
for the same mint in the two different halves; no state transition from failures.
0 requires complete verified window processing and no matched pair. Conservatively
leave a negative label UNAVAILABLE if missing Bronze packages, unsupported or
quarantined successful Pump instructions could hide a pair; do not drop them.
A verified empty decoded slot contributes its proven absence of packages; an
index-reported absence alone is a GAP, not an empty-chain assertion. Failed
transactions remain evidence and denominators but produce no successful trade.
Uninterpretable retained bytes are QUARANTINED; missing semantic evidence is
UNAVAILABLE; absent expected source coverage is GAP. Report each separately.
Duplicate diagnoses are not extra instructions or negative observations.

At the **single final look**, report, per stratum, assigned n=8, confirmed
positives k, unknown/unacquired m, and the identification interval
`[k/n, (k+m)/n]`. Use a conservative finite-population sampling bound by enlarging
both ends by `sqrt(log(40)/(2*n))` and clipping to [0,1] (95% Hoeffding bound
under the declared hash-rank-as-random-sample design model). Deterministic hashes
are not a proof of randomization; report that modeling assumption, fixed seed,
finite population and single-epoch restriction. No IID-mint/binomial claim.
No optional stopping or post-hoc horizon/threshold change is permitted.

The proposed usefulness target is an interval of total width **at most 0.50**
for the later stratum and valid source/sample/admission bindings for every
known label. This is intentionally coarse (±25 percentage points at best),
not a trading effect threshold. At n=8 the radius is about 0.480: many plausible
results will be INSUFFICIENT_SAMPLE. At the hard end, wider uncertainty, missing
required fields, contaminated evaluation or identity failure means insufficient
for that bounded B8 estimate; no automatic further acquisition. A narrow interval
alone does not satisfy full B7, B8 PIT or Research Ready gates. Inspecting later
labels for this final verdict must be deferred until B8 methodology is frozen;
today's package has no final sufficiency verdict. Engineering failure, insufficient
sample and a later valid proxy-hypothesis falsification are distinct outcomes.

## Operational proposal and present NO-GO

The new read-only `of1-window-ranges` adapter calls the **existing**
`read_run_context` and `derive_payload_from_metadata`. It neither changes old
plans nor parses an index itself. Source: retained authentic metadata-only run
`of1-e978-metadata-29d04959-01`, index SHA-256
`649754195dd6182846a9754ffd7fa26a487e0b65bea66794e7d8bf180321a59b`.
Official format: [Jetstreamer source](https://github.com/anza-xyz/jetstreamer/blob/cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24/jetstreamer-firehose/src/index.rs),
commit `cffaf3d891b3cbe45a46dd963d6d3571b2aa1a24`, retained source SHA-256
`c963bc80e8daeb94b75aac802bd96b1a6cf78defbb29e0fac431b8c150c9f83b`.
Original source consultation: 2026-09-05/06; local binding rechecked 2026-09-24.
No online source/availability/cost check in this offline task.

Only `files.old-faithful.net:443` is proposed. Per-window **new** run:
GET `/978/epoch-978-slot-ranges.raw`, GET `/978/epoch-978.sha256`,
GET `/978/epoch-978.cid`, HEAD `/978/epoch-978.car`; then, after a separate
receipt-bound payload GO, the sixteen exact CAR ranges. No redirects, mirrors,
RPC, proof-node request, full CAR or fallback. Current metadata gives exact
planning bytes; it is not fresh acquisition authority or a future stability guarantee.
Retained CAR length 709,264,399,796 and declared hash/CID remain declarations,
not locally verified whole-epoch/root-membership evidence.

Full proposal: **425,940,234 unique payload entity-bytes**, 256 CAR operations;
metadata max 192 attempts / 249,225,216 reserved bytes, payload max 768 attempts /
1,277,820,702 reserved bytes. Combined **960 attempts / 1,527,045,918 reserved
entity-bytes**. These are upper retry reservations, not physical network traffic.
At most two retries per operation, no automatic retry loop; reservation remains
charged after failure. The independent campaign ceiling is 2 GiB reserved entity
bytes. Per run: 60 attempts, original 128-MiB entity cap, 16-MiB response,
256-MiB Raw-tree cap, 512-MiB acquisition RAM; 600s metadata + 1200s payload,
30s/attempt. Maximum allocated acquisition time is 28,800s (8h) across 16 runs;
this is a ceiling, not a performance estimate. Stage expiry/boot-clock identity
survive resume. A future campaign ledger must charge all runs; per-run limits
alone are not proof that a cross-run campaign cap is enforced.

Offline processing: one slot/worker, max 3 slots /16 MiB Raw per worker,
48 MiB serialized records/slot and 64 MiB/worker, existing node/link/frame and
individual-record caps; max 5,000 rows /64 MiB per Parquet file. Current collection
limits 256 slots /32 sources; 2-GiB process /4-GiB artifacts per collection,
900s per collection. Maximum 16 collections /14,400s (4h). Run under the existing
CPUQuota=200%, MemoryHigh=5GiB, MemoryMax=6GiB, TasksMax=256, concurrency one,
nice/ionice controls. Requested IOWeight remains known ineffective; no controller
change. Shared source entity-rate cap 87,500,000 B/s and 65,536-byte burst remain.

Campaign new disk hard cap: **32 GiB**, including Raw, failures, intermediate
records, Parquet and reports; free-space floor **20 GiB** in addition to the
next operation's conservative reservation. Stop own Solana work under host
memory pressure (<2 GiB available; require >3 GiB before starting), cap,
deadline, source/hash/CID drift, missing receipt or invalid link. No guarantee
all decoder outputs fit: retained reference expansion and actual free space
are in the private report. Never truncate or replace an oversized window.
The existing 4-GiB collection guard is not itself this future campaign ledger.

**Two explicit pre-execution blockers:**
1. `sample.rs::SampleIdentity::validate` admits only the original fixed pilot.
   New windows need one minimal reviewed native sample-identity lane binding
   this selection hash/rank/role through AggregatePlan → receipt → manifest;
   also bind/charge the bounded campaign ledger. No permissive CLI relabeling,
   alternate decoder, old plan edit or expired approval is acceptable. This
   task deliberately does not implement that acquisition capability.
2. Current no-credit-spend/cost terms are NOT_CONFIRMED. Earlier approved free
   public access is historical evidence, not today's billing confirmation.
   Owner must confirm zero paid spend or supply authoritative current terms;
   no provider/billing probe is made here.

Therefore the immediate decision is whether to approve this **design and its
bounded offline acquisition-admission preparation**, not to dispatch CARs.
After those blockers, prepare a new executable-bound metadata-only approval
for the first window (12 attempts /15,576,576 bytes /600s). Stop after four
verified metadata publications; require another payload decision using those
fresh receipts and exact unchanged selection. All approval/network/execution
flags remain false now. No old authority is reused.

## Reproduction and evidence

Private dossier: `/home/chupa/Solana-project/data-old-faithful-one/governance/b7-research-sampling-proposal-20260924`.
It contains `selection.json`, `ranges.json`, source inventory, HTML/JSON decision
report, execution/code bindings, tests and independent review. Inputs and source
pins are retained unchanged. `REPRODUCE.md` supplies network-denied commands
using the existing project-local tools and fresh output paths. Copy `index.html`
and `report.json` via existing SSH/SFTP and open locally; no service is needed.
