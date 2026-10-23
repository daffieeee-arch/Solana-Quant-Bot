"""Unsigned Solana transactions in pure Python, for simulation only (paper.simfee_probe).

A legacy message (header, account keys, recent blockhash, compiled instructions) and its wire form with dummy signature
bytes. Nothing here signs: there is no private key in the lab, and the probe asks the RPC node to skip the signature check
and to replace the blockhash (simulateBundle's skipSigVerify + replaceRecentBlockhash), so such a transaction can only be
simulated, never land. Wire format: https://solana.com/docs/core/transactions/transaction-structure.

An instruction is (program, [(key, is_signer, is_writable), ...], data), keys as base58 strings.
"""

import struct

from backtest.pda import b58decode, b58encode

MAX_TX_BYTES = 1232  # the packet limit
SYSTEM = "11111111111111111111111111111111"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"


def cu16(n):
    """compact-u16 (shortvec)."""
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def read_cu16(buf, i):
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def compile_message(payer, ixs, blockhash=bytes(32)):
    """(message bytes, number of signers, ordered keys): the payer first, then signer-writable, signer-readonly,
    writable and readonly keys in first-seen order; a program id is a readonly key."""
    flags, order = {payer: [True, True]}, [payer]
    for prog, metas, _ in ixs:
        for k, s, w in metas:
            if k not in flags:
                flags[k] = [False, False]
                order.append(k)
            flags[k][0] |= s
            flags[k][1] |= w
        if prog not in flags:
            flags[prog] = [False, False]
            order.append(prog)
    groups = [[k for k in order if flags[k] == g] for g in ([True, True], [True, False], [False, True], [False, False])]
    keys = [k for g in groups for k in g]
    if keys[0] != payer:
        raise ValueError("the payer must be the first signer")
    idx = {k: i for i, k in enumerate(keys)}
    nsig = len(groups[0]) + len(groups[1])
    body = bytes([nsig, len(groups[1]), len(groups[3])]) + cu16(len(keys)) + b"".join(b58decode(k) for k in keys) + blockhash
    body += cu16(len(ixs))
    for prog, metas, data in ixs:
        body += bytes([idx[prog]]) + cu16(len(metas)) + bytes(idx[k] for k, _, _ in metas) + cu16(len(data)) + data
    return body, nsig, keys


def wire(message, signatures):
    """The transaction: compact-u16 signature count, the 64-byte signatures (dummies here), the message."""
    if any(len(s) != 64 for s in signatures):
        raise ValueError("a signature is 64 bytes")
    tx = cu16(len(signatures)) + b"".join(signatures) + message
    if len(tx) > MAX_TX_BYTES:
        raise ValueError(f"transaction of {len(tx)} bytes > {MAX_TX_BYTES}")
    return tx


def decode(tx):
    """The inverse of wire(compile_message(...)): (signatures, header, keys, blockhash, [(program, [(key, s, w)], data)]),
    for the offline self-check."""
    nsig, i = read_cu16(tx, 0)
    sigs = [tx[i + 64 * j:i + 64 * (j + 1)] for j in range(nsig)]
    i += 64 * nsig
    header = tuple(tx[i:i + 3])
    i += 3
    nkeys, i = read_cu16(tx, i)
    keys = [b58encode(tx[i + 32 * j:i + 32 * (j + 1)]) for j in range(nkeys)]
    i += 32 * nkeys
    blockhash = tx[i:i + 32]
    i += 32
    nix, i = read_cu16(tx, i)
    req, ro_signed, ro_unsigned = header

    def signer(j):
        return j < req

    def writable(j):
        return j < req - ro_signed if j < req else j < nkeys - ro_unsigned

    out = []
    for _ in range(nix):
        prog = keys[tx[i]]
        n, i = read_cu16(tx, i + 1)
        metas = [(keys[j], signer(j), writable(j)) for j in tx[i:i + n]]
        i += n
        dlen, i = read_cu16(tx, i)
        out.append((prog, metas, bytes(tx[i:i + dlen])))
        i += dlen
    if i != len(tx):
        raise ValueError("trailing bytes")
    return sigs, header, keys, blockhash, out


def cu_limit(units):
    return (COMPUTE_BUDGET, [], bytes([2]) + struct.pack("<I", units))


def transfer(src, dst, lamports):
    return (SYSTEM, [(src, True, True), (dst, False, True)], struct.pack("<IQ", 2, lamports))
