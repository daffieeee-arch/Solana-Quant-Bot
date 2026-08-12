# Solana Paper Scanner

A **paper-only** Solana momentum scanner. It does not contain wallet code, seed-phrase support, transaction signing, or real order submission.

## Safety properties

- `MODE` must be exactly `paper`; all other modes fail at startup.
- The scanner only makes public GET/read-only market-data requests to DexScreener.
- It runs only in `MODE=paper`; it has no wallet, signing, transaction-submission, or real-order code.
- Paper state is persisted atomically in the configured `DATA_DIR` and event history is stored in `events.ndjson`.
- The dashboard is read-only and should only be exposed on a trusted LAN with its local dashboard token.
- DexScreener's public latest-token feed is **best effort**, not complete Solana launch coverage and not investment advice.

## Run a one-cycle demo (no Docker / no writes)

```bash
cd /opt/data/solana-paper-scanner
set -a; . ./.env.example; set +a
MAX_CYCLES=1 npm run dev
```

Expected output is one JSON object. It may have zero entries: the safety gates deliberately reject candidates with unavailable/insufficient market data.

## Test and build

```bash
npm test
npm run build
```

## Docker build

```bash
docker build -t solana-paper-scanner:demo .
docker run --rm --env-file .env.example -e MAX_CYCLES=1 solana-paper-scanner:demo
```

No wallet, private key, seed phrase, or real funds are needed or accepted.

## Current limitations / next decisions

1. Add persistent *paper-only* state after the output format has been reviewed.
2. Add Telegram notifications using a dedicated bot token only if desired.
3. For meaningful real-time launch coverage, choose a paid/on-chain provider (Helius or Birdeye) and add a risk provider (for example Rugcheck) before considering any execution work.
4. Any future live-trading function requires separate approval, independent review, a new hot wallet with a limited balance, a kill switch, and a fresh implementation scope.
