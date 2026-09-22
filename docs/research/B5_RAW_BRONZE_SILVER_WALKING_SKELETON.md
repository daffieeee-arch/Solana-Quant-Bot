# B5 Raw → Bronze → Silver walking skeleton

> **Document status: ACTIVE — bounded technical result, not B5 completion.**

The walking skeleton reuses the immutable, receipt-bound collection already
stored under the configured OF1 data root. Its fixed native input is the
three-slot pilot range `[422669516, 422669519)` from the collection whose
`collection.json` SHA-256 is
`39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab`.
No source bytes, receipts, datasets, manifests or historical hashes are
rewritten.

Rust `of1-bronze-decoder` owns the Raw-to-Bronze decode and the bounded Pump
variant admission. Rust `of1-parquet-projection` is the definitive physical
Bronze/Silver writer: it emits deterministic uncompressed Parquet shards,
retains exact integer/null/state fields, and binds physical file hashes to
ordered logical record hashes. The Python walking-skeleton command only reads
those explicit manifests and Parquet paths and presents already-authorized
records; it contains no wire decoder and no alternative Silver semantics.

The private output is produced outside Git with:

```bash
/home/chupa/.local/share/solana-quant/toolchains/columnar-query-313-duckdb155/bin/python \
  research/columnar-query/walking_skeleton.py \
  /home/chupa/Solana-project/data-old-faithful-one/datasets/b5-pilot-context-20260914.QFk5Sc/collection-complete \
  /home/chupa/Solana-project/data-old-faithful-one/governance/b5-raw-bronze-silver-walking-20260922/report-final \
  --start-slot 422669516 --end-slot 422669519 \
  --collection-sha256 39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab
```

The dossier contains a canonical `skeleton.json`, a manifest, all selected
Raw source/byte-range index rows, all selected Silver facts, a lifecycle JSON,
and a standalone `index.html`. Operational clocks are kept only in
`execution.json`. The report distinguishes selected slot/block inventory,
Bronze packages, top-level and recorded CPI references, failures, diagnoses,
Silver facts, balance observations, `UNAVAILABLE`, `GAP` and `QUARANTINED`.
Failed transactions remain evidence and never produce a successful state
transition. Event-reported reserves are not stored as historical account
state, and acquisition/processing clocks are not historical features.

The native source class remains `RESEARCH_SAMPLING`, but the result is still
technical and `research_ready: false`. A mechanical gate rejects
`ENGINEERING_VALIDATION_ONLY` from research inputs; this task makes no B4/B5
promotion, lifecycle-completeness, Research Ready, strategy or edge claim.
