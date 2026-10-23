"""mint_check: the mint parser (SPL Token base state, Token-2022 TLV extensions), the flags, the call count, the
daily-cap refusal and the report counts on a synthetic journal."""
import base64
import json
import sqlite3
import struct

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest.token_meta_rpc import TOKEN_2022, b58decode
from wallets import mint_check as M

K1 = "11111111111111111111111111111111"
AUTH = "FgtjptxVfvf4tzoPYo2fyc1YX16rERHoZzb4ME3Uf5jp"
HOOK = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def _base(mint_auth=None, freeze=None, supply=10**15, decimals=6):
    def copt(k):
        return struct.pack("<I", 1) + b58decode(k) if k else struct.pack("<I", 0) + b"\0" * 32
    return copt(mint_auth) + struct.pack("<QBB", supply, decimals, 1) + copt(freeze)


def _tlv(ext, payload):
    return struct.pack("<HH", ext, len(payload)) + payload


def _t22(base, exts):
    return base + b"\0" * 83 + b"\x01" + b"".join(exts)


def test_classic_mint_without_authorities_is_clean():
    r = M.parse_mint(M.SPL_TOKEN, _base())
    assert r["program"] == "token" and r["mint_authority_key"] is None and r["freeze_authority_key"] is None and r["supply"] == 10**15
    f = M.flags(r)
    assert not f["veto"] and not f["token2022"] and not any(f[k] for k in M.VETO_FLAGS)


def test_classic_mint_with_freeze_and_mint_authority():
    r = M.parse_mint(M.SPL_TOKEN, _base(mint_auth=AUTH, freeze=AUTH))
    assert r["mint_authority_key"] == AUTH and r["freeze_authority_key"] == AUTH
    f = M.flags(r)
    assert f["mint_authority"] and f["freeze_authority"] and f["veto"]


def test_token2022_extensions_are_decoded():
    fee = b"\0" * 32 + b58decode(AUTH) + struct.pack("<Q", 0) + struct.pack("<QQH", 1, 0, 0) + struct.pack("<QQH", 2, 5_000_000, 250)
    exts = [_tlv(18, b"\0" * 64), _tlv(19, b"\0" * 64 + struct.pack("<I", 3) + b"abc" + struct.pack("<I", 0) + struct.pack("<I", 0) + struct.pack("<I", 0)),
            _tlv(1, fee), _tlv(12, b58decode(AUTH)), _tlv(14, b"\0" * 32 + b58decode(HOOK)), _tlv(6, b"\x02"), _tlv(26, b58decode(AUTH) + b"\x01"),
            _tlv(9, b""), _tlv(3, b58decode(AUTH)), _tlv(25, b"\0" * 56)]
    r = M.parse_mint(TOKEN_2022, _t22(_base(), exts))
    assert r["program"] == "token2022" and r["parse_error"] is None
    assert r["extensions"] == [18, 19, 1, 12, 14, 6, 26, 9, 3, 25]
    assert (r["transfer_fee_bps"], r["transfer_fee_max"], r["transfer_fee_bps_older"]) == (250, 5_000_000, 0)
    assert r["permanent_delegate_key"] == AUTH and r["transfer_hook_program"] == HOOK and r["default_state"] == 2
    assert r["pausable_authority"] == AUTH and r["paused"] is True and r["mint_close_authority_key"] == AUTH
    f = M.flags(r)
    assert all(f[k] for k in ["transfer_fee", "transfer_hook", "permanent_delegate", "non_transferable", "default_frozen", "pausable", "paused",
                              "token2022", "mint_close_authority", "metadata_pointer", "token_metadata", "scaled_ui_amount"])
    assert not f["mint_authority"] and not f["freeze_authority"] and not f["other_extension"] and f["veto"]


def test_older_transfer_fee_alone_flags_the_fee_and_a_failing_call_keeps_earlier_answers(tmp_path):
    fee = b"\0" * 64 + struct.pack("<Q", 0) + struct.pack("<QQH", 1, 10, 100) + struct.pack("<QQH", 2, 0, 0)
    r = M.parse_mint(TOKEN_2022, _t22(_base(), [_tlv(1, fee)]))
    assert (r["transfer_fee_bps"], r["transfer_fee_bps_older"]) == (0, 100) and M.flags(r)["transfer_fee"]
    # fetch: 101 mints = 2 calls; the second call fails, the first call's 100 answers are on disk
    out = tmp_path / "out"
    out.mkdir()
    mints = [f"M{i:03d}" for i in range(101)]
    pq.write_table(pa.table({"mint": mints, "families": [["K3"]] * 101}), out / "mints.parquet")
    n = [0]

    def flaky(url, keys):
        n[0] += 1
        if n[0] == 2:
            raise RuntimeError("RPC call failed: URLError")
        return {k: (M.SPL_TOKEN, _base()) for k in keys}
    with pytest.raises(RuntimeError):
        M.fetch(str(out), "http://x", 5000, call=flaky, rpc_state=str(tmp_path / "none.json"))
    assert pq.read_table(out / "accounts.parquet").num_rows == 100 and json.load(open(out / "rpc-calls.json"))["calls"] == 2
    assert M.fetch(str(out), "http://x", 5000, call=flaky, rpc_state=str(tmp_path / "none.json")) == 1  # only the missing one
    assert pq.read_table(out / "accounts.parquet").num_rows == 101


def test_token2022_pump_style_mint_is_clean():
    """A Token-2022 mint with only metadata pointer + token metadata (pump.fun's layout): no veto flag."""
    r = M.parse_mint(TOKEN_2022, _t22(_base(), [_tlv(18, b"\0" * 64), _tlv(19, b"\0" * 64 + b"\0" * 12)]))
    f = M.flags(r)
    assert f["token2022"] and f["metadata_pointer"] and f["token_metadata"] and not f["veto"]
    # a transfer hook extension whose program id is zero sets no hook
    r = M.parse_mint(TOKEN_2022, _t22(_base(), [_tlv(14, b"\0" * 64)]))
    assert r["transfer_hook_program"] is None and not M.flags(r)["transfer_hook"]
    # an unknown extension id is counted as 'other'
    r = M.parse_mint(TOKEN_2022, _t22(_base(), [_tlv(99, b"\0" * 8)]))
    assert M.flags(r)["other_extension"]
    # not a token account
    r = M.parse_mint(K1, b"\0" * 100)
    assert r["parse_error"] == "not a token mint" and M.flags(r)["veto"] is False


def test_calls_needed():
    assert M.calls_needed(0) == 0 and M.calls_needed(1) == 1 and M.calls_needed(100) == 1 and M.calls_needed(101) == 2 and M.calls_needed(1367) == 14


def _journal(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE signals (family TEXT, variant TEXT, key TEXT, t INTEGER)")
    con.execute("CREATE TABLE learned (kind TEXT, key TEXT, seq INTEGER, slot INTEGER, info TEXT)")
    pools = {"P1": ("M1", AUTH), "P2": ("M2", K1), "P3": ("M3", HOOK), "P4": ("M4", AUTH)}
    for p, (m, cr) in pools.items():
        con.execute("INSERT INTO learned VALUES ('pool', ?, 1, 459700000, ?)", (p, json.dumps(["reversed", "reversed", p, m, "reversed", "reversed", False, cr, 459700000])))
    rows = [("F7", "tp4_T600", "P1", 459700001), ("F7", "flow300_H6", "P1", 459700001), ("F7", "flow300_tp100_H6", "P1", 459700001),
            ("F7", "tp4_T600", "P2", 459700002), ("N2_F7", "tp4_T600", "P3", 459700003), ("F7", "tp4_T600", "P4", 459000000),
            ("F7", "tp4_T600", "P9", 459700009)]  # P4 before the window, P9 has no learned row
    con.executemany("INSERT INTO signals VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def test_list_fetch_report_on_a_synthetic_journal(tmp_path, monkeypatch):
    j = str(tmp_path / "journal.sqlite")
    _journal(j)
    probes = str(tmp_path / "probes.parquet")
    pq.write_table(pa.table({"pool": ["P2", "P3", "P4"]}), probes)
    out = str(tmp_path / "out")
    M.main(["list", "--out", out, "--journal", j, "--probes", probes])
    mints = pq.read_table(out + "/mints.parquet").to_pylist()
    by = {r["pool"]: r for r in mints}
    assert set(by) == {"P1", "P2", "P3", "P4"} and by["P1"]["families"] == ["H4", "H5", "K3"] and by["P2"]["families"] == ["K3", "probe"]
    assert by["P4"]["families"] == ["probe"] and by["P3"]["coin_creator"] == HOOK
    # fetch: the cap counts the token-meta process's calls of the day
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"day": M._today(), "calls": 5000}))
    data = {"M1": (M.SPL_TOKEN, _base()), "M2": (TOKEN_2022, _t22(_base(), [_tlv(12, b58decode(AUTH))])),
            "M3": (TOKEN_2022, _t22(_base(freeze=AUTH), [_tlv(18, b"\0" * 64)])), "M4": None}
    calls = []

    def fake(url, keys):
        calls.append(keys)
        return {k: data.get(k) for k in keys}
    with pytest.raises(SystemExit):
        M.fetch(out, "http://x", 5000, call=fake, rpc_state=str(state))
    assert calls == []
    state.write_text(json.dumps({"day": "2000-01-01", "calls": 4999}))  # another day: not counted
    assert M.fetch(out, "http://x", 5000, call=fake, rpc_state=str(state)) == 4
    assert len(calls) == 1 and json.load(open(out + "/rpc-calls.json"))["calls"] == 1
    assert M.fetch(out, "http://x", 5000, call=fake, rpc_state=str(state)) == 0  # nothing left: no call
    assert len(calls) == 1
    acc = {r["mint"]: r for r in pq.read_table(out + "/accounts.parquet").to_pylist()}
    assert acc["M4"]["found"] is False and base64.b64decode(acc["M1"]["data_b64"]) == _base()
    counts = M.report(out)
    assert counts["K3"]["mints"] == 2 and counts["K3"]["found"] == 2 and counts["K3"]["permanent_delegate"] == 1 and counts["K3"]["veto_any"] == 1
    assert counts["H5"]["mints"] == 1 and counts["H5"]["veto_any"] == 0 and counts["H5"]["token"] == 1
    assert counts["N2"]["freeze_authority"] == 1 and counts["N2"]["token2022"] == 1 and counts["N2"]["metadata_pointer"] == 1
    assert counts["probe"]["mints"] == 3 and counts["probe"]["found"] == 2 and counts["all_journal"]["mints"] == 3
    fl = {r["mint"]: r for r in pq.read_table(out + "/flags.parquet").to_pylist()}
    assert fl["M4"]["found"] is False and fl["M4"]["veto"] is None and fl["M2"]["veto"] is True
