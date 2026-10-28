# Review corrections — 2026-10-25

The owner asked Codex to implement the outstanding review fixes while Claude Code is unavailable. This task-specific authorization covers these lab changes; it does not change the project roles or authorize deployment, provider requests, or new research runs.

## Corrections and acceptance

| Problem | Correction | Evidence required |
|---|---|---|
| A failed entry triggered before midnight could affect the wrong UTC loss-limit day and cash before its actual attempt. | Carry `t+d` separately from the trigger; settle failed fees chronologically at the attempt. Keep trigger-cohort reporting separate. | Synthetic Parquet through the real loaders, delayed fees, midnight boundaries and racing d=0/d=1. |
| Paper write-offs still used cost rule 1, which exempted one failed exit attempt. | New Python engine exits use rule 2 and record it per row. Old NULL-version rows retain their historical journal rule (default 1); restarting never reprices old exits. | Legacy-schema restart, rule-2 write-off, second restart without duplicate booking, mixed-rule UI/report costs. |
| `written_off_later_sold` could excuse unrelated P&L differences. | Check each side's complete settlement and its recorded version against the common cost scenario; a fill needs the matching validated parent exit. | Corrupt P&L/proceeds/costs/fees and unsupported versions remain RED; a real model difference is explained. |
| E75's relative peak tolerance could accept deterioration from a negative peak. | Only finite, strictly positive peaks qualify; missing/nonfinite peaks fail. | Positive tolerance boundary, zero, negative, missing and nonfinite Parquet cases. |
| E73's bootstrap omitted dates with no signals. | Use the frozen 13-date development calendar (2026-09-19 through 2026-10-01), including zero-trade days. Reject dates outside that contract. | Sparse and empty inputs, full denominator and out-of-calendar rejection. |

The original settlement functions and rule-1 calculations remain available for frozen historical experiments. E70's original source pins are retained; the precisely hashed engine-default change is documented separately in its existing drift allowlist. The frozen H5 reproduction still refuses changed analysis code rather than silently attributing a new result to an old implementation.

## Python/Rust compatibility

The shared journal schema gains a nullable `exits.settle_rule`. The Rust writer already supports nullable added columns. Its settlement implementation remains rule 1 in this task; NULL rows therefore continue to mean that historical version. The C1/C2/C3/M3 Python generators and the Rust-versus-Python replay harness explicitly use the historical rule. The frozen golden JSON stays byte-for-byte unchanged; only its test projection omits the new version metadata. This is not evidence that the Rust engine has migrated to rule 2. New Python-rule-2 and Rust-rule-1 journals must not be declared equivalent by suppressing cost differences.

## Validation and resource limits

Targeted Python checks run with the existing lab Python, `PYTHONDONTWRITEBYTECODE=1`, no pytest cache, and a separate scope capped at 3 GiB / 200% CPU under `heavy.lock`. Logs and temporary output are outside Git at `lab/review-fixes-20261025/` under the OF1 root.

The first run passed 85 tests and found three mistakes in new tests, corrected before review. An expanded run was stopped when an existing integration test selected the authentic development store and reached the local memory cap. That incomplete run is retained as `targeted-2.log` and is not a successful gate. The replacement local run explicitly excludes the two real-store replay tests; it tests synthetic data only. NAS-only replay tests keep their existing skips.

Rust builds and large parity replays belong on the NAS under the existing coordinator workflow. No NAS job or deployment is started by this patch. The changed Rust golden projection still needs its normal crate check there:

```sh
cargo test --manifest-path lab/live/Cargo.toml --locked paper_core::tests
```

Do not interpret the repository's existing lab-only GitHub scope check as a Rust build or Python test run. Local results and the pending NAS check must be reported separately.

## Results that still require new evidence

- E63/E65/E70 report recalculations must identify the final implementation and the failed-entry attempt-day convention. Existing PR #570's earlier exit-day correction is separate historical evidence, not a result of this patch.
- E73's report needs regeneration with the fixed calendar; E75's A/B gate needs rerunning on the corrected labels. Existing output is not silently overwritten or promoted.
- Running paper/UI processes and journals are untouched. A controlled deployment is still needed to activate rule 2 for future Python paper exits; old exits remain historical.
- No new profitability, holdout, live-money or strategy claim follows from these fixes.
