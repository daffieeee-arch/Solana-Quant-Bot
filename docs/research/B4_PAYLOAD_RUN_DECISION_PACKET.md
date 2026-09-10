# B4 payload run decision packet

> **Document status: ACTIVE — UNAPPROVED.** Twin of
> [`B4_PAYLOAD_RUN_DECISION_PACKET.json`](B4_PAYLOAD_RUN_DECISION_PACKET.json).
> `approved: false`, `networkEnabled: false`, `readyToRun: false`,
> `executablePlan: false`. This packet authorizes **no** OF1/Triton call.

## Why this packet exists

The 2026-09-06 authentic metadata-only run completed four publications and then
stopped for review. Its GO is expired and must not be reused. B4/#83 remains
`In Progress` / `Unproven`. The next honest network step, if ever approved, is a
**separate** payload GO bound to retained metadata receipts — not a silent resume
and not Bronze/Silver work.

This packet is that review surface. Filling fields is not approval.

## Bound authentic metadata (outside Git)

| Field | Value |
|---|---|
| Result SHA-256 | `8c77b3f6d7a1daec92835284bdc81ba4e18cb0a0166abcec5135252da5c81b21` |
| Run id | `8a350ea0c149f9c49e0615a9460ed3695eec64af21a778b0f8d7786adbae336f` |
| Code SHA | `184e32eb03b6dfd681ed5dcb479678a3ae60da05` |
| Executable SHA-256 | `50a3f87703c7a95fc1bf13d3dc6e27176ecc517a8f3e818e4aa91681013e9b0d` |
| Publications / retries | 4 / 0 |
| Received / reserved entity bytes | 5,184,161 / 5,192,192 |
| Next instruction in result | stop for review; payload needs a separate concrete plan and GO |

See [`OF1_STAGED_ACQUISITION.md`](OF1_STAGED_ACQUISITION.md) and
[`TERRAPC_GITHUB_SYNC_STATUS.md`](TERRAPC_GITHUB_SYNC_STATUS.md).

## Unapproved candidate only

Offline derivation from the retained index maps the first epoch slot
`[422496000, 422496001)` to CAR bytes `[59, 45110)` (45,051 bytes). That
derivation is a **compatibility check**, not an approved payload plan.

- The historical provisional `[422506000, 422506128)` window must not be fetched.
- The original `[422496000, 422496128)` 128-slot candidate remains unapproved;
  sixteen attempts cannot cover 128 nonempty slots under retained caps.
- Root-to-slot membership stays `UNAVAILABLE`. Whole-CAR hash is unverified.
- Slice class, if ever run, is permanently `ENGINEERING_VALIDATION_ONLY`.

## Remaining aggregate caps are not authorization

Metadata leftover attempts/bytes/runtime are recorded in the JSON twin under
`remainingAggregateCapsAfterMetadata`. The metadata result itself states that the
remainder is not payload authorization.

## GO checklist (operator)

A future explicit GO must set the JSON authority flags and name:

1. exact payload plan hash;
2. executable SHA-256 and reviewed commit;
3. operator identity, approval id, not-after time;
4. byte/request/disk/runtime hard stops;
5. binding to the four retained metadata receipt identities;
6. cost/availability confirmation for `files.old-faithful.net`.

Until then: **no network call**.

## After a successful payload (later)

Only then may B5/#84 start under [`B5_ENTRY_GATE.md`](B5_ENTRY_GATE.md). Payload
Raw does not itself complete B4 research claims, produce Silver, or imply an edge.
