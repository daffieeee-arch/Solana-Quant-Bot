"""The RPC route for token names, without any network: PDAs, account parsing, the lookup, one pass
over recorder segments with its daily cap, and that it never runs or prints the URL by accident."""

import json
import os
import struct
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest import token_meta_rpc as R

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MINT = "M1ntXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


def s(x):
    b = x.encode()
    return struct.pack("<I", len(b)) + b


def test_program_addresses():
    # Known pump.fun PDAs: the event authority and the mint authority.
    assert R.find_program_address([b"__event_authority"], PUMP)[0] == "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
    assert R.find_program_address([b"mint-authority"], PUMP)[0] == "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
    assert R.b58encode(R.b58decode(PUMP)) == PUMP and len(R.b58decode(PUMP)) == 32


def metaplex_account(mint, name="Pepper", symbol="PEP", uri="https://x"):
    # key 4, update authority, mint, then name/symbol/uri padded with NULs as on chain
    pad = lambda t, n: s(t + "\0" * (n - len(t)))  # noqa: E731
    return bytes([4]) + bytes(range(32)) + R.b58decode(mint).rjust(32, b"\0") + pad(name, 32) + pad(symbol, 10) + pad(uri, 200) + b"\0" * 50


def token2022_mint(mint, name="Coin", symbol="CN", uri=""):
    meta = bytes(32) + R.b58decode(mint).rjust(32, b"\0") + s(name) + s(symbol) + s(uri) + struct.pack("<I", 0)
    return bytes(165) + bytes([1]) + struct.pack("<HH", 18, 4) + b"\0" * 4 + struct.pack("<HH", 19, len(meta)) + meta


def test_account_parsing():
    mint = R.b58encode(bytes([7]) * 32)
    ua, m, name, symbol, uri = R.parse_metaplex(metaplex_account(mint))
    assert (m, name, symbol, uri) == (mint, "Pepper", "PEP", "https://x") and ua == R.b58encode(bytes(range(32)))
    assert R.parse_metaplex(b"\x05" + bytes(200)) is None
    ua, m, name, symbol, uri = R.parse_token2022_metadata(token2022_mint(mint))
    assert (ua, m, name, symbol, uri) == (None, mint, "Coin", "CN", "")
    assert R.parse_token2022_metadata(bytes(165) + bytes([1]) + struct.pack("<HH", 18, 4) + bytes(4)) is None


def test_lookup_prefers_metaplex_then_token2022():
    mint = R.b58encode(bytes([9]) * 32)
    pda = R.metadata_pda(mint)
    calls = []

    def fake(url, keys):
        calls.append(keys)
        return {pda: (R.METAPLEX, metaplex_account(mint)), mint: (R.TOKEN_2022, token2022_mint(mint))}

    assert R.lookup("u", mint, fake)[:3] == ("rpc_metaplex", R.b58encode(bytes(range(32))), "Pepper")
    assert calls == [[pda, mint]]  # one call per token
    assert R.lookup("u", mint, lambda u, k: {pda: None, mint: (R.TOKEN_2022, token2022_mint(mint))})[0] == "rpc_token2022"
    assert R.lookup("u", mint, lambda u, k: {pda: None, mint: ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", bytes(82))}) is None


def segment(root, seq, mints, slots):
    """A complete recorder segment with reversed-pool creations of these tokens."""
    d = root / "segments" / f"{seq:09d}"
    (d / "pump_amm" / "CreatePoolEvent").mkdir(parents=True)
    pq.write_table(pa.table({"slot": slots, "base_mint": [R.WSOL] * len(mints), "quote_mint": mints}),
                   str(d / "pump_amm" / "CreatePoolEvent" / "part-0.parquet"))
    (d / "_segment.json").write_text("{}")


def named_by(found):
    def fake(url, keys):
        pda, mint = keys
        found.append(mint)
        return {pda: (R.METAPLEX, metaplex_account(mint, name=mint[:4])), mint: None}
    return fake


def test_one_pass_over_segments_with_the_daily_cap(tmp_path):
    root = tmp_path / "rec"
    mints = [R.b58encode(bytes([i]) * 32) for i in range(1, 5)]
    segment(root, 1, mints, [10, 11, 12, 13])
    seg = root / "segments" / "000000001" / "pump_amm" / "CreatePoolEvent"
    pq.write_table(pa.table({"slot": [14], "base_mint": [mints[0]], "quote_mint": [R.WSOL]}), str(seg / "part-1.parquet"))
    named = {}

    def fake(url, keys):
        pda, mint = keys
        named[mint] = True
        return {pda: (R.METAPLEX, metaplex_account(mint, name=mint[:4])), mint: None}

    assert R.run_once(str(root), "u", max_calls=3, call=fake, log=lambda m: None) == 3
    state = json.load(open(root / "rpc" / "state.json"))
    assert state["calls"] == 3 and len(named) == 3
    # The next pass does not ask again for named tokens; the cap of the day stops the 4th.
    assert R.run_once(str(root), "u", max_calls=3, call=fake, log=lambda m: None) == 0
    assert R.run_once(str(root), "u", max_calls=4, call=fake, log=lambda m: None) == 1
    rows = pq.read_table(str(root / "rpc" / "token_meta")).to_pylist()
    assert sorted(r["mint"] for r in rows) == sorted(mints) and all(r["source"] == "rpc_metaplex" for r in rows)
    # backtest.token_meta reads them with --recorder.
    from backtest import token_meta

    out = str(tmp_path / "tm.parquet")
    assert token_meta.build([], str(root), out) == {"rpc_metaplex": 4}


def test_off_by_default_and_the_url_is_never_printed(tmp_path):
    env = dict(os.environ, LAB_TRITON_RPC_URL="https://secret-token.example.invalid/abc")
    cmd = [sys.executable, "-m", "backtest.token_meta_rpc", "--root", str(tmp_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(R.__file__)))
    assert r.returncode != 0 and "--enable" in r.stderr
    with pytest.raises(RuntimeError) as e:
        R.rpc_accounts("https://secret-token.example.invalid/abc", ["x"], timeout=1)
    assert "secret-token" not in str(e.value)


def test_requests_carry_an_explicit_user_agent(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"result": {"value": [null]}}'

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_urlopen)
    assert R.rpc_accounts("https://example.invalid", ["x"]) == {"x": None}
    assert seen["ua"] == "solana-lab/0.1"


def test_a_failing_lookup_keeps_the_count_and_the_names_found(tmp_path):
    root = tmp_path / "rec"
    mints = [R.b58encode(bytes([i]) * 32) for i in range(1, 4)]
    segment(root, 1, mints, [10, 11, 12])
    calls = []

    def flaky(url, keys):
        calls.append(keys[1])
        if len(calls) == 2:
            raise KeyError("result")  # a reply with neither result nor error
        return named_by([])(url, keys)

    logs = []
    assert R.run_once(str(root), "https://secret.example.invalid/x", 10, call=flaky, log=logs.append) == 1
    state = json.load(open(root / "rpc" / "state.json"))
    assert state["calls"] == 2 and state["last_seq"] == -1  # counted, and the segment is read again
    assert logs == ["lookup failed: KeyError"]
    assert [r["mint"] for r in pq.read_table(str(root / "rpc" / "token_meta")).to_pylist()] == [mints[0]]
    # The next pass asks only for the two not named yet, then marks the segment as read.
    found = []
    assert R.run_once(str(root), "u", 10, call=named_by(found), log=lambda m: None) == 2
    assert found == mints[1:] and json.load(open(root / "rpc" / "state.json"))["calls"] == 4
    assert json.load(open(root / "rpc" / "state.json"))["last_seq"] == 1


def test_passes_read_only_newer_complete_segments(tmp_path):
    root = tmp_path / "rec"
    a, b, c, d = (R.b58encode(bytes([i]) * 32) for i in range(10, 14))
    segment(root, 5, [a], [10])
    found = []
    assert R.run_once(str(root), "u", 10, call=named_by(found), log=lambda m: None) == 1
    segment(root, 3, [b], [11])  # an older sequence number: already behind
    segment(root, 6, [c], [12])
    unfinished = root / "segments" / "000000007" / "pump_amm" / "CreatePoolEvent"
    unfinished.mkdir(parents=True)  # no _segment.json yet
    pq.write_table(pa.table({"slot": [13], "base_mint": [R.WSOL], "quote_mint": [d]}), str(unfinished / "part-0.parquet"))
    assert R.run_once(str(root), "u", 10, call=named_by(found), log=lambda m: None) == 1
    assert found == [a, c] and json.load(open(root / "rpc" / "state.json"))["last_seq"] == 6


def test_follow_survives_a_failing_pass_and_tried_keeps_the_newest(tmp_path, monkeypatch):
    logs, sleeps = [], []
    results = iter([ValueError("bad string"), 2])

    def once(root, url, max_calls, log):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    R.run_follow("root", "u", 10, 60, once=once, log=logs.append, sleep=sleeps.append, passes=2)
    assert logs == ["pass failed: ValueError", "2 names written"] and sleeps == [60]
    monkeypatch.setattr(R, "TRIED_MAX", 2)
    root = tmp_path / "rec"
    mints = [R.b58encode(bytes([i]) * 32) for i in range(20, 23)]
    segment(root, 1, mints, [1, 2, 3])
    R.run_once(str(root), "u", 10, call=lambda u, k: {k[0]: None, k[1]: None}, log=lambda m: None)
    assert json.load(open(root / "rpc" / "state.json"))["tried"] == mints[1:]  # insertion order, oldest dropped
