# Shadow → paper → gated live

> **Document status: ACTIVE.** Later-phase gates after Research Ready evidence.
> PAPER / RESEARCH ONLY until a separately reviewed approval changes the
> boundary. This document authorizes no wallet, order, or live funds.

## Phase gates

| Phase | Epic | Entry evidence required | Allowed work | Forbidden |
|---|---|---|---|---|
| 7 Prospective shadow | E4/#77 | B8 Research Ready **or** explicit falsification/insufficiency closeout that still needs prospective measurement | Triton-only no-order quote/finality/latency/capacity/parity measurement | Orders, signing, secondary providers |
| 8 New Rust paper engine | E5/#78 | Shadow evidence for quote/no-fill realism needs | Restart-safe paper state machine with reconciliation | Reusing frozen V1 fill semantics as V2 truth |
| 9 Professional workstation | E6/#79 | Authentic contracts from Observatory + paper | Visualization/workflow only | Browser wallet or trading-domain duplication |
| 10 VPS + gated live | E7/#80 | Paper Proven + separate live safety review | Generic Linux VPS ops under explicit approval | Automatic go-live, key exfiltration, unmanaged funds |

## Safety invariants

- Triton-only network boundary remains.
- No automatic provider activation, top-up, or fallback.
- No secrets in Git, logs, prompts, MCP queries, fixtures, or manifests.
- Live requires a new immutable approval distinct from paper/shadow success.
- Falsified or insufficient research evidence can stop the program honestly
  before Phase 10; that is a valid outcome.

## Current status

Phases 7–10 are **not started**. Immediate work remains B4 payload review
([`B4_PAYLOAD_RUN_DECISION_PACKET.md`](B4_PAYLOAD_RUN_DECISION_PACKET.md)) and,
only after authentic Raw, B5 ([`B5_ENTRY_GATE.md`](B5_ENTRY_GATE.md)).
