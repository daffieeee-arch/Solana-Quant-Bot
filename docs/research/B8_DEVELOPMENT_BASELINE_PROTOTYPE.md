# B8 DEVELOPMENT baseline prototype

> **Status: ACTIVE, preparatory only.** The fixed method in
> `research/columnar-query/b8-development-baseline-method.json` was set before
> implementation. This does not complete #87, B7, or Research Ready.

The only input is the four already inspected, native-authorized DEVELOPMENT
windows w00–w03, reread through the pinned #157 point-in-time contract. The
window is the observation unit. At the boundary after eight complete slots,
the candidate rule predicts a later admitted same-mint buy/sell pair if at
least one successful admitted buy was already present. The comparator always
predicts no pair. Later sells are label evidence only. A positive label needs
the existing source-bound pair witness from distinct transaction packages.
An absent witness is **UNKNOWN** while semantic Pump coverage cannot certify
a negative. The Brier mean squared error is computed only for known labels;
its denominator and unknown count are explicit.
Unpaired first-half candidate mints remain separately visible as unknowns even
when their window has another mint with a positive witness.

This necessary-condition rule is intentionally simple and can fail when an
earlier buy has no later admitted sell in a window with certified negative
coverage. The four authentic windows were already inspected and all have a
positive pair. Their apparent scores cannot establish discrimination,
population performance or an entry/exit edge. The prototype therefore reports
`INSUFFICIENT_SAMPLE` regardless of the descriptive arithmetic.

The chain boundary is a hypothetical Gold decision boundary, not a proven
historical observation or action time. Missing observation latency, actionable
time, independent execution opportunity, executable price, quote units and
historical protocol coverage stay unavailable. A historical event is not a
fill. No held-out evaluation input, evaluation reader, provider or decoder is
used. The private JSON/HTML are created outside Git from pinned source hashes;
execution clocks are separate from canonical content.

Run offline with the existing project-local Python toolchain, passing a new
directory under the OF1 governance root:

```text
python research/columnar-query/development_baseline.py /home/chupa/Solana-project/data-old-faithful-one/governance/NEW-PRIVATE-REPORT
```

The create-only `report.json` contains all admitted fact/package identities,
Raw receipt and collection/Parquet manifest bindings, and separate canonical
feature/label hashes. `report.html` gives a compact window view; `execution.json`
holds only operational clocks. Open the HTML locally; no service is needed.

An actual entry/exit test later needs independently evidenced observation and
execution timing, units/liquidity/cost assumptions and a separately authorized
untouched evaluation sample. These viewed DEVELOPMENT windows cannot be reused
as an untouched holdout. No evaluation release is authorized here.
