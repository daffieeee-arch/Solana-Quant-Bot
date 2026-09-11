# Recorded Pump buy: account correspondence and unresolved suffix

> **Document status: ACTIVE — bounded offline B5 diagnosis, not Silver admission.**
> B4/#83 remains In Progress / ACTIVE NOW / Unproven; B5/#84 remains Backlog /
> NEXT / Unproven. No acquisition, lease change or delivery promotion.

## Result and evidence boundary

The preserved slot `422496004`, transaction-in-slot `142`, outer instruction
`3` contains a **26-byte** Pump buy-discriminator instruction and **18 accounts**.
The [existing authentic CAR section](../../rust/of1-bronze-decoder/tests/fixtures/authentic-pump-sections.json)
is reused byte-for-byte. Its Raw publication SHA-256 is
`4487ab047ab4d4d7267a218e69877e4222178800fe5cba51547269a3772156db`.
This is engineering input, not an outcome-independent research sample.

The account extension is source-explained; the extra instruction byte is not.
The new Rust diagnostic therefore produces **no Silver**. It keeps the original
full-input `UNEXPECTED_TRAILING_BYTES` rejection and all bytes, alongside positive
address/context checks. This does not reopen or weaken the bounded B3 contract.

| Byte offsets (zero-based, end-exclusive) | Source-bound interpretation | Actual bytes / raw value |
|---|---|---|
| `[0,8)` | `buy` discriminator | `66063d1201daebea` |
| `[8,16)` | amount, little-endian u64 | `933872054023` |
| `[16,24)` | max_sol_cost, little-endian u64 | `27000000` |
| `[24,25)` | `OptionBool(bool)` field | `01`, true |
| `[25,26)` | **not specified by the selected schema** | `01`, meaning UNKNOWN |

Full bytes: `66063d1201daebea07af1c6fd9000000c0fc9b01000000000101`.
Instruction SHA-256:
`8fb2c1427bf529c080cd5882b2bc1c48f523f3173cf6ea52a705071084216874`.
The explicitly labelled prefix diagnostic is not a successful full instruction
decode. No suffix is discarded, converted or used to manufacture a candidate.

## Official source findings

All identities, receipt times, inspected history and derived account rules are
in [pump-buy-evidence.json](../../rust/of1-bronze-decoder/sources/pump-buy-evidence.json).
The complete original IDL remains outside Git because the pinned documentation
repository has no license file; only factual schema identities/rules are retained
here. Source documentation is not historical activation evidence.

1. The existing [official IDL](https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/idl/pump.json)
   specifies two u64 fields and `OptionBool`, a one-field bool struct, totaling
   25 instruction bytes including the discriminator. It is **not** evidence for
   Borsh `Option<bool>` with an additional presence tag. Commit
   `9c82f61cb711b044a17f770ab8ce9f9bdf78f333`, blob
   `062e66f032bb9f295353b573be3400070bd55e5b`, SHA-256
   `b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49`.
   All 15 returned revisions for this IDL path were inspected: earlier versions
   have two u64 arguments; versions with `track_volume` use the single-bool
   struct. None supplies a 26-byte schema. This is not a survey of every deployed
   historical Pump binary.
2. The same pin's [breaking fee-recipient document](https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/docs/BREAKING_FEE_RECIPIENT.md)
   explains 18 buy accounts: the two final remaining accounts are
   `bonding-curve-v2` and a writable buyback fee recipient. File SHA-256
   `8b05e0906eaf1746508b0b2f909ac7c344ce7be33a6564d595335c9fda399868`.
   The observed final address is in the pinned [recipient list](https://github.com/pump-fun/pump-public-docs/blob/9c82f61cb711b044a17f770ab8ce9f9bdf78f333/docs/FEE_RECIPIENTS.md),
   SHA-256 `9d66921d571a6129e5ef72504b60ae63888558e752eeb4a329b962f5cd229192`.
   Remaining accounts are independent of instruction payload bytes; they do not
   explain or authorize the extra byte.
3. The official README-linked `@pump-fun/pump-sdk` source package `1.36.0`
   calls `buy(amount, solAmount, {0:true})` and appends those two accounts.
   Its `src/pda.ts` defines the `bonding-curve-v2` seed. Exact tarball SHA-256
   `e3e612d9bf0be5f1a443d321aee51b044b5e98408170c3eb8baebf6346f569aa`;
   SDK/PDA/IDL file hashes and verified registry SHA-512 integrity are recorded.
   The registry declares git head `1c9e0343fbc28158c071bcf03237b047e8cc32d9`,
   but that GitHub commit was not retrievable (404): it is **registry-declared,
   not independently Git-verified**. The MIT-declared package was source-inspected,
   not installed or executed. It does not supply a 26-byte serialization rule.
4. A [generic Anchor v0.31.1 generated handler](https://github.com/otter-sec/anchor/blob/47284f8f0b9844c6b83234aa90f556bad00e12ed/lang/syn/src/codegen/program/handlers.rs)
   deserializes from a slice without a subsequent exhaustion check. This is a
   possible compatibility mechanism, **not proof of Pump's deployed handler or
   the suffix's meaning**. Commit `47284f8f0b9844c6b83234aa90f556bad00e12ed`,
   file SHA-256 `2619507455bbffd627176ed57c8295d03d799fb1bc70a54a03b3c10c9a5288ba`.

## What the Rust diagnostic checks

- All 18 indexed addresses, the message's actual static/loaded key counts and
  required signer/writable privileges. A readonly IDL role can be writable in the
  compiled message; minimum privileges are not exact per-CPI permissions.
- Ten local PDA/ATA computations, fixed program addresses, event mint/user/fee
  recipient correspondence and documented buyback-recipient membership.
  The [separate sealed web3 address reference](../../rust/of1-bronze-decoder/tests/fixtures/pump-buy-account-reference.json)
  agrees for the authentic case. This is a computational reference, not new
  protocol authority.
- The `associated_user` ATA is SDK-address correspondence, not proof that every
  valid on-chain call must use an ATA. `creator_vault` uses the **event-reported**
  creator; no curve account was read. Addresses do not prove account contents,
  mint ownership, global configuration at the slot or economic identity.
- Exactly one source-tagged event in the same outer instruction, fully consumed
  by the existing B3 event parser; recorded stack height 2 and direct Pump parent;
  the event-authority account reference; matching mint, user, token amount and
  volume flag. Duplicate, missing, unsupported or context-invalid events remain
  unavailable with a reason. The actual event is inner order 7, 366 bytes, SHA-256
  `b42b7ea3175d3b53e8c71e1f158f35629be8b79f064b25a1eae96c71fab019f6`.
- Full instruction exhaustion stays false despite those positive observations.
  The diagnostic never calls a candidate resolver or supplies a compatible count.
  It does not silently reuse a successful prefix as a selected candidate.

The transaction's archived status is OK, but a layout/context match is not a
canonical committed-state fact. Event reserves remain event-reported, not account
state. Quote mint identity, quote/base decimals, launch date, executable price,
historical activation and trading edge remain unknown/unproven. Root-to-slot
membership remains UNAVAILABLE.

## Reproduce and review

Use the existing pinned toolchain and the same offline reader command documented
in [the native three-slot report](B5_MULTISLOT_PUMP_SEARCH.md), with a **new** output
directory. The command verifies original Raw/receipts/plans without the writer,
approval clocks or provider capability. The quality HTML now includes a readable
buy section, full bytes, all account rows, event context and the precise blocker.
All original reports, source files and acquisition binaries remain unchanged.

The sole new direct dependency is `solana-pubkey =4.3.0`, defaults disabled,
`curve25519` enabled, for local address derivation. The exact twelve additional
locked support packages, licenses, enabled features and build-script hashes are
in [dependency-review.json](../../rust/of1-bronze-decoder/dependency-review.json).
No RPC/signing/HTTP dependency, new system package or runtime network capability
is introduced. The curve build script only selects a local compiler/backend.

Tests retain the original authentic sections and distinguish synthetic account,
privilege, suffix, truncation, event-field and invocation-context perturbations.
Both original 725- and 3,137-transaction inputs remain end-to-end regressions;
their execution receipts are separate from source/fixture evidence.

**Smallest missing proof:** a Pump-authoritative deployed deserializer or explicit
compatibility rule explaining byte offset 25 and bounding its allowed values and
semantics. Generic Anchor behavior and an OK archived status do not establish
that rule. No extra download, parser relaxation, forced Silver, universal version
framework or acquisition approval follows from this diagnosis.
