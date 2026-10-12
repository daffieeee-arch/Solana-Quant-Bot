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
