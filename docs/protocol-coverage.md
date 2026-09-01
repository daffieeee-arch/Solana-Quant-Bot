# Protocol coverage matrix — MarketIdentity (OFFLINE, 2026-08-15)

> **Document status: HISTORICAL.** Legacy fixture coverage only; not active V2 protocol truth. See [`HANDOFF_V2.md`](HANDOFF_V2.md).

Status-classificatie: SUPPORTED_AND_TESTED / PARSER_EXISTS_NOT_WIRED /
IDENTITY_INCOMPLETE / FIXTURE_MISSING / NOT_YET_SUPPORTED

| # | protocol | programId (prefix) | decoder | MarketIdentity | canonical pool/market fields | decimals source | mark/exit | fixture | status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Pump.fun | 6EF8rrecth | parsePumpSwap (structureel) ✓ | pump_bonding_curve (PDA) ✓ | bonding-curve PDA (["bonding-curve", mint]) | poolDepth/EVM ✓ | ✓ | ✓ (pump-parser.test) | SUPPORTED_AND_TESTED |
| 2 | PumpSwap | pAMMBay6c | parseGenericSwap | generic (geen curve) | — (pool nodig) | poolDepth | via Titan | ✗ | IDENTITY_INCOMPLETE |
| 3 | Raydium AMMv4 | 675kPX9M | onRaydiumUpdate | amm_cpmm (marketId≠lpMint guard ✓) | pool-account NIET in decode (alleen lpMint/vaults) | poolDepth (RPC) | Titan | raydium-identity.test | IDENTITY_INCOMPLETE |
| 4 | Raydium CPMM | CPMMoo8L | onCpmmUpdate | amm_cpmm | poolState NIET in decode (vaultA/B +lpMint) | poolDepth (RPC) | Titan | raydium-identity.test | IDENTITY_INCOMPLETE |
| 5 | Raydium CLMM | CAMMCzo5 | onGenericLaunchUpdate | generic | — | — | — | ✗ | NOT_YET_SUPPORTED |
| 6 | Meteora | cpamdpZCG | onGenericLaunchUpdate | generic | — | — | — | ✗ | IDENTITY_INCOMPLETE |
| 7 | Orca Whirlpool | whirLbMi | onGenericLaunchUpdate | generic | — | — | — | ✗ | IDENTITY_INCOMPLETE |
| 8 | Moonshot | MoonCVVN | onGenericLaunchUpdate | generic | — | — | — | ✗ | IDENTITY_INCOMPLETE |
| 9 | Jupiter | JUP6LkbZ | onGenericLaunchUpdate | generic | — | — | — | ✗ | IDENTITY_INCOMPLETE |
| 10 | Solana native | 111111 | — | — | — | — | — | — | NOT_YET_SUPPORTED |

## Legenda
- SUPPORTED_AND_TESTED: decoder + identity + offline fixture + tests groen
- PARSER_EXISTS_NOT_WIRED: decoder bestaat maar niet aan identity gekoppeld
- IDENTITY_INCOMPLETE: decoder levert niet de canonical pool/market-account
- FIXTURE_MISSING: geen recorded-vereenvoudigde fixture beschikbaar
- NOT_YET_SUPPORTED: geen decoder implementatie

## Geometry-notes (voor latere ronde, OFFLINE)
- Pump: bonding-curve PDA deterministisch (["bonding-curve", mint]).
- AMMv4/CPMM: canonical pool-state-account uit de swap-instructie-accounts
  (eerste account van de swap-inv) — vereist decoder-uitbreiding (niet deze ronde).
- CLMM: poolState + tickArray => genisczen wordt trait lange uitbreiding.
