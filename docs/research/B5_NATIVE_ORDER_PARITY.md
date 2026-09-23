# B5 native delivery-order parity

> **Document status: ACTIVE — bounded technical evidence, not B5 acceptance.**

The current native path accepts three fixed offline delivery schedules at the
verified CAR transaction-envelope boundary: `canonical`, `reverse`, and
`odd-even` (zero-based odd canonical ranks ascending, then even ranks ascending).
Each schedule covers the entire selected range, including inter-slot delivery.
The original Raw bytes, receipts, archive links and transaction positions stay
unchanged. The same Solana/status/Pump decoder processes each complete envelope;
its complete Bronze record and supported Silver facts are kept together. Only
completed packages are restored to their authoritative archival chain position.
No instruction or CPI inside a transaction is reordered.

The reader retains its integrity, source-change, frame, slot, selection and
publication gates. Aggregate byte limits are charged during delivery. A
metadata-only run remains unavailable, and failures/missing/quarantine/unsupported
outcomes are preserved. The physical writer remains `of1-parquet-projection`.
There is no second decoder, acquisition capability, concurrency framework or
new admission rule.

```text
of1-bronze-decoder RECORDED_RUN NEW_DECODE_DIRECTORY [canonical|reverse|odd-even]
```

`execution.json.native_delivery` binds the actual delivery ranks to the canonical
source/CID/receipt/package inventory. It is operational evidence, separate from
canonical records and historical time. The trace is added only when a schedule
is explicitly requested; ordinary two-argument runs retain their compact
execution receipt. Diagnostic mode keeps the existing publication caps.
The existing Rust projector verifies the
execution receipt, original receipts, Bronze/Silver parents, schema and exact
record bytes before publishing Parquet.

The small `research/columnar-query/native_order_parity.py` driver runs all three
schedules with existing binaries, then reads the actual Parquet through the
existing manifest verifier. It writes `parity.json`, `index.html`, operational
`execution.json` and a final `COMPLETE` marker in a new directory. A partial
failed run never receives a successful aggregate marker. Commands inherit the
caller's network isolation and add explicit process/time/file limits; use the
existing network-deny launcher and resource scope on the VPS.

For the authentic pilot, pass `--reference-collection` with the preserved
collection of SHA-256
`39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab`.
Only its original three pilot batches `[422669516,422669519)` are read. The
reference must still contain 3,224 atomic packages, 223 failures and seven
Silver facts. Without a reference, only the six-package native Fixture contract
used by CI is accepted; it cannot substitute for the authentic result.

Logical hashes must match exactly among the three new runs. Physical hashes
are also recorded: with identical records, the same writer and identical
partition settings, the existing deterministic-writer contract requires equal
Parquet bytes. This does not demand equal physical files across different
partitions or writer versions.

Historical record hashes include the compiled decoder identity and absolute
Raw location. The historical comparison validates their respective execution
bindings, uses the exact run-relative Raw path, and substitutes independently
verified Bronze-parent identities in Silver. Only those explicitly listed
provenance paths differ; every other field must remain equal. All new executions
retain their true code/executable identities; historical files are never resealed.

The regression uses multiple packages in each slot, multiple instructions/CPIs,
a failed Pump transaction, missing and corrupt/unsupported wire, and unavailable
state. The existing Parquet offline gate exports that Fixture and checks the
three real reader/writer executions, including both logical hashes.

This proves delivery-order parity for the selected native path only. The 22
collection facts, seven Mayhem rejections and original sample classes remain
unchanged. No Pump coverage, historical activation, actual CPI privileges,
complete lifecycle or Research Ready claim follows. B5/#84 remains open and
Unproven; its separate lifecycle acceptance question is not resolved here.

Copy the generated `index.html` and `parity.json` through the existing private
SSH connection and open `index.html` locally. No server or public access is needed.
