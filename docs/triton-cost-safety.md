# Triton-only network and cost-safety boundary

> **Document status: ACTIVE design; no network run is authorized.** PR 1 performs no Triton/Old Faithful call and spends no provider credit.

## Provider boundary

All active V2 Solana network acquisition, future live data and future execution connectivity use Triton One only.

- Allowed open-source local libraries/protocol specifications do not create an alternate provider path.
- Public Solana RPC, Helius, QuickNode, Alchemy, Birdeye, DexScreener, GeckoTerminal, public Jupiter APIs and other secondary providers are prohibited.
- Future Titan quotes use a Triton `rpcpool` Titan endpoint. Direct third-party Titan traffic is prohibited.
- A failed Triton/OF1 path fails closed; it does not select a fallback provider.

## Capability classes

| Class | Network authority | Evidence authority |
|---|---|---|
| `DOCUMENTATION_ONLY` | bounded official docs/source navigation | context/navigation only; never canonical dataset bytes |
| `ACQUISITION_LEASED` | one exact approved OF1 acquisition run plan | acquired bytes/receipts become evidence only after hash/coverage checks |
| `LIVE_RUNTIME_LEASED` | later exact Triton endpoints with approval/metering/hard stop | prospective shadow/runtime evidence only |
| `NETWORK_ISOLATED_REPLAY` | no network | deterministic transformations/tests over pinned local evidence |

`files.old-faithful.net` is an allowed official Triton OF1 source under `ACQUISITION_LEASED`. Documentation hosts remain `DOCUMENTATION_ONLY` even when operated by Triton.

Hosted Old Faithful gRPC is not assumed available and is not the selected V2 acquisition path. V2 initially uses direct official OF1 access through a pinned Jetstreamer/OF1 path. Any future hosted endpoint requires explicit availability and cost confirmation from Triton.

The public official documentation and consulted read-only documentation MCP do not currently align completely on hosted availability. This is an open decision, not permission to probe an endpoint.

## Default-deny acquisition wrapper

The V2 wrapper must:

- pin the official Jetstreamer/OF1 source commit and exact host/path/index identities;
- deny caller-supplied base URLs, S3 endpoints and backend overrides;
- deny mirrors/public RPC and unpinned redirects;
- use one immutable half-open range and slice class per approved plan;
- meter all requests, retries, headers/body bytes, disk high-water, runtime and concurrency;
- stop on any hard limit without auto-expansion;
- retain exact receipts/content hashes/CIDs and explicit incomplete/abort state;
- resume only after source/plan/code/completed-content identity is revalidated;
- run Raw → Bronze → Silver in `NETWORK_ISOLATED_REPLAY`.

No full epoch is downloaded for the first slice.

## Approval contract

Before any `ACQUISITION_LEASED` or `LIVE_RUNTIME_LEASED` process construction, record and explicitly approve:

1. purpose and evidence class;
2. exact service/host/path/endpoint and redirect policy;
3. source commit/index/sidecar/content identities;
4. epoch/range and selection rationale;
5. maximum requests/retries/bytes/single response;
6. maximum disk/memory/runtime/concurrency and required free space;
7. metering output and hard-stop behavior;
8. resume/abort/quarantine rules;
9. cost/availability confirmation where applicable;
10. operator and approval timestamp.

Every numeric value is a provisional per-run parameter until that plan is approved. The architecture does not contain a universal slot count, byte/request/disk/runtime cap, range or epoch.

## Slice separation

- `ENGINEERING_VALIDATION_ONLY` may deliberately contain known Pump activity and can validate the pipeline/UI. It is permanently excluded from strategy/edge claims.
- `RESEARCH_SAMPLING` is deterministically preregistered independent of outcomes and becomes research-eligible only after every evidence gate.

The candidate `[422506000, 422506128)` is provisional until origin, rationale and class are recorded. It is not an authorized request.

## Later live/shadow controls

A future balance or account change never activates clients. `TRITON_LIVE_ENABLED=false` remains a useful legacy construction guard until replacement, but V2 requires a stronger explicit lease containing:

- maximum duration and request/byte/cost budgets;
- warning and hard-stop thresholds;
- automatic disconnect;
- per-service attribution;
- quote/finality/freshness/capacity metrics;
- restart behavior that does not silently reacquire authority.

Prospective live shadow remains no-order. Signing/submission is a later separate capability boundary.

## Historical cost evidence

Earlier project records report a $125 → $0 prepaid balance and incomplete attribution. That incident motivates fail-closed budgets but is not a current price/cost fact. Do not make a new cost claim without current explicit Triton billing evidence, and do not probe billing/runtime as part of documentation work.
