"""Fill and priority-fee probe for live paper (coordinator, 23 Oct; research/triton-services-edge-2026-10-20.md section
2.5 prio 1 and section 2.4 prio 4; the rule and its operationalisation: research/simfee-probe-rule-2026-10-23.md).

For every live paper entry and exit of K3 (F7 tp4_T600) and group H (F7 flow300_H6, flow300_tp100_H6) from 23 Oct on
(t >= 459,648,000, the journal day >= 2026-10-23) it builds the PumpSwap swap at the paper size as an UNSIGNED
transaction bundle and asks Triton's simulateBundle (Jito RPC) what it would do on the node's working bank ('tip'), with
skipSigVerify and replaceRecentBlockhash; and per trigger it asks getRecentPrioritizationFees for the pool at the
50th / 75th / 90th percentile. Research only: there is no key and no signing code, the signatures are random bytes and
the blockhash is zero, and only those two read methods can be posted (METHODS).

- Inputs: a backup copy of the live journal (sqlite backup API on a read-only connection: never the live file), and the
  pool's accounts from the recorder's newest direct PumpSwap trade row of that pool (ix_accounts; no RPC call). Only
  reversed pools (WSOL base) with the observed v1 layout (default coin creator, no cashback, two remaining accounts) are
  built; anything else is skipped with a class, without a call.
- Entry (a token buy = PumpSwap `sell` of WSOL): [setup: WSOL account, transfer, sync, token account, buyback account;
  swap: sell(size, min 1)]. Exit (a token sell = `buy_exact_quote_in` paid in tokens): the payer holds no tokens and a
  simulation cannot override state, so [setup; acquire: sell(A, min T); exit: buy_exact_quote_in(T, min 1)]. The exit's
  model error is measured on the state after our own acquire; the true exit is a model estimate (exit_est).
- Readout: the user's token / WSOL account amounts before and after the swap (u64 at data[64:72]), and the pool and vault
  accounts before it: the engine's own math (backtest.venues) on exactly that state gives M (model error, bps); D compares
  with the journal's fill (model error plus the drift since the decision: we simulate at receipt, 20-90 s later).
- Meter (fail-closed): <LAB>/probe/simfee/state.json per UTC day, each call counted and saved before it is sent; at most
  5,000 calls a day (fee calls only below 4,000), $0.25 a day estimated, 600 an hour; exit 76 (cap), 77 (runaway),
  78 (errors or an unreadable state). A 429 waits >= 10 s with exponential backoff.
- Default is a DRY RUN: no URL is read, every socket connect raises, and the exact request bodies go to requests.jsonl.
  --enable needs the approval file, LAB_TRITON_RPC_URL (https, *.rpcpool.com; it holds the token and is never printed)
  and --payer, a funded on-curve system account the owner chose (no key needed: the signature check is skipped).

  python -m paper.simfee_probe --lab <LAB> --payer <PUBKEY> [--enable] [--follow] [--interval 30] [--max-calls N]
  python -m paper.simfee_probe --lab <LAB> --summary         (the rule's evaluation over every run)
"""

import argparse
import base64
import datetime as dt
import glob
import hashlib
import json
import os
import random
import socket
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import duckdb
import numpy as np

from backtest import pda
from backtest.venues import Pool, rev_buy_token, rev_sell_token

from . import txwire

RULE = "research/simfee-probe-rule-2026-10-23.md"
RULE_SHA = "5e434eccabc86511a7eba566b7f78fcf117cb82cab9afe0e243b20bac1a5cd77"
FLOOR = 459_648_000  # the first slot after the clean lock (epoch 1064, Alpenglow)
DAY0 = "2026-10-23"
FAMILY = "F7"
VARIANTS = ("tp4_T600", "flow300_H6", "flow300_tp100_H6")  # K3 and group H (N2_F7 is another family)
METHODS = frozenset({"simulateBundle", "getRecentPrioritizationFees"})
USER_AGENT = "solana-lab-research"
PCTS = (5000, 7500, 9000)
CAP_DAY, FEE_UNTIL, HOUR_CAP, USD_CAP = 5000, 4000, 600, 0.25
USD_PER_CALL, USD_PER_GB = 10 / 1e6, 0.08  # Triton's standard RPC rate (unverified for simulateBundle)
PACE_S, MAX_CONSEC = 0.5, 5
EXIT_CAP, EXIT_RUNAWAY, EXIT_ERRORS = 76, 77, 78
CU_SETUP, CU_SWAP = 80_000, 400_000
ACQUIRE_MIN = 100_000_000  # lamports: the exit's acquire is max(0.1 SOL, 1.5 x the journal's proceeds)
SCAN_SEGMENTS = 120  # recorder segments searched back for a pool's trade row (~2 h)
AMM, WSOL, TOKEN, ATAP, SYSTEM = pda.PUMP_AMM, pda.WSOL, pda.TOKEN, pda.ATA_PROGRAM, txwire.SYSTEM
DEFAULT_KEY = pda.DEFAULT_KEY
IDL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extractor", "idl", "pump_amm.json")
DIRECT = ("sell", "buy", "buy_exact_quote_in")  # v1 rows whose ix_accounts name every account
# Swap failures that a market state explains (the rule's 'uitval'); every other failure is structural (the probe's).
MARKET_CODES = {6001: "ZeroBaseAmount", 6002: "ZeroQuoteAmount", 6003: "TooLittlePoolTokenLiquidity", 6004: "ExceededSlippage",
                6016: "BuyMoreBaseAmountThanPoolReserves", 6020: "DisabledBuy", 6021: "DisabledSell",
                6039: "BuyNotEnoughQuoteTokensToCoverFees", 6040: "BuySlippageBelowMinBaseAmountOut", 6063: "InsufficientRealQuoteReserves"}


def _idl():
    with open(IDL) as f:
        idl = json.load(f)
    ixs = {i["name"]: i for i in idl["instructions"]}
    out = {}
    for name in ("sell", "buy_exact_quote_in"):
        ix = ixs[name]
        out[name] = (bytes(ix["discriminator"]), [(a["name"], bool(a.get("signer")), bool(a.get("writable"))) for a in ix["accounts"]])
    return out


SWAPS = _idl()  # {name: (discriminator, [(role, signer, writable)])}: the pinned IDL (lab/extractor/idl/pump_amm.json)


class Stop(Exception):
    def __init__(self, code, why):
        super().__init__(why)
        self.code = code


# ----------------------------------------------------------------------------- the journal (a copy)


def snapshot(journal_db, dst):
    """A consistent copy of the live journal: the sqlite backup API from a read-only connection, closed at once."""
    src = sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True)
    try:
        src.execute("PRAGMA query_only=1")
        tmp = dst + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)
    return dst


def journal_rows(copy):
    """(entries, exits, triggers) from the copy: K3 and H only, t >= FLOOR and the row's own day >= DAY0. Exits without a
    real sale (restart_lost, entry_slippage, written_off, proceeds 0) are not simulated."""
    con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    v = ", ".join(f"'{x}'" for x in VARIANTS)
    base = f"o.family = '{FAMILY}' AND o.variant IN ({v}) AND o.t >= {FLOOR}"
    try:
        entries = [dict(r) for r in con.execute(f"""
            SELECT o.family, o.variant, o.key AS pool, o.t, o.day, o.entry_slot, o.size, o.status, s.orientation, s.venue,
                   f.tokens AS j_tokens, f.slot AS fill_slot
            FROM orders o JOIN signals s ON s.family = o.family AND s.variant = o.variant AND s.key = o.key AND s.t = o.t
            LEFT JOIN fills f ON f.family = o.family AND f.variant = o.variant AND f.key = o.key AND f.t = o.t AND f.side = 'buy'
            WHERE {base} AND o.day >= '{DAY0}' AND o.status IN ('filled', 'entry_failed') ORDER BY o.entry_slot""")]
        exits = [dict(r) for r in con.execute(f"""
            SELECT o.family, o.variant, o.key AS pool, o.t, o.day, o.entry_slot, o.exit_decision_slot, o.exit_slot, o.exit_reason,
                   o.tokens, o.proceeds, s.orientation, s.venue, f.quote AS j_sol
            FROM exits o JOIN signals s ON s.family = o.family AND s.variant = o.variant AND s.key = o.key AND s.t = o.t
            JOIN fills f ON f.family = o.family AND f.variant = o.variant AND f.key = o.key AND f.t = o.t AND f.side = 'sell'
            WHERE {base} AND o.day >= '{DAY0}' AND f.quote > 0 AND o.tokens > 0
              AND o.exit_reason NOT IN ('restart_lost', 'entry_slippage', 'written_off') ORDER BY o.exit_slot""")]
        triggers = [dict(r) for r in con.execute(f"""
            SELECT DISTINCT o.key AS pool, o.t FROM signals o
            WHERE {base} AND o.day >= '{DAY0}' AND o.venue = 'pool' ORDER BY o.t""")]
    finally:
        con.close()
    return entries, exits, triggers


# ----------------------------------------------------------------------------- the pool's accounts (the recorder)


class Recorder:
    """The newest direct v1 PumpSwap trade row of a pool at or before a slot, from the recorder's complete segments
    (then paper.keeper's kept copies): the pool-side accounts, the fee bps and the virtual quote reserves."""

    COLS = ("slot, tx_index, parent_ix_name, ix_accounts, coin_creator, lp_fee_basis_points, protocol_fee_basis_points, "
            "coin_creator_fee_basis_points, cashback_fee_basis_points, virtual_quote_reserves")

    def __init__(self, root):
        self.root = root
        self.segs = {}  # seq -> (first_slot, last_slot, dir), complete segments only
        self.cache = {}

    def refresh(self):
        for d in glob.glob(os.path.join(self.root, "segments", "*")):
            seq = os.path.basename(d)
            if seq in self.segs or not seq.isdigit():
                continue
            try:
                with open(os.path.join(d, "_segment.json")) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if m.get("complete"):
                self.segs[seq] = (m["first_slot"], m["last_slot"], d)
        for seq in [s for s, (_, _, d) in self.segs.items() if not os.path.isdir(d)]:
            del self.segs[seq]  # removed by retention

    def _query(self, files, pool, upto):
        files = [f for f in files if os.path.exists(f)]
        if not files:
            return None
        con = duckdb.connect()
        con.execute("SET memory_limit = '256MB'; SET threads = 1")
        lst = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
        r = con.execute(f"""SELECT {self.COLS} FROM read_parquet({lst}, union_by_name = true)
                            WHERE ix_pool = ? AND slot <= ? AND parent_ix_name IN ('sell', 'buy', 'buy_exact_quote_in')
                            ORDER BY slot DESC, tx_index DESC LIMIT 1""", [pool, upto]).fetchone()
        con.close()
        return None if r is None else dict(zip([c.strip() for c in self.COLS.split(",")], r))

    def row(self, pool, upto):
        cand = sorted(((int(s), d) for s, (lo, _, d) in self.segs.items() if lo <= upto), reverse=True)[:SCAN_SEGMENTS]
        for i in range(0, len(cand), 20):
            files = [os.path.join(d, "pump_amm", ev, "*.parquet") for _, d in cand[i:i + 20] for ev in ("BuyEvent", "SellEvent")]
            files = [f for pat in files for f in glob.glob(pat)]
            r = self._query(files, pool, upto)
            if r:
                return r
        kept = glob.glob(os.path.join(os.path.dirname(self.root), "journal", "paper-k3", "kept", "*", "signal_trades",
                                      "pump_amm__*Event.parquet"))
        return self._query(kept, pool, upto)


def pool_accounts(row, pool):
    """The pool-side accounts of a direct v1 row, checked against their derivations; (accounts, None) or (None, why)."""
    try:
        acc = json.loads(row["ix_accounts"])
    except (TypeError, ValueError):
        return None, "no_ix_accounts"
    extra = acc.get("_extra") or []
    if acc.get("pool") != pool:
        return None, "layout_pool"
    if acc.get("base_mint") != WSOL:
        return None, "unsupported_not_reversed"
    if (row.get("coin_creator") or DEFAULT_KEY) != DEFAULT_KEY or (row.get("cashback_fee_basis_points") or 0) > 0:
        return None, "unsupported_layout"
    if len(extra) != 2:
        return None, "unsupported_remaining_accounts"
    need = [r for r, _, _ in SWAPS["sell"][1] if r not in ("user", "user_base_token_account", "user_quote_token_account")]
    if any(not acc.get(r) for r in need):
        return None, "layout_missing_account"
    qp, mint = acc["quote_token_program"], acc["quote_mint"]
    checks = (acc["pool_base_token_account"] == pda.ata(pool, WSOL, acc["base_token_program"]),
              acc["pool_quote_token_account"] == pda.ata(pool, mint, qp),
              extra[1] == pda.ata(extra[0], mint, qp),
              acc["program"] == AMM)
    if not all(checks):
        return None, "layout_derivation"
    acc["global_volume_accumulator"] = acc.get("global_volume_accumulator") or pda._pda([b"global_volume_accumulator"], AMM)
    acc["_buyback"] = (extra[0], extra[1])
    return acc, None


# ----------------------------------------------------------------------------- the transactions


def user_accounts(payer, acc):
    return {"user": payer, "user_base_token_account": pda.ata(payer, WSOL, TOKEN),
            "user_quote_token_account": pda.ata(payer, acc["quote_mint"], acc["quote_token_program"]),
            "user_volume_accumulator": pda._pda([b"user_volume_accumulator", pda.b58decode(payer)], AMM)}


def _swap_ix(name, acc, user, a, b, data_extra=b""):
    disc, roles = SWAPS[name]
    metas = [((user[r] if r in user else acc[r]), s, w) for r, s, w in roles]
    bw, bata = acc["_buyback"]
    metas += [(bw, False, False), (bata, False, True)]  # remaining accounts: the buyback fee recipient and its token account
    return (AMM, metas, disc + struct.pack("<QQ", a, b) + data_extra)


def _setup(payer, acc, user, lamports):
    wsol, tok = user["user_base_token_account"], user["user_quote_token_account"]
    bw, bata = acc["_buyback"]
    mint, qp = acc["quote_mint"], acc["quote_token_program"]

    def create(ata, owner, m, prog):
        return (ATAP, [(payer, True, True), (ata, False, True), (owner, False, False), (m, False, False), (SYSTEM, False, False),
                       (prog, False, False)], bytes([1]))  # CreateIdempotent

    return [txwire.cu_limit(CU_SETUP), create(wsol, payer, WSOL, TOKEN), txwire.transfer(payer, wsol, lamports),
            (TOKEN, [(wsol, False, True)], bytes([17])),  # SyncNative
            create(tok, payer, mint, qp), create(bata, bw, mint, qp)]


def _tx(payer, ixs):
    msg, nsig, _ = txwire.compile_message(payer, ixs)
    return txwire.wire(msg, [random.randbytes(64) for _ in range(nsig)])


def entry_bundle(payer, acc, size):
    """[setup, swap]: buy the token for `size` lamports in a reversed pool = PumpSwap sell(size, min 1)."""
    user = user_accounts(payer, acc)
    return [_tx(payer, _setup(payer, acc, user, size)),
            _tx(payer, [txwire.cu_limit(CU_SWAP), _swap_ix("sell", acc, user, size, 1)])], user


def exit_bundle(payer, acc, tokens, acquire):
    """[setup, acquire, exit]: get at least `tokens` with sell(acquire, min tokens), then sell exactly `tokens` =
    buy_exact_quote_in(tokens, min 1) (24 bytes of arguments: no track_volume, as the live direct calls)."""
    user = user_accounts(payer, acc)
    return [_tx(payer, _setup(payer, acc, user, acquire)),
            _tx(payer, [txwire.cu_limit(CU_SWAP), _swap_ix("sell", acc, user, acquire, tokens)]),
            _tx(payer, [txwire.cu_limit(CU_SWAP), _swap_ix("buy_exact_quote_in", acc, user, tokens, 1)])], user


def bundle_request(txs, pre, post, rid):
    cfg = {"simulationBank": "tip", "skipSigVerify": True, "replaceRecentBlockhash": True, "transactionEncoding": "base64",
           "preExecutionAccountsConfigs": [None if a is None else {"encoding": "base64", "addresses": a} for a in pre],
           "postExecutionAccountsConfigs": [None if a is None else {"encoding": "base64", "addresses": a} for a in post]}
    return {"jsonrpc": "2.0", "id": rid, "method": "simulateBundle",
            "params": [{"encodedTransactions": [base64.b64encode(t).decode() for t in txs]}, cfg]}


def fee_request(pool, pct, rid):
    return {"jsonrpc": "2.0", "id": rid, "method": "getRecentPrioritizationFees", "params": [[pool], {"percentile": pct}]}


def selfcheck(txs, payer):
    """Offline: every transaction decodes back, signs with the payer only, fits the packet limit and differs from the others
    (the node rejects duplicate messages in a bundle)."""
    msgs = set()
    for tx in txs:
        sigs, header, keys, _, ixs = txwire.decode(tx)
        if header[0] != 1 or keys[0] != payer or len(sigs) != 1 or len(tx) > txwire.MAX_TX_BYTES:
            return False
        msgs.add(tx[65:])
    return len(msgs) == len(txs)


# ----------------------------------------------------------------------------- readout and the engine's math


def spl_amount(data):
    return struct.unpack_from("<Q", data, 64)[0] if data and len(data) >= 72 else None


def decode_pool(data):
    """The PumpSwap Pool account (pinned IDL, Borsh after the 8-byte discriminator): mints, vaults, coin creator, flags and
    the virtual quote reserves (i128)."""
    if not data or len(data) < 261:
        return None
    k = lambda a: pda.b58encode(data[a:a + 32])  # noqa: E731
    return {"base_mint": k(43), "quote_mint": k(75), "base_vault": k(139), "quote_vault": k(171), "coin_creator": k(211),
            "mayhem": data[243], "cashback": data[244], "vqr": int.from_bytes(data[245:261], "little", signed=True), "len": len(data)}


def _accounts(res, i):
    """The i-th transaction's (pre, post) accounts as [(owner, data bytes) or None]."""
    tr = res["value"]["transactionResults"][i]

    def dec(lst):
        return [None if a is None else (a.get("owner"), base64.b64decode(a["data"][0])) for a in (lst or [])]

    return dec(tr.get("preExecutionAccounts")), dec(tr.get("postExecutionAccounts"))


def engine_pool(acc_row, pool_acc, base_vault, quote_vault):
    """The engine's Pool on a simulated state: b = the base (WSOL) vault, e = the quote vault + the virtual quote reserves,
    the fee bps of the pool's newest trade row (as the engine takes them, streams.pool_leg)."""
    p = decode_pool(pool_acc)
    b, q = spl_amount(base_vault), spl_amount(quote_vault)
    if p is None or b is None or q is None:
        return None, p
    fees = [int(acc_row.get(c) or 0) for c in ("lp_fee_basis_points", "protocol_fee_basis_points", "coin_creator_fee_basis_points",
                                               "cashback_fee_basis_points")]
    return Pool(b, q + p["vqr"], *fees, virt=p["vqr"]), p


def bps(a, b):
    return None if a is None or not b else round(1e4 * (a - b) / b, 2)


def classify(res, swap_i):
    """(class, detail): ok, rpc, structural (setup, acquire, account or program errors) or market (MARKET_CODES on the swap)."""
    if "error" in res:
        return "rpc", {"code": res["error"].get("code")}
    v = res.get("result", {}).get("value") or {}
    trs = v.get("transactionResults") or []
    if v.get("summary") == "succeeded" and len(trs) > swap_i and trs[swap_i].get("err") is None:
        return "ok", None
    i = len(trs) - 1
    err = trs[i].get("err") if trs else None
    code = None
    if isinstance(err, dict) and "InstructionError" in err:
        e = err["InstructionError"][1]
        code = e.get("Custom") if isinstance(e, dict) else None
    detail = {"tx": i, "err": err, "code": code, "name": MARKET_CODES.get(code)}
    if i == swap_i and code in MARKET_CODES:
        return "market", detail
    return "structural", detail


# ----------------------------------------------------------------------------- the meter and the RPC


class Meter:
    """The UTC day's calls, saved atomically before each call (a crash never undercounts); an unreadable state stops."""

    def __init__(self, path, max_run=None, now=time.time):
        self.path, self.max_run, self.now, self.run_calls = path, max_run, now, 0
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.s = json.load(f)
            except (OSError, ValueError):
                raise Stop(EXIT_ERRORS, "the meter state is unreadable (fail closed)") from None
        else:
            self.s = {}
        self._day()

    def _day(self):
        today = dt.datetime.fromtimestamp(self.now(), dt.timezone.utc).strftime("%Y-%m-%d")
        if self.s.get("day") != today:
            self.s = {"day": today, "calls": 0, "calls_sim": 0, "calls_fee": 0, "req_bytes": 0, "resp_bytes": 0, "http_429": 0,
                      "hour": [], "fee_off": self.s.get("fee_off")}

    def usd(self):
        return self.s["calls"] * USD_PER_CALL + (self.s["req_bytes"] + self.s["resp_bytes"]) * USD_PER_GB / 1e9

    def reserve(self, kind):
        """True and counted, or False (fee calls past FEE_UNTIL or switched off); Stop at a cap."""
        self._day()
        t = self.now()
        self.s["hour"] = [x for x in self.s["hour"] if x > t - 3600]
        if self.s["calls"] >= CAP_DAY or self.usd() >= USD_CAP:
            raise Stop(EXIT_CAP, f"daily cap: {self.s['calls']} calls, ${self.usd():.4f}")
        if self.max_run is not None and self.run_calls >= self.max_run:
            raise Stop(EXIT_CAP, f"--max-calls {self.max_run} reached")
        if len(self.s["hour"]) >= HOUR_CAP:
            raise Stop(EXIT_RUNAWAY, f"{len(self.s['hour'])} calls in the last hour")
        if kind == "fee" and (self.s["calls"] >= FEE_UNTIL or self.s.get("fee_off")):
            return False
        self.s["calls"] += 1
        self.s["calls_" + kind] += 1
        self.s["hour"].append(t)
        self.run_calls += 1
        self.save()
        return True

    def add_bytes(self, req, resp):
        self.s["req_bytes"] += req
        self.s["resp_bytes"] += resp
        self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


def check_url(url):
    """https and a Triton host, checked without ever printing the URL (it holds the token)."""
    u = urllib.parse.urlsplit(url or "")
    if u.scheme != "https" or not (u.hostname or "").endswith(".rpcpool.com"):
        raise SystemExit("LAB_TRITON_RPC_URL is missing or not an https *.rpcpool.com URL (not printed)")
    return url


def http_post(url, body, timeout=20):
    """(status, rate-limit headers, raw body). Errors carry the exception class only: a URL error can contain the URL."""
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k: v for k, v in (e.headers or {}).items() if k.lower().startswith("x-ratelimit")}, b""
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"RPC call failed: {e.__class__.__name__}") from None


class Rpc:
    def __init__(self, url, meter, post=http_post, sleep=time.sleep, log=print):
        self.url, self.meter, self.post, self.sleep, self.log = url, meter, post, sleep, log
        self.consec = 0

    def call(self, req, kind):
        """The response JSON, or None when the meter declines (a fee call past FEE_UNTIL). Counted before it is sent."""
        if req["method"] not in METHODS:
            raise ValueError(f"method {req['method']!r} is not allowed")
        body = json.dumps(req).encode()
        backoff = 10
        while True:
            if not self.meter.reserve(kind):
                return None
            try:
                status, rl, raw = self.post(self.url, body)
            except RuntimeError as e:
                self.meter.add_bytes(len(body), 0)
                self._error(str(e))
                return {"error": {"code": "transport"}}
            self.meter.add_bytes(len(body), len(raw))
            if status == 429:
                self.meter.s["http_429"] += 1
                self.meter.save()
                self.log(f"429 rate limited {rl}: waiting {backoff} s")
                self.sleep(backoff)
                backoff = min(300, backoff * 2)
                continue
            if status != 200:
                self._error(f"HTTP {status}")
                return {"error": {"code": f"http_{status}"}}
            try:
                res = json.loads(raw)
            except ValueError:
                self._error("unparsable response")
                return {"error": {"code": "json"}}
            if "error" in res:
                self._error(f"RPC error {res['error'].get('code')}")
            else:
                self.consec = 0
            self.sleep(PACE_S)
            return res

    def _error(self, what):
        self.consec += 1
        self.log(f"call failed: {what} ({self.consec} in a row)")
        if self.consec >= MAX_CONSEC:
            raise Stop(EXIT_ERRORS, f"{self.consec} failed calls in a row")


# ----------------------------------------------------------------------------- one pass


def _fee_summary(res, t):
    vals = (res or {}).get("result") or []
    slots = [(v["slot"], v["prioritizationFee"]) for v in vals if isinstance(v, dict) and "slot" in v]
    if not slots:
        return {"n_slots": 0}
    win = [f for s, f in slots if t <= s <= t + 10] or [f for _, f in slots[:10]]
    at_t = next((f for s, f in slots if s == t), None)
    return {"n_slots": len(slots), "min_slot": slots[0][0], "max_slot": slots[-1][0], "covers_t": slots[0][0] <= t,
            "at_t": at_t, "window_max": max(win), "window_median": float(np.median(win)), "values": slots}


def simulate_entry(e, acc, row, payer, rpc, rid):
    txs, user = entry_bundle(payer, acc, e["size"])
    pre = [None, [acc["pool"], acc["pool_base_token_account"], acc["pool_quote_token_account"], user["user_quote_token_account"]]]
    post = [None, [user["user_quote_token_account"], user["user_base_token_account"]]]
    req = bundle_request(txs, pre, post, rid)
    out = {"selfcheck": selfcheck(txs, payer), "instruction": "sell", "size": e["size"], "j_tokens": e["j_tokens"]}
    if rpc is None or not out["selfcheck"]:
        return req, out
    res = rpc.call(req, "sim")
    if res is None:
        return req, dict(out, cls="declined")
    cls, detail = classify(res, 1)
    out.update(cls=cls, detail=detail)
    v = res.get("result") or {}
    out["context_slot"] = (v.get("context") or {}).get("slot")
    if cls != "ok":
        return req, out
    (pre1, post1) = _accounts(v, 1)
    trs = v["value"]["transactionResults"]
    sim = (spl_amount(post1[0][1]) or 0) - ((spl_amount(pre1[3][1]) if pre1[3] else 0) or 0)
    p, dec = engine_pool(row, *(a[1] if a else None for a in pre1[:3]))
    eng = rev_buy_token(p, e["size"]) if p else None
    out.update(units=[tr.get("unitsConsumed") for tr in trs], sim_out=sim, eng_same_state=eng.tokens if eng else None,
               M_bps=bps(sim, eng.tokens if eng else None), D_bps=bps(sim, e["j_tokens"]), pool_state=dec and {k: dec[k] for k in ("vqr", "len", "mayhem", "cashback")},
               pool_decode_ok=bool(dec and dec["base_vault"] == acc["pool_base_token_account"] and dec["quote_vault"] == acc["pool_quote_token_account"]),
               would_miss_tol=None if not e["j_tokens"] else sim < 0.95 * e["j_tokens"])
    return req, out


def simulate_exit(x, acc, row, payer, rpc, rid):
    acquire = max(ACQUIRE_MIN, -(-3 * x["j_sol"] // 2))
    txs, user = exit_bundle(payer, acc, x["tokens"], acquire)
    vaults = [acc["pool"], acc["pool_base_token_account"], acc["pool_quote_token_account"]]
    pre = [None, vaults, vaults + [user["user_base_token_account"], user["user_quote_token_account"]]]
    post = [None, [user["user_quote_token_account"]], [user["user_base_token_account"], user["user_quote_token_account"]]]
    req = bundle_request(txs, pre, post, rid)
    out = {"selfcheck": selfcheck(txs, payer), "instruction": "buy_exact_quote_in", "tokens": x["tokens"], "acquire": acquire,
           "j_sol": x["j_sol"]}
    if rpc is None or not out["selfcheck"]:
        return req, out
    res = rpc.call(req, "sim")
    if res is None:
        return req, dict(out, cls="declined")
    cls, detail = classify(res, 2)
    if detail and detail.get("tx") in (0, 1):
        detail["stage"] = "setup" if detail["tx"] == 0 else "acquire"  # our own preparation: structural, never uitval
    out.update(cls=cls, detail=detail)
    v = res.get("result") or {}
    out["context_slot"] = (v.get("context") or {}).get("slot")
    if cls != "ok":
        return req, out
    pre1, _ = _accounts(v, 1)
    pre2, post2 = _accounts(v, 2)
    trs = v["value"]["transactionResults"]
    sim = (spl_amount(post2[0][1]) or 0) - ((spl_amount(pre2[3][1]) if pre2[3] else 0) or 0)
    p2, dec = engine_pool(row, *(a[1] if a else None for a in pre2[:3]))
    eng = rev_sell_token(p2, x["tokens"]) if p2 else None
    p1, _ = engine_pool(row, *(a[1] if a else None for a in pre1[:3]))
    est = rev_sell_token(p1, x["tokens"]) if p1 else None  # the exit on the tip state before our acquire: model-based
    out.update(units=[tr.get("unitsConsumed") for tr in trs], sim_out=sim, eng_same_state=eng.trader_quote if eng else None,
               M_bps=bps(sim, eng.trader_quote if eng else None), exit_est=est.trader_quote if est else None,
               D_bps=bps(est.trader_quote if est else None, x["j_sol"]), pool_state=dec and {k: dec[k] for k in ("vqr", "len", "mayhem", "cashback")},
               pool_decode_ok=bool(dec and dec["base_vault"] == acc["pool_base_token_account"] and dec["quote_vault"] == acc["pool_quote_token_account"]))
    return req, out


def run_pass(lab, state_dir, run_dir, payer, rpc, rec, log=print):
    """One pass over the journal copy: the fee calls of new triggers, then the new entries and exits. Returns the rows
    written. rpc None: a dry run (requests.jsonl only, nothing marked seen)."""
    copy = snapshot(os.path.join(lab, "journal", "paper-k3", "journal", "journal.sqlite"), os.path.join(state_dir, "journal-copy.sqlite"))
    entries, exits, triggers = journal_rows(copy)
    seen_path = os.path.join(state_dir, "seen.json")
    seen = set(json.load(open(seen_path))) if os.path.exists(seen_path) else set()
    rec.refresh()
    n, rid = 0, int(time.time() * 1000)

    def mark(key):
        if rpc is None:
            return
        seen.add(key)
        tmp = seen_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(seen), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, seen_path)

    def write(name, row):
        with open(os.path.join(run_dir, name), "a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    meter = rpc.meter if rpc else None
    fee_rows = []
    for tr in triggers:
        for pct in PCTS:
            key = f"fee|{tr['pool']}|{tr['t']}|{pct}"
            if key in seen:
                continue
            rid += 1
            req = fee_request(tr["pool"], pct, rid)
            if rpc is None:
                write("requests.jsonl", req)
                continue
            if meter.s.get("fee_off") or meter.s["calls"] >= FEE_UNTIL:
                break
            mark(key)
            t0 = time.time_ns()
            res = rpc.call(req, "fee")
            if res is None:
                break
            row = {"pool": tr["pool"], "t": tr["t"], "pct": pct, "unix_ns": t0, "latency_ms": (time.time_ns() - t0) / 1e6,
                   "error": res.get("error"), **_fee_summary(res, tr["t"])}
            write("fees.jsonl", row)
            fee_rows.append(row)
            n += 1
    _percentile_guard(meter, run_dir, log)
    for side, rows in (("entry", entries), ("exit", exits)):
        for x in rows:
            key = f"sim|{x['family']}|{x['variant']}|{x['pool']}|{x['t']}|{side}"
            if key in seen:
                continue
            base = {k: x[k] for k in ("family", "variant", "pool", "t", "day")}
            base.update(side=side, fill_slot=x["entry_slot"] if side == "entry" else x["exit_slot"],
                        status=x.get("status") or x.get("exit_reason"))
            if x.get("orientation") != "reversed" or x.get("venue") != "pool":
                write("sims.jsonl", dict(base, cls="skipped", why="not a reversed pool"))
                mark(key)
                continue
            upto = x["t"] if side == "entry" else x["exit_slot"]
            row = rec.row(x["pool"], upto)
            acc, why = pool_accounts(row, x["pool"]) if row else (None, "no_recorder_row")
            if acc is None:
                write("sims.jsonl", dict(base, cls="skipped", why=why))
                mark(key)
                continue
            rid += 1
            if rpc is not None:
                mark(key)
            t0 = time.time_ns()
            req, out = (simulate_entry if side == "entry" else simulate_exit)(x, acc, row, payer, rpc, rid)
            base.update(row_slot=row["slot"], fee_bps=[row.get(c) for c in ("lp_fee_basis_points", "protocol_fee_basis_points",
                                                                            "coin_creator_fee_basis_points", "cashback_fee_basis_points")])
            if rpc is None:
                write("requests.jsonl", req)
                write("selfcheck.jsonl", dict(base, selfcheck=out["selfcheck"]))
                continue
            out["latency_ms"] = (time.time_ns() - t0) / 1e6
            if out.get("context_slot") is not None:
                out["slots_late"] = out["context_slot"] - base["fill_slot"]
            write("sims.jsonl", dict(base, **out))
            n += 1
    return n


def _percentile_guard(meter, run_dir, log):
    """If the percentile is ignored, p50 and p90 are the same array: after 10 triggers with all three, stop fee calls."""
    if meter is None or meter.s.get("fee_off"):
        return
    path = os.path.join(run_dir, "fees.jsonl")
    if not os.path.exists(path):
        return
    by = {}
    for line in open(path):
        r = json.loads(line)
        by.setdefault((r["pool"], r["t"]), {})[r["pct"]] = [v for _, v in r.get("values") or []]
    full = [v for v in by.values() if all(p in v for p in PCTS)]
    if len(full) >= 10 and all(v[5000] == v[9000] for v in full[:10]):
        meter.s["fee_off"] = "the percentile looks ignored (p50 = p90 in the first 10 triggers)"
        meter.save()
        log(meter.s["fee_off"])


# ----------------------------------------------------------------------------- the rule's evaluation


def summary(lab, draws=10_000, seed=7):
    """The rule (research/simfee-probe-rule-2026-10-23.md): per side, n, the median of M with a bootstrap 95 % CI, the
    market and structural failure shares; D, slots_late and would_miss_tol beside it (report only)."""
    rows = [json.loads(line) for p in sorted(glob.glob(os.path.join(lab, "probe", "simfee-*", "sims.jsonl"))) for line in open(p)]
    rng = np.random.default_rng(seed)
    out = {}
    for side in ("entry", "exit"):
        r = [x for x in rows if x.get("side") == side and x.get("cls") not in (None, "skipped", "declined")]
        ok = [x for x in r if x["cls"] == "ok" and x.get("M_bps") is not None]
        m = np.array([x["M_bps"] for x in ok])
        n_ok, n_mkt = sum(x["cls"] == "ok" for x in r), sum(x["cls"] == "market" for x in r)
        res = {"attempted": len(r), "ok": n_ok, "ok_with_M": len(ok),
               "uitval_pct": round(100 * n_mkt / (n_ok + n_mkt), 2) if n_ok + n_mkt else None,  # market / (ok + market)
               "structural_pct": round(100 * sum(x["cls"] == "structural" for x in r) / len(r), 2) if r else None,
               "rpc_pct": round(100 * sum(x["cls"] == "rpc" for x in r) / len(r), 2) if r else None}
        if len(m):
            boot = np.median(m[rng.integers(0, len(m), size=(draws, len(m)))], axis=1)
            lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
            res.update(M_median=float(np.median(m)), M_lo=lo, M_hi=hi,
                       D_median=float(np.median([x["D_bps"] for x in ok if x.get("D_bps") is not None] or [np.nan])),
                       slots_late_median=float(np.median([x["slots_late"] for x in ok if x.get("slots_late") is not None] or [np.nan])),
                       systematic=bool(len(m) >= 30 and abs(float(np.median(m))) > 25 and (lo > 0 or hi < 0)))
        res["uitval"] = bool(res["uitval_pct"] is not None and res["uitval_pct"] > 3)
        res["probe_invalid"] = bool(res["structural_pct"] is not None and res["structural_pct"] > 3)
        out[side] = res
    out["rule"] = {"file": RULE, "sha256": RULE_SHA}
    return out


# ----------------------------------------------------------------------------- main


def _dry_guard():
    """A dry run cannot reach the network: no URL, and every socket connect raises."""
    os.environ.pop("LAB_TRITON_RPC_URL", None)

    def refuse(*a, **k):
        raise OSError("dry run: no network")

    socket.socket.connect = refuse
    socket.create_connection = refuse


def _check_payer(payer):
    try:
        raw = pda.b58decode(payer)
    except Exception:  # noqa: BLE001
        raise SystemExit("--payer is not a base58 public key") from None
    if not pda.on_curve(raw) or payer in (AMM, TOKEN, ATAP, SYSTEM, WSOL, DEFAULT_KEY):
        raise SystemExit("--payer must be an on-curve wallet address (not a program or a PDA)")
    return payer


def _refuse_recorder_dir(path):
    if os.path.exists(os.path.join(path, "budget.json")):
        raise SystemExit(f"{path} holds budget.json (a recorder root): refused")


def main(argv=None, post=http_post):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", required=True, help="the lab data root (journal/, recorder/, probe/)")
    ap.add_argument("--payer", help="the owner's choice: a funded on-curve system account (public key only)")
    ap.add_argument("--enable", action="store_true", help="paid Triton calls (approval file + LAB_TRITON_RPC_URL)")
    ap.add_argument("--approval", default=os.path.expanduser("~/.config/solana-lab/simfee-probe.approved"))
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--max-calls", type=int, default=None, help="a cap for this run (e.g. 10 for the first test)")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    if a.summary:
        print(json.dumps(summary(a.lab), indent=1))
        return 0
    if not a.payer:
        raise SystemExit("--payer is required (the owner's choice; a dry run builds the transactions for it too)")
    payer = _check_payer(a.payer)
    state_dir = os.path.join(a.lab, "probe", "simfee")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%MZ")
    run_dir = os.path.join(a.lab, "probe", f"simfee-{stamp}" + ("" if a.enable else "-dry"))
    for d in (state_dir, run_dir):
        os.makedirs(d, exist_ok=True)
        _refuse_recorder_dir(d)
    log_f = open(os.path.join(run_dir, "events.log"), "a")

    def log(m):
        line = f"{dt.datetime.now(dt.timezone.utc).strftime('%FT%TZ')} {m}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    rec = Recorder(os.path.join(a.lab, "recorder"))
    if not a.enable:
        _dry_guard()
        n = run_pass(a.lab, state_dir, run_dir, payer, None, rec, log)
        reqs = sum(1 for _ in open(os.path.join(run_dir, "requests.jsonl"))) if os.path.exists(os.path.join(run_dir, "requests.jsonl")) else 0
        log(f"DRY RUN: {reqs} requests built (requests.jsonl), {n} calls made; nothing was sent")
        return 0
    if not os.path.exists(a.approval):
        raise SystemExit(f"no approval file {a.approval} (LAB review and the coordinator's OK first)")
    url = check_url(os.environ.get("LAB_TRITON_RPC_URL"))
    code = 0
    try:
        meter = Meter(os.path.join(state_dir, "state.json"), a.max_calls)
        rpc = Rpc(url, meter, post=post, log=log)
        while True:
            if not os.path.exists(a.approval) or os.path.exists(os.path.join(a.lab, "locks", "disk-stopped")):
                log("stopped: the approval file is gone or the disk guard stopped lab jobs")
                break
            n = run_pass(a.lab, state_dir, run_dir, payer, rpc, rec, log)
            log(f"pass: {n} rows; today {meter.s['calls']} calls (sim {meter.s['calls_sim']}, fee {meter.s['calls_fee']}), ${meter.usd():.4f}")
            if not a.follow:
                break
            time.sleep(a.interval)
    except Stop as s:
        log(f"STOP {s.code}: {s}")
        code = s.code
    finally:
        log_f.close()
        with open(os.path.join(run_dir, "SHA256SUMS"), "w") as f:
            for name in sorted(os.listdir(run_dir)):
                p = os.path.join(run_dir, name)
                if name != "SHA256SUMS" and os.path.isfile(p):
                    f.write(f"{hashlib.sha256(open(p, 'rb').read()).hexdigest()}  {name}\n")
    return code


if __name__ == "__main__":
    def _hook(t, e, tb):  # the class only: a message could carry the URL
        print(f"failed: {t.__name__}", file=sys.stderr)

    sys.excepthook = _hook
    sys.exit(main())
