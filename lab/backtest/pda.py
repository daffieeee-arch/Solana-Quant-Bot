"""Offline Solana address derivation: base58, program-derived addresses, ATAs and pump pools.

Used to classify PumpSwap pools from their events alone. A pool's protocol fee account is the
associated token account of the fee recipient for the pool's quote mint, so comparing it with
ATA(recipient, WSOL) tells whether the quote is SOL. No RPC is involved.

A PDA is sha256(seeds || bump || program || "ProgramDerivedAddress") for the highest bump whose
hash is not a valid ed25519 point; the on-curve test matches curve25519-dalek decompression.
"""

import hashlib
from functools import lru_cache

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_IDX = {c: i for i, c in enumerate(_B58)}

_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + _IDX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    out = b"\x00" * (len(s) - len(s.lstrip("1"))) + body
    if len(out) != 32:
        raise ValueError(f"not a 32-byte key: {s}")
    return out


def b58encode(b):
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def on_curve(b):
    """True if the 32 bytes decompress to an ed25519 point (y = low 255 bits, sign ignored):
    (y^2 - 1) / (d*y^2 + 1) must be a square mod p, zero included."""
    y = (int.from_bytes(b, "little") & ((1 << 255) - 1)) % _P
    yy = y * y % _P
    u = (yy - 1) % _P
    if u == 0:
        return True
    v = (_D * yy + 1) % _P
    return pow(u * v % _P, (_P - 1) // 2, _P) == 1  # Legendre(u/v) = Legendre(u*v)


def find_program_address(seeds, program):
    for bump in range(255, -1, -1):
        h = hashlib.sha256()
        for s in seeds:
            h.update(s)
        h.update(bytes([bump]))
        h.update(program)
        h.update(b"ProgramDerivedAddress")
        d = h.digest()
        if not on_curve(d):
            return d, bump
    raise ValueError("no viable bump")


def _pda(seeds, program):
    return b58encode(find_program_address(seeds, b58decode(program))[0])


ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"
DEFAULT_KEY = "11111111111111111111111111111111"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


@lru_cache(maxsize=None)
def ata(owner, mint, token_program=TOKEN):
    return _pda([b58decode(owner), b58decode(token_program), b58decode(mint)], ATA_PROGRAM)


def pool_authority(mint):
    """The pump program's pool creator for a migrated mint."""
    return _pda([b"pool-authority", b58decode(mint)], PUMP)


def amm_pool(index, creator, base_mint, quote_mint):
    return _pda([b"pool", index.to_bytes(2, "little"), b58decode(creator), b58decode(base_mint),
                 b58decode(quote_mint)], PUMP_AMM)


def canonical_pool(mint, quote_mint=WSOL):
    """The PumpSwap pool a pump curve migrates into (index 0, created by the pool authority).
    Pump events name native SOL as the default key; the pool's quote mint is then WSOL."""
    if quote_mint in (None, DEFAULT_KEY):
        quote_mint = WSOL
    return amm_pool(0, pool_authority(mint), mint, quote_mint)
