"""Solana keys without a Solana library: base58, the ed25519 on-curve test and the pump.fun bonding-curve PDA of a
mint (seeds "bonding-curve" + mint under 6EF8rr…), which owns the curve's token vault. The transfers ledger needs it
to tell the curve's own vault row from any other program-derived owner in the same transaction (a PDA-held trader
account, a bot's PDA): the extractor flags every off-curve owner as `is_vault`.

A mint that is no 32-byte key (test fixtures) is seeded as its UTF-8 bytes, so the derivation stays deterministic."""

import hashlib

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + _ALPHABET.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def b58encode(b):
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _ALPHABET[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def on_curve(key32):
    """True when the 32 bytes decompress to an ed25519 point (a key that can sign); a PDA is off the curve."""
    y = int.from_bytes(key32, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    x2 = (y2 - 1) * pow((_D * y2 + 1) % _P, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return False
    return not (x == 0 and sign)


def is_pda(b58):
    try:
        k = b58decode(b58)
    except ValueError:
        return False
    return len(k) == 32 and not on_curve(k)


def curve_pda(mint, program=PUMP_PROGRAM):
    """The bonding-curve account of a pump.fun mint: find_program_address(["bonding-curve", mint], program)."""
    try:
        mb = b58decode(mint)
        if len(mb) != 32:
            raise ValueError(mint)
    except ValueError:
        mb = mint.encode()
    pb = b58decode(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"bonding-curve" + mb + bytes([bump]) + pb + b"ProgramDerivedAddress").digest()
        if not on_curve(h):
            return b58encode(h)
    raise ValueError(f"no bump for {mint}")
