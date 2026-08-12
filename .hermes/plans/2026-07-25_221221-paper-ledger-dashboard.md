# Paper Ledger, Exit Engine, and Dashboard Implementation Plan

> **For Hermes:** Implement incrementally with strict TDD. Do not add wallet, signing, transaction-submission, or live-order code.

**Goal:** Make the running Solana scanner observable and restart-safe by persisting paper state/events, correctly processing exits, and exposing a LAN-only paper dashboard after the user approves its network exposure.

**Architecture:** Keep Node/TypeScript and public DexScreener data. Add a standard-library JSON snapshot plus append-only NDJSON event ledger in a narrowly writable `data/` mount. Update open positions from current token data before evaluating new candidates. Serve a read-only status/dashboard view only after an explicit port/auth decision.

**Tech stack:** Node 22, TypeScript, Vitest, Node `fs/promises` and `http`; no wallet libraries or database dependency in the initial increment.

---

## Current findings

- The deployed Custom App is `RUNNING`, has one container, no exposed ports, and a read-only project bind mount.
- The current scanner can make a `paper_entry`, but `evaluateOpenPosition()` is not called from `Scanner.runOnce()`. Consequently, exit logic exists but is currently unreachable from the runtime loop. This is a correctness blocker for P&L reporting.
- Portfolio state is memory-only and `DATA_DIR=/tmp`; state and trade history disappear after a container restart.
- Current DexScreener discovery only looks at recent profiles. It must additionally fetch current data for every open mint in order to evaluate stop-loss, trailing-stop, max-hold, and mark-to-market safely.

## Safety boundaries

- `MODE=paper` remains mandatory.
- No wallet, keypair, private key, seed phrase, transaction signing, RPC send, or exchange order code.
- New data mount is limited to `/mnt/fastdisk/ai/hermes/solana-paper-scanner/data` and is the only writable app path.
- Dashboard defaults to no exposed port until the user chooses a LAN-only port/authentication policy.
- The app stays non-privileged, without Docker socket, host network, or broad host mounts.

## Task 1: Define persisted paper-state and event schemas

**Files**
- Create: `src/ledger.ts`
- Create: `tests/ledger.test.ts`

**TDD steps**
1. Write a failing test that `loadOrCreateLedger()` returns a fresh portfolio when no snapshot exists.
2. Run `npm test -- tests/ledger.test.ts`; expect failure because module/function is absent.
3. Implement the minimum atomic snapshot read/write functionality with a versioned JSON envelope.
4. Re-run the focused test, then full `npm test`.
5. Add one failing test proving `recordEvent()` appends a JSON line without overwriting earlier events; implement and re-run.

**Schema**
```ts
type PaperLedger = {
  schemaVersion: 1;
  portfolio: Portfolio;
  events: PaperTradeEvent[]; // bounded recent in snapshot; complete history in events.ndjson
  updatedAt: string;
};
```
Store `state.json` atomically using a same-directory temporary file and rename. Store audit-friendly entries in `events.ndjson`.

## Task 2: Fetch and evaluate open positions before new entries

**Files**
- Modify: `src/providers/dexscreener.ts`
- Modify: `src/scanner.ts`
- Modify: `tests/dexscreener.test.ts`
- Modify: `tests/scanner.test.ts`

**TDD steps**
1. Write a failing provider test for `fetchSnapshotsForMints(mints)` that produces a current Solana snapshot for an open mint.
2. Verify RED; implement only token lookup/mapping; verify GREEN.
3. Write a failing scanner test: an existing position whose fetched price reaches stop-loss produces an `exit` decision and removes the position.
4. Verify RED; update scanner to fetch current snapshots for `portfolio.positions`, call `evaluateOpenPosition`, persist the changed portfolio, then consider new candidates.
5. Add tests for max-hold and trailing-stop exits, including an unavailable quote resulting in a safe hold/no invented price.
6. Run the full suite and TypeScript build.

**Acceptance conditions**
- No exit is inferred without a valid current price.
- Existing positions are evaluated before any new entry.
- New-entry discovery does not re-open an already-held mint.

## Task 3: Persist entries, exits, decisions, and snapshots

**Files**
- Modify: `src/scanner.ts`
- Modify: `src/main.ts`
- Modify: `src/ledger.ts`
- Create: `tests/runtime-persistence.test.ts`

**TDD steps**
1. Write a failing test proving a paper entry is persisted and can be loaded into a fresh scanner instance.
2. Implement event conversion for `paper_entry`, `paper_exit`, `rejected`, and scanner-cycle health events.
3. Write a failing test proving an exit persists realized P&L and position closure across a simulated restart.
4. Implement minimum runtime lifecycle: load ledger at start; save after every completed cycle; log only redacted/safe structured events.
5. Run focused tests, full tests, and `npm run build`.

**Metrics to calculate from persisted data**
- realized P&L in SOL;
- unrealized P&L for each open position, using the latest valid observed price;
- total entries/exits, win rate, average realized P&L;
- win/loss streak and current daily realized loss;
- scanner last-success time and last error (message only).

## Task 4: Build a read-only dashboard API and HTML view

**Files**
- Create: `src/dashboard.ts`
- Create: `src/dashboard-view.ts`
- Create: `tests/dashboard.test.ts`
- Modify: `src/main.ts`

**TDD steps**
1. Write a failing test that `/api/status` returns only paper-mode, health, positions, P&L summary, and recent safe decisions/events.
2. Implement a dependency-free Node `http` server with `/healthz`, `/api/status`, and `/`.
3. Write a failing test that the rendered HTML labels the app `PAPER ONLY` and includes no action/order controls.
4. Implement a minimal responsive HTML table/cards view; no external scripts, telemetry, or CDN dependencies.
5. Keep the dashboard read-only: no POST routes and no configuration-changing endpoints.
6. Run tests, build, and a local HTTP smoke test.

## Task 5: Harden runtime behavior and observability

**Files**
- Modify: `src/config.ts`
- Modify: `src/main.ts`
- Modify: `tests/config.test.ts`
- Create: `tests/health.test.ts`

**TDD steps**
1. Add config tests for a dashboard enable flag, port, and event retention limit; defaults must be conservative.
2. Add a health model that marks the scanner degraded after consecutive provider failures but continues retrying on normal scan intervals.
3. Test graceful shutdown: SIGTERM saves the most recent ledger before exit.
4. Test bounded event retention in the dashboard snapshot while `events.ndjson` preserves the full audit history.

## Task 6: Prepare the secure TrueNAS redeploy manifest

**Files**
- Modify: `truenas-custom-app.yaml`
- Modify: `README.md`

**Changes**
- Replace `DATA_DIR=/tmp` with `/data`.
- Add only the dedicated writable host bind mount:
  `/mnt/fastdisk/ai/hermes/solana-paper-scanner/data` → `/data`.
- Keep the project source mount read-only.
- If and only if the user approves LAN dashboard access, publish the chosen dashboard port only to the LAN-management address, never `0.0.0.0` by default.
- Preserve `read_only`, dropped capabilities, `no-new-privileges`, resource limits, and paper-only environment.

**Verification**
1. Validate manifest safety constraints and Compose syntax where available.
2. Use the existing pinned WSS TrueNAS API only after a read-only app-status check.
3. Redeploy only `solana-paper-scanner` after user approval.
4. Confirm `RUNNING`, paper-only log health, persistent `/data` files, restart restoration, and dashboard behavior.

## Task 7: Add operator documentation

**Files**
- Modify: `README.md`

Document:
- paper-only disclaimer and absence of real execution;
- dashboard URL/access boundary (if enabled);
- how to interpret P&L and why it is simulated;
- how to export/inspect `events.ndjson`;
- restart/recovery behavior;
- known market-data limits: public DexScreener is best-effort and not a low-latency all-launch feed;
- explicit warning that results are not a promise of profit and should not be used for live trading without extensive validation.

## Deployment decision required

The code and persistence can be built locally without external exposure. Before I change the live TrueNAS App manifest, choose the dashboard access policy:

1. LAN-only dashboard on a dedicated port with a dashboard login/token;
2. LAN-only dashboard without login (less safe; anyone on the LAN could view paper metrics);
3. no dashboard port; keep a local ledger and deliver summaries via logs/Telegram only.

## Full verification checklist

```bash
npm test
npm run build
node dist/main.js  # controlled single-cycle local smoke test
```

Then, after explicit deployment approval:

- pinned-WSS TrueNAS `app.get_instance` confirms `RUNNING`;
- dashboard reports `mode: paper`;
- paper state survives an app restart;
- an injected/controlled price test produces a persisted exit event and realized P&L;
- no codebase scan finds wallet/transaction/private-key primitives.
