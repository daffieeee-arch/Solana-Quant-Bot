# B4 acquisition monitor

> **Document status: ACTIVE — implementation evidence is Fixture.** B4/#83 stays
> In Progress / ACTIVE NOW / Unproven. B5/#84 stays Backlog / NEXT. This interface
> observes; it grants no acquisition authority and does not complete B4.

## What is built

A separate React/TypeScript application displays Rust-owned acquisition readings.
It does not import or extend the frozen dashboard. A GET-only local server serves
bounded snapshots and hash-checked receipt/manifest links. No browser route can
start, retry or approve acquisition. No monitoring database or system package is
required.

The explicit Rust `monitor` feature enables local Unix-datagram telemetry only;
default builds remain without socket transport. The existing default-off
`network-of1` feature and separate metadata/payload admission are unchanged.
Building with both features does not authorize a request.

`https::CaptureObserver` observes the actual durable reservation, validated
headers, each successful entity read, verification, publication and failure.
The store's existing durable ordering is unchanged. No fsync is added per
network fragment. A missing or congested monitor drops telemetry rather than
blocking capture, replaying an operation or changing a budget.

The small local relay publishes bounded latest-state JSON outside immutable run
directories. It never takes the acquisition writer lock. There is no operational
history migration, extra file in the old run, provider fallback or automatic
cleanup. Telemetry is lossy operational state, not a replacement for receipts.
If a terminal sample is lost, the UI becomes stale; the read-only importer can
reconstruct the receipt-backed terminal summary without resuming the writer.

## Exact meanings

| Display | Meaning |
|---|---|
| Selection progress | Published selections plus the current attempt's received prefix; retry prefixes are not added twice. An interrupted prefix may reset on retry |
| Selection denominator | Planned operation count; exact byte total only once every selected operation's length is known. Never the whole CAR size or a budget cap |
| Received entity bytes | Real successful Rust entity reads, including retry traffic; excludes headers/TLS/physical wire overhead |
| `PROCESS_OBSERVED` | Current process has observed the traffic since fresh initialization |
| `DURABLE_LOWER_BOUND` | Restarted process has no knowledge of lost userspace fragments; surviving receipt bytes plus new reads are a lower bound |
| `RECEIPTS_ONLY` | Historical import counts published receipt bytes only; historical failed-attempt traffic is not reconstructed |
| Verified / published | The byte counter conservatively counts only verified, durably published Raw/receipt pairs. A separate stage shows verification before publication; neither means CAR-root membership, protocol support or Research Ready |
| Reserved | Full response allowances durably charged before dispatch, not downloaded or useful data |
| Speed / ETA | Rust monotonic-clock samples; ETA stays unknown without sufficient measurements and a known remaining selection size. It estimates download only, not remaining verification/publication time |
| Shared speed limit | New plans bind 87,500,000 response-entity-bytes/s = 700 decimal Mbps, across official OF1 runs by the same Linux user with concurrency one. The bounded short burst is 65,536 bytes, not a per-connection allocation. TLS/HTTP/other physical network overhead is outside this byte unit |
| Limiter wait | Rust reports the observed waiting flag and actual monotonic sleep nanoseconds accumulated in the current process only, preserving sub-millisecond waits. Waiting does not extend deadlines. Import cannot reconstruct past waiting; old plans without this setting remain unavailable, not zero/unlimited. A stale snapshot makes the current waiting state unknown |
| Storage | Existing conservative durable-store disk charge and available filesystem bytes, sampled at operation boundaries/import; not a per-fragment filesystem scan |
| Last update | Producer timestamp/session/sequence; a live snapshot older than three seconds is visibly stale. Recorded history is labelled recorded, never presented as a current download |
| Domain counts | `UNAVAILABLE_NOT_DECODED_IN_B4`; transactions/Pump events/coins are not zero |

`OF1_MONITOR_1` is the bounded operational snapshot, not a canonical research
schema. It carries at most 32 operations, 64 speed samples, 16 errors and 40
artifact references in 64 KiB. The relay keeps at most 16 latest run snapshots.
Rust owns selection/budget/evidence calculations; TypeScript validates transport
shape and formats the result. A browser refresh does not mutate any run.

The optional `rate_limit` object carries the immutable plan policy separately
from lossy operational waiting measurements. It is omitted for historical plans;
new-plan receipt imports show the policy with `waiting` and `process_wait_ns`
both null. A new process starts a new operational wait counter, not a durable
run-total claim. Intra-request wait hooks use the existing five-per-second
telemetry sampling boundary and add no per-fragment file write or fsync. The
browser continues to display actual `traffic.speed_bps` independently of the
configured limit; a measured low rate does not by itself prove throttling.

New clock-policy runs carry a separate `clock_context`: UTC remains the actual
producer timestamp, while elapsed/remaining time uses the immutable same-boot
deadline. Boot mismatch or unavailable elapsed-clock evidence makes remaining
runtime unknown, not zero or renewed authority. Session/sequence and boot order
accept an actual UTC correction without accepting an older snapshot. Browser
staleness uses monotonic time since the last changed sample for this policy;
historical snapshots keep their original contract. None of these observational
fields can admit an acquisition; see the [clock contract](OF1_STAGED_ACQUISITION.md#versioned-utc-provenance-and-boot-deadlines).

## Authentic and simulated views

The later 2026-09-11 authentic payload has a separate
[receipt-bound offline verification result](OF1_RECORDED_CAR_VERIFICATION.md).
The UI distinguishes capture/publication, Raw/receipts, CAR/slot verification
and domain decoding. It reads an external `verification-RUN_ID.json` from the
snapshot directory, never the immutable run. The GET-only attachment validates
manifest/receipt/Raw references and stage/count/selection consistency; it does
not rerun the Rust verifier or continuously rehash Raw. No report means
`UNAVAILABLE`, even at 5/5 publications. Original failures remain separate history.
The following older metadata and simulation results retain their original scope.

The existing authentic metadata run is
`8a350ea0c149f9c49e0615a9460ed3695eec64af21a778b0f8d7786adbae336f`:
four HTTP 200 publications, zero retries, 5,184,161 published entity bytes and
5,192,192 reserved bytes. Its original code/executable/lease/Raw/receipts remain
unchanged. The read-only importer checks manifest/attempt/receipt/content hashes;
it never calls store resume or replaces the source executable identity.
Historic intra-request speed and ETA were not recorded and remain unavailable.
Recorded elapsed time is initialization to last receipt, not a measured process
exit duration. No CAR payload or decoded coin exists in that run.

The separately named local simulation uses numeric-loopback TLS and a synthetic
5,184,000-byte epoch index. Fragments are paced so real received measurements can
be seen before publication. A deliberately short CID response stops a child
process; an explicit second child reopens the same fixture run, preserves spent
attempts and original deadlines, and completes the remaining metadata. It uses
no official endpoint, credential or paid service. This is not a simulated claim
about real OF1 throughput.

## Local browser startup

Use the installed toolchain wrapper in WSL. Keep telemetry and fixture data on
WSL ext4 outside the checkout. The commands below build offline from the existing
lockfiles and import already-present evidence; they perform no provider request.

```bash
cd /home/dmesdary/code/Solana-Quant-Bot
TOOLCHAIN_RUN=/home/dmesdary/.local/share/solana-quant/run-with-toolchain
"$TOOLCHAIN_RUN" npm run build
"$TOOLCHAIN_RUN" cargo +1.97.1 build --offline --locked --release \
  --manifest-path rust/of1-range-recorder/Cargo.toml \
  --features monitor,tls-fixture --bin of1-monitor --bin of1-monitor-simulation
```

Import the existing authentic result read-only (no writer lock or lease renewal):

```bash
MONITOR_DIR=/home/dmesdary/solana-quant-data/monitor/b4-local
rust/of1-range-recorder/target/release/of1-monitor import \
  /home/dmesdary/solana-quant-data/runs/of1-e978-metadata-29d04959-01 \
  "$MONITOR_DIR"
```

Start the browser server in terminal 1, from the repository:

```bash
/home/dmesdary/.local/share/solana-quant/run-with-toolchain \
  npm run start:acquisition-monitor -- \
  --snapshots /home/dmesdary/solana-quant-data/monitor/b4-local --port 4173
```

Optional live **local simulation**, terminal 2:

```bash
rust/of1-range-recorder/target/release/of1-monitor relay \
  /home/dmesdary/solana-quant-data/monitor/b4-local/relay.sock \
  /home/dmesdary/solana-quant-data/monitor/b4-local
```

Then terminal 3, with a new fixture root (never reuse an authentic run):

```bash
FIXTURE_PARENT="$(mktemp -d /home/dmesdary/solana-quant-data/runs/monitor-fixture.XXXXXX)"
FIXTURE_ROOT="$FIXTURE_PARENT/run"
rust/of1-range-recorder/target/release/of1-monitor-simulation \
  --with-payload \
  "$FIXTURE_ROOT" /home/dmesdary/solana-quant-data/monitor/b4-local/relay.sock
# Optional receipt-backed terminal refresh; still read-only, no writer resume:
rust/of1-range-recorder/target/release/of1-monitor import \
  "$FIXTURE_ROOT" /home/dmesdary/solana-quant-data/monitor/b4-local
```

`--with-payload` additionally admits one sealed synthetic CAR range with a
separate **Fixture** payload lease after metadata restart. Its paced 206 response
exercises selected-slot/range progress, then the existing offline verifier checks
five archival nodes and four links. No Solana/Pump transaction payload is decoded.
The live snapshot labels this check `CID_SLOT_VERIFIED_FIXTURE_ONLY`; the stdout
integrity report must be retained outside the immutable run. Historical import
does not replay that verifier and reports CAR verification unknown. Root-to-slot
membership and whole-CAR hash remain unverified. Without this flag the original
metadata-only fixture scenario is unchanged; its earlier receipts/screenshots
remain historical evidence. The synthetic range is not the authentic index's
45,051-byte candidate and proves no Pump activity.

Do not start a second relay/server on an occupied socket/port. After an abrupt
relay exit, choose a fresh socket name for both relay and simulator; no run data
needs removal. The simulator takes about 15 seconds on the observed machine;
this is not an OF1 speed estimate. Select the amber **Lokale simulatie** card in
the browser. The automatic restart may make its intermediate stop brief; the
captured screenshots below include a deliberate operator pause at that boundary.

The browser URL is **http://localhost:4173/** (Windows → WSL localhost forwarding).
The server binds only `127.0.0.1`. No remote/public bind is needed.

For a future separately approved run, `of1-acquire capture-stage` can receive
`--monitor-socket LOCAL_SOCKET` when compiled with `monitor,network-of1`. That
argument affects telemetry only: it cannot change the fixed OF1 endpoint, lease,
budget, executable identity or retry behavior. No such run is authorized here.

## Executed browser evidence

The [machine-readable browser receipt](../../schemas/acquisition/of1/monitor-browser-evidence.json)
binds the actual Rust run IDs, DOM readings and screenshot hashes. These are
real browser screenshots, not generated mockups:

- [Authentic metadata, receipt-backed recorded result](assets/b4-monitor/authentic-metadata.png)
- [Live loopback response, bytes rising before publication](assets/b4-monitor/simulation-live.png)
- [Same fixture after restart, no refunded attempt](assets/b4-monitor/simulation-complete.png)

The short-response stop is recorded in the machine-readable DOM evidence and
the original screenshots outside Git. That capture exposed an almost-complete
percentage rounding to 100%; the display now says `<100%` until actual completion,
with a regression test. The pre-correction screenshot is retained as such, not
presented as the final UI.

Three successive live screenshots observed 720 KiB → 1.3 MiB → 1.8 MiB received,
while published stayed 0 B. Rust measured approximately 398/396/398 KiB/s;
the explicitly current-operation download estimate decreased 10 → 9 → 8 seconds.
The synthetic index has 5,184,000 bytes. The stopped fixture was resumed under
the same original deadline: 4 publications, 5 attempts, 1 retry,
5,184,140 published bytes and 5,196,288 reserved bytes. The resumed received
counter is honestly `DURABLE_LOWER_BOUND`; 11 pre-crash userspace bytes are not
invented from reservations. The original authentic metadata has different
sidecar lengths and remains 5,184,161 published bytes / 4 attempts / 0 retries.

Full local trace/screenshots/logs are retained outside Git under
`/home/dmesdary/solana-quant-data/governance/b4-download-monitor-20260906`.
All 190 files in the authentic run were rehashed against its existing inventory
(`5ff4a8b909608c37b1ca3a5b95b36d1084cb598ad975a4007a93867ecef1cafd`)
and remain identical. No new provider call or historical payload occurred.

### Paced payload and restart evidence (2026-09-11)

The [additional browser receipt](../../schemas/acquisition/of1/monitor-payload-browser-evidence.json)
records a new, separate **Fixture** run. Chrome on Windows observed actual Rust
payload reads increasing 32 → 208 → 384 bytes while published payload bytes stayed
zero; the DOM's independently polled progress also increased. ETA was initially
unknown, then 3,254 → 1,050 ms. After receipt publication and offline verification,
all 468 synthetic payload bytes were complete. The original deadline and charged
failed attempt survived the metadata child restart: 5 publications, 6 attempts,
1 retry, 5,184,608 published bytes and 5,196,756 reserved bytes. The test gate executes
this whole scenario again without a running collector, proving telemetry is optional.

- [Live payload request, before publication](assets/b4-monitor/payload-live-night.png)
- [Completed fixture with explicit integrity limits](assets/b4-monitor/payload-complete-night.png)

The new run is retained at
`/home/dmesdary/solana-quant-data/runs/of1-monitor-payload-fixture-20260911-01`.
Its executable, stdout integrity report and browser traces are retained under
`/home/dmesdary/solana-quant-data/governance/night-shift-20260911` and hashed in the
receipt. Both earlier fixture roots and the authentic metadata run remain unchanged.
The screenshot is not authentic CAR evidence, a decoded transaction dataset or a
claim about public OF1 performance.

## CI / roadmap-sync triage (2026-09-11)

PR [#108](https://github.com/daffieeee-arch/Solana-Quant-Bot/pull/108) product CI
`tests-build-zero-cost` passed. Failed Sync run `34543139237`, job
`103089946817`, has no executed steps: its annotation states that the job was
not started because of account payments or spending limits. No token check or
Project mutation executed in that job. A local missing-token error does not
diagnose this runner-start failure. The original job/annotation are retained in
`/home/dmesdary/solana-quant-data/governance/night-shift-20260911/`.
The independent workflow runs on trusted main; this PR changes no token,
Project schema or sync code. Recheck actual final-head Actions after runner
availability is restored; do not weaken checks or mark B4 complete from CI.

## Next bounded results, not implemented here

1. Accept this monitor and finish the acquisition binary before generating a
   new executable/decision identity. Preserve the old packets. The practical
   next authentic path is a small new run with separate exact metadata GO and
   then payload GO, not relabelling the old manifest for a new binary. Resource
   caps are not evidence of paid query costs.
2. B5 decodes the accepted authentic Raw with Rust into canonical Bronze/Silver
   and a static quality/lifecycle report. Add **Discovered coins & data quality**
   from those facts: mint identity, evidenced name/ticker, creation/creator when
   observed, first/last dataset observation, supported transactions/buy/sell,
   volume/curve/migration fields, coverage, quarantine and field provenance.
   Ticker is not identity; first observation is not launch. Missing creation,
   logos or external metadata stay unknown; event prices are not fills. No
   second browser/Python decoder is introduced to populate an empty panel.
3. The Research Observatory adds interactive lifecycle/cohort comparisons over
   those accepted facts. A separate bounded Python delivery then pins a tested
   DuckDB/Polars/Arrow/marimo/MLflow workspace and demonstrates one reproducible
   query/plot/experiment on appropriate authentic data—not empty scaffolding.
4. A later explicitly selected AI integration binds answers to executed SQL or
   Python, dataset/code identity, sample size, plots and limitations. Exploration
   and untouched evaluation data remain separate; negative experiments remain
   visible. AI prose alone is not empirical evidence. No external AI service,
   new MCP or paid account is activated now.

Engineering failure, insufficient admissible data and edge falsification stay
distinct. An `ENGINEERING_VALIDATION_ONLY` slice can prove mechanics, never a
strategy edge. Strategy research waits for suitable outcome-independent data;
strategy implementation and prospective testing follow supported evidence.
