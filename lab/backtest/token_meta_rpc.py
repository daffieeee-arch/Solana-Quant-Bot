"""token_meta through Triton RPC: names of new reversed-pool tokens (off by default).

Reversed-pool tokens are not pump.fun mints, so neither the CreateEvent nor (mostly) the
extractor's metadata decoder names them. This side process reads the recorder's segments, finds the
tokens of new reversed pools (CreatePoolEvent with WSOL as base, and the pool_creations stream),
and asks Triton's RPC once per token: getMultipleAccounts([Metaplex metadata PDA, mint]). The name,
symbol and uri come from the Metaplex metadata account or, for a Token-2022 mint, from its
TokenMetadata extension. Rows go to <root>/rpc/token_meta/part-*.parquet (the token_meta columns,
source rpc_metaplex / rpc_token2022), which `backtest.token_meta --recorder <root>` reads.

A UI lookup only, never a strategy input: names, symbols and uris must not feed signals, filters
or parameters (a build over all chunks also reads epochs 1047-1051, the hold-out).

Paid Triton usage ($10 per million calls; ~1,700 new reversed pools a day): it runs only with
--enable and LAB_TRITON_RPC_URL set (the URL holds the token: it is never printed or logged), and
stops for the UTC day at --max-calls-per-day (state in <root>/rpc/state.json).

  python -m backtest.token_meta_rpc --root <recorder root> --enable [--follow] [--max-calls-per-day 5000]
"""

import argparse
import base64
import datetime as dt
import glob
import hashlib
import json
import os
import struct
import time
import urllib.request

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

WSOL = "So11111111111111111111111111111111111111112"
METAPLEX = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_METADATA_EXT = 19  # spl-token-2022 ExtensionType::TokenMetadata
# An explicit User-Agent: Old Faithful answered 403 to urllib's default one, so a provider's UA
# filter would otherwise fail quietly.
USER_AGENT = "solana-lab/0.1"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SCHEMA = pa.schema([("slot", pa.uint64()), ("tx_index", pa.uint64()), ("signature", pa.string()), ("outer_ix", pa.uint64()),
                    ("inner_ix", pa.uint64()), ("source", pa.string()), ("mint", pa.string()), ("name", pa.string()),
                    ("symbol", pa.string()), ("uri", pa.string()), ("update_authority", pa.string())])


# ----------------------------------------------------------------------------- keys and PDAs


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def b58encode(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _on_curve(h):
    """Whether 32 bytes decode to an ed25519 point (a program address must not)."""
    y = int.from_bytes(h, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    return (x * x - x2) % _P == 0


def find_program_address(seeds, program):
    pid = b58decode(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + pid + b"ProgramDerivedAddress").digest()
        if not _on_curve(h):
            return b58encode(h), bump
    raise ValueError("no program address")


def metadata_pda(mint):
    return find_program_address([b"metadata", b58decode(METAPLEX), b58decode(mint)], METAPLEX)[0]


# ----------------------------------------------------------------------------- account parsing


def _string(data, pos):
    (n,) = struct.unpack_from("<I", data, pos)
    if n > 1024 or pos + 4 + n > len(data):
        raise ValueError("bad string")
    return data[pos + 4:pos + 4 + n].decode("utf-8", "replace").rstrip("\0"), pos + 4 + n


def parse_metaplex(data):
    """(update authority, mint, name, symbol, uri) of a Metaplex Metadata account (key 4)."""
    if len(data) < 1 + 64 + 12 or data[0] != 4:
        return None
    ua, mint = b58encode(data[1:33]), b58encode(data[33:65])
    name, p = _string(data, 65)
    symbol, p = _string(data, p)
    uri, _ = _string(data, p)
    return ua, mint, name, symbol, uri


def parse_token2022_metadata(data):
    """(update authority, mint, name, symbol, uri) from a Token-2022 mint's TokenMetadata extension."""
    if len(data) <= 166 or data[165] != 1:  # account type 1: Mint (after the 165-byte padded base)
        return None
    pos = 166
    while pos + 4 <= len(data):
        ext, length = struct.unpack_from("<HH", data, pos)
        value = data[pos + 4:pos + 4 + length]
        if ext == TOKEN_METADATA_EXT:
            ua = b58encode(value[0:32]) if any(value[0:32]) else None
            mint = b58encode(value[32:64])
            name, p = _string(value, 64)
            symbol, p = _string(value, p)
            uri, _ = _string(value, p)
            return ua, mint, name, symbol, uri
        if ext == 0 and length == 0:
            break
        pos += 4 + length
    return None


# ----------------------------------------------------------------------------- RPC


def rpc_accounts(url, keys, timeout=20):
    """getMultipleAccounts (base64): {key: (owner, data bytes) or None}. Errors never contain the URL."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                       "params": [keys, {"encoding": "base64", "commitment": "confirmed"}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except Exception as e:  # noqa: BLE001 - the message of a URL error may contain the URL
        raise RuntimeError(f"RPC call failed: {e.__class__.__name__}") from None
    if "error" in res:
        raise RuntimeError(f"RPC error {res['error'].get('code')}")
    out = {}
    for k, v in zip(keys, res["result"]["value"]):
        out[k] = None if v is None else (v["owner"], base64.b64decode(v["data"][0]))
    return out


def lookup(url, mint, call=rpc_accounts):
    """(source, update authority, name, symbol, uri) of one mint, or None."""
    pda = metadata_pda(mint)
    acc = call(url, [pda, mint])
    m = acc.get(pda)
    if m and m[0] == METAPLEX:
        x = parse_metaplex(m[1])
        if x and x[1] == mint:
            return ("rpc_metaplex", x[0], *x[2:])
    t = acc.get(mint)
    if t and t[0] == TOKEN_2022:
        x = parse_token2022_metadata(t[1])
        if x:
            return ("rpc_token2022", x[0], *x[2:])
    return None


# ----------------------------------------------------------------------------- the loop


def _segments_after(root, after_seq):
    """(seq, dir) of complete recorder segments (with _segment.json) after after_seq, in order."""
    out = []
    for meta in glob.glob(os.path.join(root, "segments", "*", "_segment.json")):
        d = os.path.dirname(meta)
        name = os.path.basename(d)
        if name.isdigit() and int(name) > after_seq:
            out.append((int(name), d))
    return sorted(out)


def _read(con, dirs, table, select):
    files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, table, "*.parquet")))
    return con.execute(f"SELECT {select} FROM read_parquet({files!r}, union_by_name = true)").fetchall() if files else []


def new_reversed_tokens(dirs, con):
    """(mint, first slot) of the quote tokens of reversed pools in these segment directories."""
    rows = []
    for table in ("pump_amm/CreatePoolEvent", "pool_creations"):
        rows += [r for r in _read(con, dirs, table, f"quote_mint, slot, base_mint = '{WSOL}'") if r[2]]
    first = {}
    for mint, slot, _ in rows:
        first[mint] = min(slot, first.get(mint, slot))
    return sorted(first.items(), key=lambda x: (x[1], x[0]))


TRIED_MAX = 100_000  # mints remembered as looked up (the oldest go first)


def _save_state(path, state):
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def _write_rows(root, rows):
    if rows:
        path = os.path.join(root, "rpc", "token_meta", f"part-{int(time.time() * 1000)}.parquet")
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path + ".tmp")
        os.replace(path + ".tmp", path)


def run_once(root, url, max_calls, call=rpc_accounts, log=print):
    """Look up the tokens of new reversed pools in segments not read yet (state.json keeps the last
    segment read, the UTC day's call count and the mints tried). The call count is saved after every
    call and the names found so far are written even when a lookup fails, so a crash or a restart
    never spends calls twice. Returns the rows written."""
    os.makedirs(os.path.join(root, "rpc", "token_meta"), exist_ok=True)
    state_path = os.path.join(root, "rpc", "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    state.setdefault("tried", [])
    state.setdefault("last_seq", -1)
    if state.get("day") != today:
        state.update(day=today, calls=0)
    tried = list(state["tried"])  # insertion order: the oldest are dropped first
    tried_set = set(tried)
    con = duckdb.connect()
    segs = _segments_after(root, state["last_seq"])
    dirs = [d for _, d in segs]
    named = {r[0] for r in _read(con, dirs, "token_meta", "mint")}
    named |= {r[0] for r in _read(con, [os.path.join(root, "rpc")], "token_meta", "mint")}
    rows = []
    stopped = False
    try:
        for mint, slot in new_reversed_tokens(dirs, con):
            if mint in named or mint in tried_set:
                continue
            if state["calls"] >= max_calls:
                log(f"daily RPC cap reached ({max_calls} calls): stopped until the next UTC day")
                stopped = True
                break
            state["calls"] += 1
            tried.append(mint)
            tried_set.add(mint)
            state["tried"] = tried[-TRIED_MAX:]
            _save_state(state_path, state)  # every paid call is counted before its answer is used
            try:
                x = lookup(url, mint, call)
            except Exception as e:  # noqa: BLE001 - the message could carry the URL: only the class
                log(f"lookup failed: {e.__class__.__name__}" if not isinstance(e, RuntimeError) else str(e))
                tried.pop()  # asked again in a later pass (the call stays counted)
                tried_set.discard(mint)
                stopped = True
                break
            if x:
                src, ua, name, symbol, uri = x
                rows.append({"slot": slot, "tx_index": 0, "signature": None, "outer_ix": 0, "inner_ix": 0, "source": src,
                             "mint": mint, "name": name, "symbol": symbol, "uri": uri, "update_authority": ua})
        if not stopped and segs:
            state["last_seq"] = segs[-1][0]  # all tokens of these segments were handled
    finally:
        _write_rows(root, rows)
        state["tried"] = tried[-TRIED_MAX:]
        _save_state(state_path, state)
    return len(rows)


def run_follow(root, url, max_calls, interval, once=run_once, log=print, sleep=time.sleep, passes=None):
    """One pass per interval; a failing pass is logged (class only) and the next one follows."""
    n = 0
    while passes is None or n < passes:
        n += 1
        try:
            log(f"{once(root, url, max_calls, log=log)} names written")
        except Exception as e:  # noqa: BLE001
            log(f"pass failed: {e.__class__.__name__}")
        if passes is None or n < passes:
            sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="the recorder's output directory")
    ap.add_argument("--enable", action="store_true", help="required: paid Triton RPC calls (owner's OK)")
    ap.add_argument("--follow", action="store_true", help="keep running, one pass per --interval")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--max-calls-per-day", type=int, default=5000)
    a = ap.parse_args()
    url = os.environ.get("LAB_TRITON_RPC_URL")
    if not a.enable:
        raise SystemExit("off by default: pass --enable (paid Triton RPC calls; the owner's OK)")
    if not url:
        raise SystemExit("set LAB_TRITON_RPC_URL (it holds the token: never put it on the command line)")
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)  # noqa: E731
    if a.follow:
        run_follow(a.root, url, a.max_calls_per_day, a.interval, log=log)
    else:
        log(f"{run_once(a.root, url, a.max_calls_per_day, log=log)} names written")


if __name__ == "__main__":
    main()
