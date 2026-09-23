# B4 and B5 — accepted bounded engineering contracts

> **Document status: ACTIVE acceptance decision.** Explicitly accepted by the project owner on **2026-09-23**. This dated decision resolves the previously open acceptance interpretations; it does not rewrite original issue criteria, execution reports or dataset identities.

## Decision and limits

[B4/#83](https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/83) is technically accepted for the bounded acquisition contract: approved plans and source identity, request/budget accounting, integrity and coverage of the acquired ranges, and clean reopening under the same plan identity. Authentic acquisition and clean metadata-to-payload continuation are distinct from the separately accepted **fixture** crash/retry evidence. No authentic provider crash, power-loss experiment or new acquisition is claimed. During B4, transaction/Pump counts remain `UNAVAILABLE_NOT_DECODED_IN_B4`; domain counts are supplied by subsequent offline B5 decoding. This is the explicitly accepted interpretation of B4's progress/count requirement.

[B5/#84](https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/84) is technically accepted for the delivered native Rust Raw → Bronze → Silver route, definitive Rust Arrow/Parquet writer, receipt/source/manifests, atomic packages, exact integers, replay and native delivery-order parity, and the static quality/lifecycle reports. Broader schemas and Pump variants are not newly admitted.

The owner explicitly accepted **proposal A** for the lifecycle criterion: the authentic fragment for mint `4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump` contains **15 packages, five admitted trade facts (two buys / three sells), and 58 balance observations**, with missing lifecycle phases visibly retained. It satisfies the limited engineering requirement. The original phrases “real token lifecycle” and “explicit gaps” did not prescribe a mandatory observed creation/migration phase-set or complete lifetime. The original architecture did name creation, curve progress and completion/migration as display subjects; they remain visible as unproven, not removed from the interface contract. This is an explicit acceptance interpretation, not a claim that these phases occurred or were decoded.

**Research Ready remains false.** Creation, completion, migration, full lifetime, historical program activation and actual CPI signer/writable privileges remain separately unproven. First/last observations do not establish launch/end. Event reserves remain recorded event fields, not historical account state or executable liquidity. Bronze completeness, Pump instruction coverage, Silver admission and lifecycle coverage remain separate measures.

The original three-slot `[422669516,422669519)` pilot remains `RESEARCH_SAMPLING`. The sixteen `[422669519,422669535)` slots remain post-hoc descriptive engineering context. The mint selection is itself post-hoc descriptive. The collection retains 21,719 packages, 1,898 failed transactions, **22 Silver facts (15 buys / seven sells)** and **seven rejected Mayhem cases**. Project Evidence `Engineering Validation` describes accepted engineering mechanics; it does not reclassify any dataset or admit a rejected fact.

## Evidence chain

All private relative paths below begin at `/home/chupa/Solana-project/data-old-faithful-one/`. Existing sealed evidence remains unchanged; hashes come from the relevant files/manifests, not recollection. Historical WSL paths remain provenance.

| Evidence | Binding and accepted contribution |
|---|---|
| Original bounded B4/B5 criterion assessment | `governance/b4-b5-acceptance-20260922T235643Z/RESULTAAT.md`, SHA256 `011729e6b8cf8c76b42dbd6c3503a24b8b896db4dec725fc450d995d0e43621f`; authentic versus fixture accounting and original criterion tables |
| [Walking skeleton #132](https://github.com/daffieeee-arch/Solana-Quant-Bot/pull/132) | Merge `1530dd597a145d2b40e13c8beb4d1df2e676bd14`; reviewed head `f23b2549b0494d543c0fd4f312f8fcf4ce80966b`; `governance/b5-raw-bronze-silver-walking-20260922/RESULTAAT.md` and `report-f23b254/manifest.json` (manifest SHA256 `f19472a57be09dd760e8a4b0f1fd6691132f09e3c2a9144a2ec54580ccc6b268`) |
| [Native order parity #133](https://github.com/daffieeee-arch/Solana-Quant-Bot/pull/133) | Merge `9bc6d0560405e3a0156448b418b57105766b0104`; reviewed head `055a001fec601d2556c9621c34abc12545af3ebd`; `governance/b5-native-order-parity-20260923/AFRONDING.md`, SHA256 `3b4bdfdb10d6b717d2600d8a096e0bc9ded0a172c1aa52b83ac8c6135a3384b8`. Canonical/reverse/fixed odd-even input: each 3,224 packages, 223 failures, seven facts, none from failures; equal Bronze/Silver logical hashes |
| Lifecycle proposal A | `governance/b5-lifecycle-acceptance-20260923/RESULTAAT.md`, SHA256 `3ecfbd6e1b12e455d9cbd217a9998946ce41e298c5ade674efe6a3761f383cd3`; `report.json`, SHA256 `9edb126db344f31a78438e8c5f6635dd50e7368ba2f054b575776d8c1d68a8a0`, and `index.html`. Exact source/receipt/instruction bindings, known facts and absent phase evidence |
| Accepted collection and this closeout | Collection SHA256 `39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab`; new closeout evidence under `governance/b4-b5-engineering-acceptance-20260923/`. No dataset replay follows from this status change |

The #132 Bronze logical hash is `78fdb358ffdbcf13fa5a6a0b78c899234e7980c04a246cf2383ed0ecc39269d8`; Silver is `3188dcc5f4d48124d4a5655c26fe8e4d536cf94104e66ad90aa5413cfd327a22`. These belong to that manifest. #133's newer provenance-bound runs have their own hashes: Bronze `e458f48216d43a360d4424c7eb15f26068033e37c4f868d009fbc2039b32e944`, Silver `18486877a4b77e707ecc6ac5ff008cc35c496c2d1d561d50ec757d609db821ec`. Parity is across the three #133 orders, not an assertion that different-version manifests must share hashes.

Earlier reports correctly retain their publication-time `open`/`Unproven` verdicts and missing-acceptance notes. The “creation or migration” wording in #133's closeout is a stronger later interpretation, not a historically mandatory criterion. This dated decision supersedes those delivery-status conclusions only. The original B3 matrix and individual crash/protocol fixtures retain their original evidence levels.

## Ordered administrative closeout

After this decision is recorded through the reviewed documentation PR and required delivery checks, close **#83 first**, verify it, then close **#84** as `completed`. Preserve their original criteria/body text and append this decision. Change only their roadmap metadata and lifecycle state through the existing workflow:

| Issue | Status | V2 Disposition | Evidence | V2 Phase |
|---|---|---|---|---|
| #83 / B4 | `Done` | `SUPERSEDED` | `Engineering Validation` | `2 Authentic Acquisition` |
| #84 / B5 | `Done` | `SUPERSEDED` | `Engineering Validation` | `3 Bronze & Silver` |
| #85 / B6 | `Backlog` | `NEXT` | `Unproven` | `4 Research Observatory` |

`SUPERSEDED` uses the existing completed-milestone routing convention already used by B3; the issues close as **completed**, not `not planned`. It does not assert that a dataset or implementation has been replaced. Do not add a new disposition option or directly edit synchronizer-managed Project fields. Read back trusted-main Roadmap Sync and the actual values after each ordered closure. Completion receipts belong in the new private closeout dossier, not in rewritten historical reports.

B6 is the queued successor, **not started or authorized for implementation by this closeout**. B7/B8 remain `LATER`. Between this completed closeout and a separately approved B6 task there is no concrete `ACTIVE NOW` development item; a next-task proposal is not an in-progress delivery. Program/epic routing does not change that fact.

## One proposed first B6 increment — not started

Build one **read-only interactive mint-package inspector** for the accepted nineteen-slot collection and the already selected mint. Reuse the existing manifest-bound quality summary and mint-timeline JSON/contracts; add a small independent React/TypeScript view and bounded loopback-default GET adapter. Do not extend the frozen legacy dashboard, re-decode data or build the full Observatory.

Visible result: a private browser page shows the collection's coverage/provenance and lets the user step through the **15 atomic packages** in chain order, expand their existing facts/balances/diagnoses and inspect receipt/hash bindings. Creation/completion/migration remain explicitly unavailable/unproven panels.

Acceptance for that future increment:

1. The selected manifest/source binding and counts match existing evidence: 15 packages, five facts, 58 balances, three failed packages without facts; pilot and context remain visibly separate. No duplicated rows or browser-derived domain facts.
2. Runtime-validated read-only contracts preserve exact integer strings, transaction boundaries and ordering. Unknown/unavailable/quarantine states remain distinct; defined state fixtures cover `READY`, `STALE`, `GAP`, `REPLAYING`, `UNAVAILABLE` and `UNPROVEN` without inventing authentic examples.
3. A bounded manifest-registered GET surface defaults to loopback and rejects mutation routes, arbitrary paths/symlinks and non-authorized non-loopback use. No provider, scanner, wallet or control route is reachable; the static report remains usable.
4. Keyboard-accessible previous/next selection and atomic-package expansion have contract tests and authentic browser screenshots. Operational clocks never drive historical order; event values are not labelled executable prices or account state.

This is one partial B6 increment, not full #85 acceptance. No new acquisition, installation, server deployment or implementation starts with this decision.

## Subsequent B6 authorization and integration — 2026-09-23

The table and proposal above retain the acceptance-time decision: that closeout
did not authorize B6. The owner subsequently gave a separate bounded local GO
and then authorized integration of the [first mint inspector](B6_LOCAL_MINT_INSPECTOR.md).
PR #134 merged as `be077022bc163218335d1cc112c03068694819a4`; main CI, CodeQL
and roadmap-sync passed, and #83 was closed/verified before #84. Both read back
Done / SUPERSEDED / Engineering Validation. The temporary Project inconsistency
converged; its original cause remains unknown and both failed runs are preserved.

Current B6 routing is In Progress / ACTIVE NOW / Unproven with only this partial
increment. #85 remains open; B7 is queued NEXT without a development GO. This
later authorization changes neither the accepted B4/B5 interpretation nor
their original criteria, dataset classes or Research Ready=false boundary.
