## Roadmap routing

Roadmap: #

<!--
Keep exactly one non-empty Roadmap: line. Put the concrete bounded delivery
issue first; later issues are secondary context. If Roadmap: is removed,
an exact Closes/Fixes/Resolves line may provide the fallback route. Free issue
references never determine Project metadata inheritance.
-->

- [ ] The PR links or closes the concrete delivery issue.
- [ ] Project metadata/evidence is updated when the implementation changes scope or proof level.

## Change

Describe the bounded change and the invariant it preserves or introduces.

## Evidence

- Tests:
- Replay/fixture/prospective evidence:
- Performance or resource evidence:
- Screenshots for frontend changes:

## Safety

- [ ] Paper/research only; no wallet, signing, transaction submission or live funds.
- [ ] No secret, token, endpoint credential or private data is committed or logged.
- [ ] Triton remains zero-cost/offline unless separately and explicitly approved.
- [ ] Failure paths are fail-closed and represented honestly in the UI/API.

## Review

- [ ] CI is green.
- [ ] Documentation and source-of-truth status are current.
- [ ] Relevant backend/frontend/data contracts were independently reviewed.
