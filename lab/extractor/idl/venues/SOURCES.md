# Venue IDLs (venue census, LAB_VENUE_CENSUS=1)

The official Anchor IDLs of the DEX programs the venue census names. They are data only: no SDK, no code from these
projects is built. `tools/gen_venue_ix.py` turns them, with `../pump.json` and `../pump_amm.json`, into
`src/venue_ix.rs` (instruction discriminator, name and the pool account's position). The test
`venue_ix_matches_the_pinned_idls` checks the table against these files and each file's `address` against
`PROGRAM_LABELS`. Fetched 2026-10-21 with an anonymous User-Agent; each file is pinned by its commit or package
version and its sha256.

| file (our label) | program id | source | sha256 |
|---|---|---|---|
| raydium_cpmm.json | CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C | github.com/raydium-io/raydium-idl @ e7e0c96fe77bcf6a020b84a44c47a722aac8e359, raydium_cpmm/raydium_cp_swap.json | 1202f6dc8e1c3216598f2ad5c620b9aa8c64ac584563fafed68125c27fb6df81 |
| raydium_clmm.json | CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK | github.com/raydium-io/raydium-idl @ e7e0c96fe77bcf6a020b84a44c47a722aac8e359, raydium_clmm/raydium_clmm.json | 040a8c4866317fa028be8a81db54325ce6d9b92aeb10582d89992855bbbce5c1 |
| raydium_launchlab.json | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | github.com/raydium-io/raydium-idl @ e7e0c96fe77bcf6a020b84a44c47a722aac8e359, raydium_launchpad/raydium_launchpad.json | 963189409945069409f32e345d4adf2892e0b3ca1be504372577fe34a23c6cdb |
| meteora_dlmm.json | LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo | github.com/MeteoraAg/dlmm-sdk @ 576919e3e4368e542c402f000b4264724f7f23ec, idls/dlmm.json | 045c0f4af044be046b6a14b350077e4242e8ac026b64a855803dab30fcdd8b35 |
| meteora_damm_v1.json | Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB | github.com/MeteoraAg/damm-v1-sdk @ 9e668081d17c36da5c0eb97ab568e097a58a1c7e, ts-client/src/amm/idl.json | 9529f39319eabfac4c9a3c5a05745d190eccdd9d121087cefbc887df25f7e3cc |
| meteora_damm_v2.json | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG | github.com/MeteoraAg/damm-v2-sdk @ 79ebbfe59a225e641a2f37cd03404f26de1b0c8e, src/idl/cp_amm.json | 7f5e5f5e9cab57cd208575dc53359676550e41ee0c19fc822560fe1062fd3ac5 |
| meteora_dbc.json | dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN | github.com/MeteoraAg/dynamic-bonding-curve-sdk @ a8e5fedf1cc62e488da477a19ae3c6ad9a707f80, packages/dynamic-bonding-curve/src/idl/dynamic-bonding-curve/idl.json | 63035b9a4a04b6a7f8fb9f81778275049c129a70d6f8bf6e08866516cf3da4cc |
| orca_whirlpool.json | whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc | npm @orca-so/whirlpools-sdk 0.22.0 (gitHead 46dc1c26bc553423f1c7bad35ba5cf9d19f6b4e7), dist/artifacts/whirlpool.json (orca-so/whirlpools idl/README.md points there) | f3e95ddf321d24b71ce56f5ba842055899f32a1feee3be9f0d3ccf2bd6a55bb8 |

Raydium AMM v4 (675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8) is not an Anchor program and has no IDL. Its
instruction tags and the amm account's position are written by hand in `AMM_V4_IXS` (extract.rs) from
github.com/raydium-io/raydium-amm @ 91739d8d9f5cf347e0ce14ebe4d2bb7069fb31b6, program/src/instruction.rs (sha256
499a331b01a1142975dece9ca7258a937ae0598422c33ec516c2f3e4883e0818): `AmmInstruction::unpack` for the tags, the
instruction builders for the account order (amm_pool at 1; at 4 in initialize2).

The pool account of an instruction: the first of its accounts (nested groups flattened, IDL order) named pool_state,
lb_pair, whirlpool, whirlpool_one, pool, bonding_curve, virtual_pool or amm; none for instructions without one
(configs, admin).
