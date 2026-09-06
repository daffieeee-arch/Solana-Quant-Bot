# OF1 offline transport evidence

> **Document status: ACTIVE — FIXTURE ONLY.** Deterministically generated from an executed local HTTP scenario. No provider or historical-data traffic.

Index planning → durable reservation → exact loopback HTTP response → Raw/receipt publication → truncated next response → actual process exit → restart → matching retry → verified progress.

- Fixture index SHA-256: `3919f15479264300e451c53ce7b276cf90391488b9cbbd7f4c9c1ea70f41c60c`
- Plan SHA-256: `79cd0dd8bf954caaed969d55bc46a06ea7c52cb75d835fd029821d232f1cc222`

| Executed result | Before retry after restart | Final |
|---|---:|---:|
| Durable attempts | 2 | 3 |
| Charged response-entity allowance | 64 | 96 |
| Verified published response-entity bytes | 32 | 64 |
| Published requests | 1 | 2 |
| Unpublished attempts | 1 | 1 |
| Original wall deadline | 160000 | 160000 |
| Original boot deadline | 70000 | 70000 |

The child exits with code 86 while the store is alive; Rust destructors do not run.
The first publication survives. A seven-byte truncated prefix is retained and the next process retries the same range; the full retry agrees on those bytes.
All three attempts remain charged. The already-published range is not fetched again.
The report proves this process-loss/restart scenario, not physical power loss.

The fixed fixture server supplies 71 entity bytes (32 + 7 + 32). The conservative durable allowance is 96 bytes.
This known fixture schedule does not convert unreceipted actual bytes after arbitrary process loss into a measured zero or complete total.
`response_entity_bytes` is not a physical-wire cap. HTTP headers and a bounded post-entity framing probe are distinct from the declared entity.

Every published Raw/receipt pair is revalidated; both original deadlines remain unchanged.
Changed source identity is source drift; differing retained overlapping bytes under the same claimed identity are conflicting bytes. Neither may be published.
Those rejection cases, malformed ranges/streams, retry limits and deadline stops are exercised by the focused transport tests.

Domain counts and slot-semantic membership remain `UNAVAILABLE_NOT_DECODED_IN_B4`; epoch-root membership and CID verification remain `UNAVAILABLE`.
No authentic observation, historical activation, research readiness, economics or profitability is established.
B4 remains open / In Progress / ACTIVE NOW / Unproven. No acquisition run is authorized.
