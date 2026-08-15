# Pump instruction source-classificatie (OFFLINE, 2026-08-15)

De Pump.fun parser herkent trade-instructies uit TWEE bronnen. Deze worden
expliciet gescheiden — OBSERVED bytes worden NIET automatisch als officiële
Pump-instructies behandeld.

## OFFICIAL_IDL_VARIANT
Gepinde officiële IDL: `pump-fun/pump-public-docs idl/pump.json` @ commit
`3c6721a67c0b206b` (sha256 b90bc471…), v0.1.0. Discriminator = SHA256("global:<name>")[0:8].

| variant | disc (hex) | accts |
|---|---|---|
| buy | 66063d1201daebea | 16 |
| sell | 33e685a4017f83ad | 14 |
| buy_v2 | b817ee6167c5d33d | 27 |
| sell_v2 | 5df6823ce7e940b2 | 26 |
| buy_exact_quote_in_v2 | c2ab1c46684d5b2f | 27 |
| buy_exact_sol_in | 38fc74089edfcd5f | 16 |

## 2. OBSERVED_RUNTIME_DISPATCH

Waargenomen op mainnet (2026-08-15, publieke RPC-dumps; bewijs in
`/opt/data/real-trades.json` + reviewer-dump deleg_0c403372). De live binary
dispatcht op een CUSTOM dispatcher — NÍET sha256("global:<name>"). Deze bytes
worden alleen als Pump geaccepteerd wanneer ALLE structuur-conditionals slagen:

- program-id exact matcht (6EF8rrect…)
- accountlayout voldoende bewijs (mint à curve met derived-PDA in de set)
- mint geldig (32-byte base58)
- bonding-curve afgeleid met officiële `@solana/web3.js findProgramAddressSync`
- aanwezige curve == derived-PDA exact
- provenancefixture aanwezig (live_sell_mainnet / live_buy_mainnet)

| disc (bytes) | notatie | familie |
|---|---|---|
| e6345c8dd8b14540 | observed_sell | sell (bewezen: sig 4RNa86Ef, a2=mint a3=curve=derived-PDA) |
| 0094d0da1f435eb0 | observed_buy | buy (via FLASHX8-router) |
| 1e7435e21cba7f11 | observed_buy_v2 | buy_v2 |
| e822865bc7d49d0e | observed_buy_exact_sol_in | buy_exact_sol_in |

## 3. Fail-closed

- Onbekende discriminator → undefined (geen toekenning)
- Niet-Pump program-id → undefined
- mint/curve mismatch (curve ≠ derived-PDA, niet in accounts) → undefined
- invalid accountlayout (<2 geldige 32-byte accounts) → undefined
- Deze bytes zijn GEEN vervanging voor de IDL; ze zijn een OBSERVED-fallback.