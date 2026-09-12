# Bounded recorded Pump sell → Silver event facts

> **Document status: ACTIVE — local offline B5 preparation, not delivery completion.**
> This follow-up starts at the explicitly authorized local commit
> `21de55dfb9fb4b42caad8df0d8bbe3fa89920969`. The earlier buy branch/PR is unchanged.
> B4/#83 remains In Progress / ACTIVE NOW / Unproven; B5/#84 remains Backlog /
> NEXT / Unproven. GitHub access, acquisitions and Project mutations are not authorized.

## Source and selected observation

Slot `422496004`, transaction `153`, outer instruction `2` contains exactly 24
sell bytes, not an unexplained prefix. The unchanged [authentic CAR section](../../rust/of1-bronze-decoder/tests/fixtures/authentic-pump-sections.json)
is decoded through the existing complete transaction-wire/status pipeline.
Raw SHA-256: `4487ab047ab4d4d7267a218e69877e4222178800fe5cba51547269a3772156db`.

The [source receipt](../../rust/of1-bronze-decoder/sources/pump-sell-evidence.json)
reuses only already retained files, verified offline against their hashes:

- Official Pump repository commit `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`,
  [`idl/pump.json`](https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json),
  blob `062e66f032bb9f295353b573be3400070bd55e5b`, SHA-256
  `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.
- Same-pin `docs/BREAKING_FEE_RECIPIENT.md`, SHA-256
  `8b05e0906eaf1746508b0b2f909ac7c344ce7be33a6564d595335c9fda399868`;
  `docs/PUMP_CASHBACK_README.md`, SHA-256
  `f8375a40e45c87a1cbdd50bbce744a8d7c2b9d901a1365090ff513605d24cb23`;
  the same-pin fee recipient list.
- Already retained official README-linked SDK source package `1.36.0`:
  `src/sdk.ts` SHA-256 `242736485abfa36648646b07e5b81b6e7bd4fe5a074411ebd790bc029e534b80`,
  `src/pda.ts` SHA-256 `f6311abd14dea28e80cdb852f2363ef958ec48f470a0d16c8d4cf78694c9f972`.
  SDK Git head remains registry-declared, not independently Git-verified.

No source was newly fetched. Documentation is not historical activation proof.
The full unlicensed IDL remains outside Git, not newly vendored.

| Surface | Observed bytes / interpretation |
|---|---|
| Instruction | `33e685a4017f83ad9f7897f156180000ebc7ca2600000000` |
| Discriminator | `33e685a4017f83ad`, exact pinned `sell` |
| `[8,16)` amount u64 | `26761699489951` |
| `[16,24)` min_sol_output u64 | `650823659` |
| Instruction SHA-256 | `bee483bc1525bb1cfa55b25f94a2c3848ad41ba2d5c0a21b1d5b24ce997d8da7` |
| Event | 367 bytes, `is_buy=false`, `ix_name=sell`, full Borsh exhaustion |
| Event-CPI SHA-256 | `d26a7bd6420252a2ba13e5f17d0d2a3ad5d0f36c1509d2d85289f1532bead3bb` |
| Context | outer 2 / inner order 2 / stack height 2, direct Pump parent, event authority |

## Seventeen accounts, not an extra instruction layout

The selected pattern has fourteen pinned IDL roles, followed by the documented
writable user-volume accumulator, readonly bonding-curve-v2 and writable buyback
recipient. These are remaining **accounts**, not extra instruction bytes.

| Position | Role | Required writable / signer |
|---:|---|---|
| 0 | global | no / no |
| 1 | fee_recipient | yes / no |
| 2 | mint | no / no |
| 3 | bonding_curve | yes / no |
| 4 | associated_bonding_curve | yes / no |
| 5 | associated_user | yes / no |
| 6 | user | yes / yes |
| 7 | system_program | no / no |
| 8 | creator_vault | yes / no |
| 9 | token_program | no / no |
| 10 | event_authority | no / no |
| 11 | program | no / no |
| 12 | fee_config | no / no |
| 13 | fee_program | no / no |
| 14 | user_volume_accumulator | yes / no |
| 15 | bonding_curve_v2 | no / no |
| 16 | buyback_fee_recipient | yes / no |

The actual token program is Token-2022, not Tokenkeg. Minimum compiled-message
privileges include the V0 loaded addresses. A readonly role may be globally
writable. Nine offline PDA/ATA calculations match the sealed cross-language
reference. ATA/PDA correspondence is not proof of account contents, token-account
ownership or a read of `is_cashback_coin`; creator-vault uses the event-reported
creator, not a separately read curve account.

## Admission and output semantics

One registered profile: `pump-sell-9c82f61-token2022-cashback17-direct-v1`.
The code derives its match from actual full instruction/event bytes, direct
invocation context, seventeen distinct address/privilege matches, documented fee
recipients, mint/user/amount agreement, the selected non-mayhem/no-shareholder/
track-volume-false pattern and archived successful transaction status. There is
no caller-provided compatible count or hardcoded slot/transaction allowlist.
Uniqueness is **within this bounded registered profile**, not a survey of all
historical deployed Pump versions. Nested calls remain explicitly unsupported.

Silver `PUMP_SILVER_RECORDED_SELL_1` is one atomic instruction/event fact package.
It binds the original complete Bronze record hash, native run/receipt/index
bindings, Raw/CID/section identity, transaction-wire/status hashes, signatures,
slot/order, source receipt and actual decoder identity. Output uses new directories;
neither writer state nor old reports/manifests are rewritten. `silver.jsonl` is
hashed in `execution.json` and published before the durable COMPLETE marker.
JSON is this bounded review format, **not the physical Parquet writer decision**.

Numeric wire fields remain exact decimal strings. Event reserves are not account
state. Event `fee=8921826` is not the fee-recipient balance increment `4460913`;
the buyback field is also `4460913`. Do not add these into an invented net fee.
Cashback is event-reported `2817419`; other instructions, tips and transaction
fees affect balances. No instruction-level net proceeds are inferred.

The event's quote mint is 32 zero bytes. They remain raw bytes with **UNKNOWN
economic identity**, not a default SOL/WSOL mint. The Token-2022 TransferChecked
decimals byte is not promoted to canonical mint decimals by this component.
Names, ticker, launch date, quote/base decimals, executable price, activation
range and account-write state remain unknown/not established. The transaction's
archived OK status alone does not prove any of those facts. Signature cryptographic
verification and root-to-slot membership are not established by this reader.

The [26-byte buy diagnosis](B5_PUMP_BUY_SOURCE_BINDING.md) is preserved separately:
its extra byte is still unexplained, full-input rejection remains intact, and
neither B3 parser behavior nor the old evidence matrix is changed.

## Offline checks and scope

Authentic sections are reused byte-for-byte; synthetic boundary/negative cases
are explicitly labelled. Tests cover full-input bounds, integer limits, Borsh
corruption, malformed and duplicate event context, all account positions, V0
privileges, failed/unknown status, source identity, provenance and deterministic
JSON/HTML. The synthetic native-run harness keeps Fixture receipts and tests
Raw → Bronze → Silver binding without forging an authentic run.

Only `borsh =1.8.0` becomes an additional **direct** dependency to reuse the
existing bounded TradeEvent wire type. It was already in Cargo.lock; the resolved
packages, features, licenses and build scripts are unchanged. No package download,
network client, provider, signer, system install or global toolchain change.
The existing per-slot/selection output cap now includes derived Silver bytes;
no acquisition or reader budget is increased.

The subsequent local result receipt records full 3,137/725 regressions, two
deterministic executions, artifact hashes, actual browser output and final gates.
GitHub CI and ordinary Roadmap Sync must run only after access and publication
are separately permitted. This local development does not contact GitHub,
change PR #117, merge, close issues or promote Project evidence.
