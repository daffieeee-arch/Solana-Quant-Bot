# PumpSwap exact-in source fixtures

18 already inspected on-chain BuyEvent observations (epoch 1064, no new holdout
inspection). Expected amounts are extracted from `source_event`, **not** from
`corrected` or a Python/Rust implementation. Slot/transaction/instruction position,
pool and signature retain the public source identity. `post_e` is the event's
pre-vault plus virtual quote plus `quote_amount_in_with_lp_fee`; this is effective
pricing reserve, not an assertion about the physical v2 fee vault.

Source outside Git: `lab/research/lp-short-completion-20261025/correction-validation-scoped.json`
under the OF1 root. SHA256: `5007acec3cb89ac0e475843715bc97050b9ad40fb26acffd6bc08270111d304f`.
It binds the source Parquet filenames and original event fields. Existing prior
validation reports exact parity for 50,089 swaps; this fixture preserves the 18
first mismatches rather than vendoring the dataset.

Official reference: [PumpSwap SDK buy.ts](https://github.com/pump-fun/pump-swap-sdk/blob/34903ca54a7110942db9573b2271a6a3e9c9e1d9/src/sdk/buy.ts),
package `@pump-fun/pump-swap-sdk@2.2.0`, commit
`34903ca54a7110942db9573b2271a6a3e9c9e1d9`. Retained archive SHA256:
`b3afc4bcb1cca18c4460c3f41c08be8022bf44b4d6f7de22fac28b7c3edf2a57`.
Consulted 2026-10-25 (retained documentation, no package installation).
`exactQuoteInFees` / `buyQuoteInput` compute fees on the initial floor quotient,
then shave the net while keeping those fees. The recorded events establish
applicability here; an SDK publication alone would not prove historical behavior.

The fixture covers `buy_exact_quote_in` and `buy_exact_quote_in_v2`, zero cashback.
No new claim about cashback, transfer-tax tokens or unobserved variants is made.
