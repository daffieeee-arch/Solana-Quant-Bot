# B5 — transaction-grouped mint observation timeline

> **Document status: ACTIVE.** Bounded implementation under #127 / B5 #84.
> This is a post-hoc descriptive lifecycle fragment, not a complete lifecycle,
> independent sample, B4/B5 completion or Research Ready evidence.

## Fixed scope and admission boundary

The authorized case is mint `4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump`
over the existing `[422669516,422669535)` collection, SHA-256
`39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab`.
The three original `RESEARCH_SAMPLING` slots and sixteen
`POSTHOC_DESCRIPTIVE_CONTEXT` / `ENGINEERING_VALIDATION_ONLY` slots keep their
original identities. Selection of this mint was post-hoc because existing
facts showed both sides; that is descriptive engineering context only.

The query/report in `research/columnar-query/mint_timeline.py` reuses
`load_collection`, `attach_collection` and the locked DuckDB reader. It does
not read transaction wire bytes, derive PDAs, change admission or run Raw decoding.
The closed Mayhem source investigation #126 and its prepared unsent question
remain separate. All 22 collection facts and seven Mayhem rejections remain intact.

## Report contract

`mint-timeline.sql.json` selects the complete union of four evidence channels:

- existing Silver `mint`, joined on Bronze parent hash and collection source;
- an exact mint in Rust's typed token balance observations;
- an explicit `event_reported.mint_address` in existing profile diagnostics,
  or `structural_fields.mint_bytes_base58` in existing structural probes;
- exact equality in the already decoded message account-key list.

Only these explicit fields attribute a diagnostic mint. Expected account roles,
address correspondence, unrelated sibling events and transaction success cannot
fill an absent mint. Other package diagnoses remain visible as `UNKNOWN` or
`EXPLICIT_OTHER_MINT`. A reference or structural probe never becomes a trade.
The older source-diagnostic lane has no explicit event mint and remains unknown.

Compact boolean signals are materialized before the Silver existence join.
Otherwise DuckDB may carry large canonical record strings through the join;
the first real query reached the unchanged 256 MB cap. The corrected query
passes with the same one-thread, no-spill limit. No timeout or cap was increased.

Each source/parent binding has one card. Pre/post observations and evaluator
entries preserve their original list positions. Exact duplicate physical rows
of one bound record do not create additional cards/facts. Instruction counts
use declared top-level and recorded CPI positions, independent of diagnoses;
neither declaration nor metadata presence proves every instruction executed.
Different evaluators of one instruction stay separate diagnoses. Structural
probes, profile diagnostics and unique diagnosed positions have separate totals.

Cards retain chain order, source/receipt/manifest hashes, original signatures,
failures, integer strings, present decimals and existing Silver context.
Rust's existing signed balance deltas remain transaction-wide. No delta is
calculated for balance-only packages, allocated to an instruction, or summed
across facts. Event quantities, instruction bounds and balance metadata remain
separate. No inferred quote units, price, fill, return, account state or CPI rights.

All selected cards are shown. The existing 8 MiB report cap and a 10,000-package
guard fail rather than publish a truncated success. Empty slots/channels mean
no selected row, not proven zero activity. First/last observations do not prove
launch, migration or lifecycle end.

Canonical JSON binds SQL, parameters, source and code hashes and contains all
integers as decimal strings. Actual execution UTC, duration and RSS are separate
in `query-execution.json`. No acquisition/processing clock orders the timeline.

## Measured bounded result

The real manifest-bound query selected **15 unique packages**: six pilot and
nine context packages. Twelve succeeded and three failed on-chain. There are
**five unchanged mint facts: two buys / three sells**, two pilot and three context.
The **58 mint balance observations** comprise 24 pilot and the previously known
34 context observations in nine context packages. Every selected package in
this case has balance evidence; message-only/diagnosis-only selection is tested
with synthetic projected fixtures, not claimed as an authentic observation.

The selected packages contain **162 instruction references** (69 declared
top-level / 93 recorded CPI), **seven profile diagnoses** and **26 structural
probes**. Two profile diagnoses are `NOT_ADMITTED`. Ten diagnosis/probe entries
have an explicit matching mint; 23 do not establish mint attribution. These
are overlapping evidence counts, never additional transactions or trades.

The successful development query took 8.583 seconds with peak RSS 426,140 KiB
(whole Python process, distinct from DuckDB's 256 MB buffer cap). Final report
hashes, exact reviewed commit, targeted tests and PR/main checks are recorded
outside Git under `governance/b5-mint-timeline-20260921/`. The failed optional
`/usr/bin/time` invocation and initial query memory failure remain there too.

## Reproduce and view privately

Use the approved VPS wrapper, resource-limited stage and existing socket-denied
launcher; see [the development setup](../WSL_DEVELOPMENT_SETUP.md). No install,
provider request or service change is required. Run from the accepted checkout:

```bash
node scripts/with-toolchain.mjs -- \
  /home/chupa/Solana-project/data-old-faithful-one/migrations/vps-integration-20260920/launcher \
  /home/chupa/Solana-project/data-old-faithful-one/migrations/vps-integration-20260920/network-deny.bpf \
  python research/columnar-query/mint_timeline.py \
  /home/chupa/Solana-project/data-old-faithful-one/datasets/b5-pilot-context-20260914.QFk5Sc/collection-complete \
  /home/chupa/Solana-project/data-old-faithful-one/governance/NEW-MINT-TIMELINE-REPORT \
  --mint 4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump \
  --collection-sha256 39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab
```

The output directory must be new. Enclose the command in the existing Solana
CPU200%/MemoryHigh5G/MemoryMax6G/Tasks256 scope, nice10/ionice3 and a 900-second
hard timeout. The CLI also preserves stricter inherited limits and applies the
existing 2 GiB address-space bound. Generated files belong outside Git.

Copy `index.html`, `query-results.json` and `query-execution.json` over the
existing private SSH connection and open `index.html` locally. Alternatively,
the existing `research/columnar-query/serve.mjs` can serve that exact directory
on a separately selected loopback port with an SSH tunnel. This task does not
start a viewer or change the existing port-7040 service/public access.

## Validation

Focused Python tests run within the existing Parquet offline gate and cover
independent channels, join multiplication, multiple instructions/facts/diagnoses,
source-bound identity, source classes, failures/unknowns, exact large integers,
escaping, deterministic ordering and fail-closed limits. Real collection
parity and report inspection are separate evidence, not inferred from fixtures.
The implementation follows one independent exact-commit review and at most one
focused recheck, required PR checks, protected squash merge and main/roadmap
verification. Parent B4/B5 remain open and Unproven.
