# lab/ui/web — Solana Lab web app

Read-only front end for `lab/ui/server.py` (paper trading and research; nothing is sent or signed).
Design: `ops/design/ui-v1` (exchange style, dark only).

## Stack and licences

| package | use | licence |
|---|---|---|
| react, react-dom 19 | UI | MIT |
| vite, @vitejs/plugin-react | build | MIT |
| tailwindcss 4, @tailwindcss/vite | styles | MIT |
| typescript | types | Apache-2.0 |
| ag-grid-community, ag-grid-react | tables | MIT |
| echarts | charts | Apache-2.0 |
| lightweight-charts | price charts | Apache-2.0 |
| react-grid-layout | panels | MIT |
| @radix-ui/react-dialog, -toggle-group, -slot | shadcn/ui component base (Meer sheet, segmented control) | MIT |
| class-variance-authority | shadcn/ui variants | Apache-2.0 |
| clsx, tailwind-merge | `cn()` helper | MIT |
| @number-flow/react | rolling numbers | MIT |
| motion | transitions, rows sliding in | MIT |
| @fontsource-variable/manrope, @fontsource-variable/jetbrains-mono | self-hosted fonts (no Google requests) | package MIT, fonts **OFL-1.1** |

The fonts themselves are only published under the SIL Open Font License 1.1, which allows bundling and
self-hosting. `lightningcss` (a Vite/Tailwind build dependency, not shipped) is MPL-2.0.

## Build

Builds run in a bounded scope and write outside the repo:

```bash
systemd-run --user --scope -p MemoryMax=1500M -p CPUQuota=100% -- nice -n 10 \
  node_modules/.bin/vite build --outDir "$LAB_DATA_ROOT/ui-cache/web.new" --emptyOutDir
```

`npm install` counts as heavy: run it under `heavy.lock` (see the repo's AGENTS.md).

## iPhone

`index.html` has a web-app manifest, an apple-touch-icon and `apple-mobile-web-app-capable`, so "Zet op
beginscherm" opens the app standalone. A service worker (offline cache, faster start) needs HTTPS; the app is
served over plain HTTP on the tailnet address. Serving it over HTTPS would need, for example, `tailscale serve`
with the tailnet's certificate in front of port 8501. That changes shared Tailscale settings, so it is the
owner's or coordinator's decision; this app does not change it.

## Live data: files the app reads

The server (`lab/ui/server.py`, `lab/ui/paper.py`) only reads files; it never calls a node, Triton or
DexScreener and never touches a key. Pages refresh on a Server-Sent Events tick (`/api/stream`, sent when the
journal or the recorder writes) and fall back to polling every 30 s.

| file | written by | used for |
|---|---|---|
| `<journal>/journal.sqlite` (+ `-wal`) | paper engine (`lab/paper`) | Paper, Wallet (paper part), markers |
| `<journal>/wallet.json` | live engine (later) | Wallet: on-chain balance and transactions |
| `<recorder>/budget.json`, `<recorder>/segments/*/_segment.json`, `arrivals/*.parquet` | recorder (`lab/recorder`) | Triton, Paper's Triton card |
| `<recorder>/reconcile.json` | `python -m paper.reconcile <recorder> --out <recorder>/reconcile.json` | Triton: check against OF1 |
| `<data>/ui-cache/bot-wallet.json` | owner / coordinator | the bot wallet's public address |
| `<data>/ui-cache/triton.json` | owner, by hand | Triton prepaid balance |
| `lab/paper/risk.yaml` of the engine's checkout (`LAB_PAPER_RISK`) | owner | daily loss limit, kill switch |

`<journal>` is the newest `<data>/journal/*/journal/` (or `LAB_PAPER_JOURNAL`); `<recorder>` is
`<data>/recorder` (or `LAB_RECORDER_ROOT`). Without a recorder or `wallet.json` the pages show the samples in
`lab/ui/paper_sample/`, marked "voorbeelddata". Slots of the hold-out window (452304000–454463999) are dropped.

### `wallet.json` (live engine)

Written atomically (temp file + rename) after every change, public data only:

```json
{
  "address": "<bot wallet, base58>",
  "slot": 457920450,
  "time_unix": 1791802859,
  "balance_lamports": 10380000000,
  "txs": [
    {"signature": "<base58>", "slot": 457920401, "time_unix": 1791802840,
     "kind": "buy | sell | other", "mint": "<token mint or null>", "symbol": "PEPPER or null",
     "amount_lamports": -500000000, "fee_lamports": 5000, "tip_lamports": 1000000,
     "status": "landed | failed | dropped", "landed_slot_delta": 1, "note": "free text or null"}
  ]
}
```

- `amount_lamports`: the SOL change of the wallet by this transaction, signed (a buy is negative).
- `landed_slot_delta`: landing slot minus the decision slot; null when it did not land.
- `txs`: newest first, at most 50 are shown.

The paper engine (lab/paper, #183) writes this file with `"mode": "paper"`, `"address": null` and
`"status": "paper"` per transaction: the Wallet page then shows it as "paper (gesimuleerd)", never as an
on-chain wallet, and leaves out the address and Solscan link.

### `positions` marks (paper engine)

Open positions carry `mark_proceeds`, `mark_slot` and `mark_pnl` (lamports). The Paper tab shows `mark_pnl`
(proceeds after exit costs minus the entry cost including fees, the same definition as `exits.pnl`), so an
open position's P&L does not jump when it closes.

### `bot-wallet.json` and `triton.json`

```json
{"address": "<bot wallet public address>"}
{"balance_usd": 18.40, "as_of": "2026-10-12"}
```
