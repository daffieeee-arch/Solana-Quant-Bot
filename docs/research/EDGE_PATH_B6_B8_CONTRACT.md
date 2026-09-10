# Edge path contract — B6 → B8

> **Document status: ACTIVE.** Preregistered progression for Observatory, data
> sufficiency and the first falsifiable baseline. No edge, profitability, or
> Research Ready claim is established by this document.

## Ordering

| Delivery | Issue | Observable result | Blocked until |
|---|---|---|---|
| B6 Research Observatory MVP | #85 | Browser Ingestion/Data Quality + Token Lifecycle Replay on **authentic** data | B5 static authentic report exists |
| B7 Scale + Cohort Explorer | #86 | Cohort distributions + explicit data-sufficiency verdict | B6 authentic visibility; preregistered expansion plan |
| B8 PIT Gold v0 + baseline | #87 | Walk-forward baseline **or** `INSUFFICIENT_SAMPLE` **or** `FALSIFIED` | B7 sufficiency known; untouched holdout registered |

## Hard rules

1. Synthetic/fixture cockpit output is not Observatory evidence.
2. `ENGINEERING_VALIDATION_ONLY` slices never support edge claims.
3. Features may not use `acquired_at` / `processed_at` as historical inputs.
4. Atomic transaction packages only; no same-transaction reaction/fill.
5. `execution_opportunity_at` stays nullable/`UNAVAILABLE` without independent
   evidence; the next historical transaction is not a default fill.
6. Missing evidence is never mapped to zero/healthy/profitable.
7. Allowed baseline family first: deterministic rules and descriptive statistics,
   then simple models — no deep learning or autonomous LLM trade signals.

## Valid terminal labels for B8

- `EDGE_CANDIDATE` — only with named dataset/model manifests, costs, uncertainty
  and execution-evidence limits;
- `FALSIFIED` — preregistered hypothesis fails its predeclared threshold on
  admissible PIT evidence;
- `INSUFFICIENT_SAMPLE` — scientifically valid stop; not a bot failure.

Profitability is never a milestone label.

## Current status

All three deliveries remain backlog relative to unfinished B4/B5. No strategy UI
or legacy paper tuning should run in parallel to skip these gates.
