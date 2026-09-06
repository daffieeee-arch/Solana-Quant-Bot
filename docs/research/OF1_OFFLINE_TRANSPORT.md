# OF1 offline transport and invariant parity

> **Document status: ACTIVE — FIXTURE ONLY.** B4 remains open / In Progress / ACTIVE NOW / Unproven. This partial repair does not authorize or complete authentic acquisition.

This extends the [source-bound planner](OF1_RUST_PLANNER.md) and [durable Raw store](OF1_DURABLE_RAW_STORE.md) with one local HTTP simulation path. The [executed report](OF1_TRANSPORT_EVIDENCE.md) and [machine-readable result](../../schemas/acquisition/of1/transport-evidence.json) show index planning → durable reservation → local response → Raw/receipt publication → process exit/restart → resumed progress. There is no OF1, RPC, provider, credential, domain decoder or wallet capability.

## Boundary and protocol subset

The Rust crate's default feature set remains empty and socket-free. Only the explicit `loopback-fixture` feature exposes `LoopbackFixture::new(port)`. Its address is constructed internally as numeric `127.0.0.1`; callers cannot supply a host, URL, proxy, backend, S3 configuration or endpoint environment variable. Logical source identity remains bound to the approved planner format, but the transport receipt is **Fixture**, never evidence of a request to that source. PR103 did not implement `network-of1`; the later [staged path](OF1_STAGED_ACQUISITION.md) adds that separately admitted, default-off feature. The no-new-dependency/capability statements here describe this retained PR103 fixture lane, not the later staged implementation.

The deliberately narrow HTTP/1.1 fixture contract accepts a single `206` response, an exact `Content-Length`, exact planned start/end in `Content-Range`, an object total matching the plan, and `Connection: close`. It rejects full-object `200`, redirects, duplicate headers, missing/ambiguous lengths, transfer encoding (including chunked), compression and multipart responses. Headers are capped at 16,384 bytes. Entity reads are bounded by the smaller of the remaining range and an 8,192-byte chunk; no unbounded read-to-end is used. After the declared entity, at most one additional byte is read to verify connection EOF. Any trailing byte rejects publication; that framing probe is not another response entity.

The budget metric remains **`response_entity_bytes`**, not physical wire bytes. Headers, the single-byte framing probe, TCP/TLS overhead and packets are not silently claimed to be covered by that metric. Header size, individual reads and the original attempt deadline have separate hard limits. TLS and live-server compatibility are outside this fixture delivery.

This subset follows the length/incomplete-response distinction in [RFC 9112 §6.3](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3) and range semantics in [RFC 9110 §15.3.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7); it intentionally supports less than a general-purpose HTTP client. A rejected fixture framing form is not evidence that authentic OF1 supports or requires another form.

## Durable attempt, streaming and publication

1. Resolve a request from the verified index plan. Already-published requests are read from the store, not dispatched again.
2. Persist and fsync one reservation before connecting. It charges the complete planned response allowance and one attempt. Failure or restart never refunds it.
3. Validate bounded response headers, including source-validator continuity, before reading entity bytes.
4. Before each bounded read, check the original attempt/run deadlines and conservative disk space. Persist received chunks as immutable hash-bound byte/receipt pairs under the run's pending area before accepting their prefix as reusable evidence.
5. On exact length and framing EOF, publish the complete Raw/receipt pair through the existing fsync/atomic-publication protocol. The receipt binds normalized range/status and optional strong ETag, plus the executed retry comparison. Raw bytes remain unchanged and domain decoding never occurs.
6. On error, poison the writer. Retry requires reopening and revalidating the persisted plan, index, source/context fingerprints, executable identity, clocks, reservations, chunks and published content. A retry is a new, explicitly charged attempt, never mutation replay.

Per-read socket timeouts are bounded by the **remaining original** attempt/run deadline, not a renewed idle timeout. A local monotonic deadline additionally prevents a stream from extending its attempt through repeated short reads. The durable run's wall/boot deadlines survive process restart; changed boot identity or clock rollback fails closed. The original publication crash tests remain authoritative for the individual fsync/rename seams.

The fixture store deliberately retains failed-attempt chunks for comparison and forensics, and assembles a bounded complete payload before publishing the final pair. These copies consume the configured disk high-water budget. This is not a claim of a scale-optimized acquisition implementation. An incomplete or corrupt chunk/pair cannot be silently repaired, promoted or treated as a valid prefix.

Resume validates surviving sealed prefixes under the existing non-adversarial filesystem/fsync contract. External deletion or rollback of a complete terminal chunk pair is not proven detectable; this delivery does not claim authenticated completeness of an attempt's history or physical power-loss proof. No code prunes those artifacts, and no missing chunk is synthesized.

## Retry bytes: exact meaning

| Observation | Result |
|---|---|
| Complete previous response metadata identifies a different object total or different strong ETag, including loss of a previously recorded validator | `SOURCE_DRIFT`; persist terminal quarantine and stop |
| Retained bytes overlap the same planned range but differ, with no prior metadata drift | `CONFLICTING_BYTES`; persist terminal quarantine and stop |
| All previously retained overlapping bytes match | `RETAINED_OVERLAP_MATCHED`; only the compared prefix is supported |
| No durable previous bytes exist | `NO_PRIOR_BYTES`; comparison is unavailable, not a consistency claim |
| No strong validator is present | Source immutability is unproven even when compared prefixes match |

Validators apply across ranges of the same run/object; byte comparison applies only to overlapping attempts for the same planned request. A shorter retry cannot erase a longer prior prefix. Terminal conflict evidence survives reopening; a later successful response cannot overwrite it. Hash agreement is local integrity evidence, not authentication, CID verification or epoch-root/slot membership.

## Test lanes, parity and evidence

The existing default-feature graph, build scripts, tests and old evidence generators remain under the socket-denying launcher. The explicit loopback tests/report need local sockets; their exact source hashes are reviewed in `assert-of1-planner-offline.mjs`, with no general network/process exception. Compilation remains locked/offline and socket-denied. The loopback runtime lane is **not claimed to be an OS-wide external-network sandbox**: its boundary is the reviewed fixed-loopback constructor and source-pinned fixture harness. No additional dependency, build script, provider client or endpoint is introduced.

The [parity manifest](../../schemas/acquisition/of1/transport-parity.json) maps all 26 former JavaScript test cases to preserved Rust/policy tests or explicit invalid/implementation-coupled retirement. Removal follows passing replacement tests, not line coverage or apparent unreachability. Historical planner/durability reports and the earlier salvage record retain their original bytes; Git history at the recorded base commit reconstructs removed paths. No legacy directory is added.

Domain counts, slot semantics and historical transaction evidence remain `UNAVAILABLE_NOT_DECODED_IN_B4`, never zero. CID/root membership and authentic source stability remain `UNAVAILABLE`. The report is deterministic **Fixture** engineering evidence, not Engineering Validation, insufficient research data, edge falsification, Research Ready status or profitability. Live lease budgeting and the distinction between engineering failure, insufficient data and edge falsification remain in the separately planned lease/evidence step; no live run plan is approved here.
