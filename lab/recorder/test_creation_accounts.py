"""creation_accounts.py: the reversed creations' accounts of a covered range, and its refusals."""

import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import creation_accounts as ca

LO = 454_896_000  # epoch 1053


def addr(n):
    """A base58 address of 32 bytes (n, then 31 sevens)."""
    b = bytes([n]) + bytes([7]) * 31
    v, out = int.from_bytes(b, "big"), ""
    while v:
        v, r = divmod(v, 58)
        out = ca.B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def chunk(root, a, b, rows, ix=True, complete=True):
    d = os.path.join(root, f"{a}-{b}")
    os.makedirs(os.path.join(d, "pump_amm", "CreatePoolEvent"), exist_ok=True)
    with open(os.path.join(d, "_manifest.json"), "w") as f:
        json.dump({"status": "complete" if complete else "running", "slot_start": a, "slot_end_exclusive": b,
                   "extractor_sha256": "e" * 64}, f)
    if rows:
        cols = {"slot": [r[0] for r in rows], "base_mint": [r[1] for r in rows], "fee_payer": [r[3] for r in rows],
                "signers": [r[4] for r in rows]}
        if ix:
            cols["ix_accounts"] = [json.dumps(r[2]) for r in rows]
        pq.write_table(pa.table(cols), os.path.join(d, "pump_amm", "CreatePoolEvent", "part-0.parquet"))


def test_the_accounts_of_the_reversed_creations_only(tmp_path):
    root = str(tmp_path / "chunks")
    # two chunks covering [LO, LO + 200): a reversed creation each, a sol pool's (not taken), a list value (_extra)
    # and a value that is no address (skipped, counted)
    chunk(root, LO, LO + 100, [(LO + 1, ca.WSOL, {"pool": addr(1), "creator": addr(2), "_extra": [addr(3), "x"]}, addr(4), None),
                                (LO + 2, addr(9), {"pool": addr(10)}, addr(11), None)])
    chunk(root, LO + 100, LO + 200, [(LO + 150, ca.WSOL, {"pool": addr(5)}, addr(4), f"{addr(6)},{addr(7)}")])
    out = str(tmp_path / "creation_accounts.txt")
    ca.main(["--chunks", root, "--from", str(LO), "--to", str(LO + 200), "--out", out])
    lines = open(out).read().splitlines()
    head = [x for x in lines if x.startswith("#")]
    body = [x for x in lines if not x.startswith("#")]
    assert body == sorted({addr(i) for i in (1, 2, 3, 4, 5, 6, 7)})
    assert "chunks 2, reversed creations 2, accounts 7, non-address values skipped 1" in head[2]
    assert all(ca.is_address(x) for x in body) and not os.path.exists(out + ".tmp")


@pytest.mark.parametrize("case, why", [
    ("holdout", "touches the hold-out 1047-1051"),
    ("clean", "touches the clean 1055-1063"),
    ("gap", "no complete chunk covers"),
    ("running", "no complete chunk covers"),
    ("no_ix", "has no ix_accounts"),
    ("empty", "no reversed creation"),
])
def test_refusals_write_nothing(tmp_path, capsys, case, why):
    root = str(tmp_path / "chunks")
    lo, hi = LO, LO + 200
    row = [(LO + 1, ca.WSOL, {"pool": addr(1)}, addr(4), None)]
    if case == "holdout":
        lo, hi = 454_000_000, LO
    elif case == "clean":
        hi = 455_760_001
    chunk(root, LO, LO + 100, row if case != "empty" else [(LO + 2, addr(9), {"pool": addr(10)}, addr(11), None)],
          ix=case != "no_ix")
    if case != "gap":
        chunk(root, LO + 100, LO + 200, [], complete=case != "running")
    out = str(tmp_path / "creation_accounts.txt")
    with pytest.raises(SystemExit) as e:
        ca.main(["--chunks", root, "--from", str(lo), "--to", str(hi), "--out", out])
    assert e.value.code == 1 and why in capsys.readouterr().err and not os.path.exists(out)


def test_addresses_are_32_bytes():
    assert ca.is_address("11111111111111111111111111111111") and ca.is_address(ca.WSOL) and ca.is_address(addr(0))
    assert not ca.is_address("abc") and not ca.is_address(ca.WSOL + "1") and not ca.is_address("0" * 44) and not ca.is_address(None)
