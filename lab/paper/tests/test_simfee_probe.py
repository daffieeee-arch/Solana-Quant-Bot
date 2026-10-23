"""The fill and priority-fee probe (paper.simfee_probe, paper.txwire): the unsigned wire format, the swaps against the pinned
IDL, the pool-account guards, the meter's caps (counted before the call, fail-closed), the RPC allowlist and error
masking, the readout against the engine's math on the same state, the journal filters, the dry run's network guard and
the rule's evaluation. No test touches the network."""

import base64
import hashlib
import json
import os
import socket
import sqlite3
import struct

import pytest

from backtest import pda
from backtest.venues import Pool, rev_buy_token, rev_sell_token
from paper import journal as J
from paper import simfee_probe as sp
from paper import txwire

AMM, WSOL, TOKEN = pda.PUMP_AMM, pda.WSOL, pda.TOKEN
T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
CANARY = "https://canary-token-abc123.mainnet.rpcpool.com/SECRETTOKEN"


def key(i):
    return pda.b58encode(hashlib.sha256(b"key" + bytes([i])).digest())


def payer():
    i = 0
    while True:
        b = hashlib.sha256(b"payer" + bytes([i])).digest()
        if pda.on_curve(b):
            return pda.b58encode(b)
        i += 1


def fake_acc(qp=T22):
    pool, mint, recipient, bw = key(1), key(2), key(3), key(4)
    cc_auth = pda._pda([b"creator_vault", bytes(32)], AMM)
    acc = {"pool": pool, "global_config": pda._pda([b"global_config"], AMM), "base_mint": WSOL, "quote_mint": mint,
           "pool_base_token_account": pda.ata(pool, WSOL, TOKEN), "pool_quote_token_account": pda.ata(pool, mint, qp),
           "protocol_fee_recipient": recipient, "protocol_fee_recipient_token_account": pda.ata(recipient, mint, qp),
           "base_token_program": TOKEN, "quote_token_program": qp, "system_program": txwire.SYSTEM,
           "associated_token_program": pda.ATA_PROGRAM, "event_authority": pda._pda([b"__event_authority"], AMM), "program": AMM,
           "coin_creator_vault_ata": pda.ata(cc_auth, mint, qp), "coin_creator_vault_authority": cc_auth,
           "fee_config": key(5), "fee_program": key(6), "user": key(7), "user_base_token_account": key(8),
           "user_quote_token_account": key(9), "_extra": [bw, pda.ata(bw, mint, qp)]}
    row = {"ix_accounts": json.dumps(acc), "coin_creator": pda.DEFAULT_KEY, "cashback_fee_basis_points": 0, "lp_fee_basis_points": 25,
           "protocol_fee_basis_points": 5, "coin_creator_fee_basis_points": 0, "slot": 460_300_000}
    return row, pool


def test_compact_u16_and_the_round_trip():
    for n, raw in ((0, "00"), (127, "7f"), (128, "8001"), (16383, "ff7f"), (16384, "808001")):
        assert txwire.cu16(n).hex() == raw and txwire.read_cu16(txwire.cu16(n), 0) == (n, len(raw) // 2)
    p = payer()
    ixs = [txwire.cu_limit(1000), txwire.transfer(p, key(1), 5), (key(2), [(key(3), False, False), (p, True, False)], b"\x01\x02")]
    msg, nsig, keys = txwire.compile_message(p, ixs)
    tx = txwire.wire(msg, [bytes(64)])
    sigs, header, k2, bh, out = txwire.decode(tx)
    assert (nsig, header, k2, bh) == (1, (1, 0, 4), keys, bytes(32))  # readonly: ComputeBudget, System, key 2, key 3
    assert out[1] == (txwire.SYSTEM, [(p, True, True), (key(1), False, True)], struct.pack("<IQ", 2, 5))
    assert out[2][1] == [(key(3), False, False), (p, True, True)]  # the payer is a writable signer for every instruction
    with pytest.raises(ValueError):
        txwire.wire(msg, [bytes(63)])


def test_the_swaps_come_from_the_pinned_idl():
    for name, n in (("sell", 21), ("buy_exact_quote_in", 23)):
        disc, roles = sp.SWAPS[name]
        assert disc == hashlib.sha256(f"global:{name}".encode()).digest()[:8] and len(roles) == n
        assert [r for r, s, _ in roles if s] == ["user"]


def test_the_entry_and_exit_bundles():
    row, pool = fake_acc()
    acc, why = sp.pool_accounts(row, pool)
    assert why is None
    p = payer()
    txs, user = sp.entry_bundle(p, acc, 500_000_000)
    assert sp.selfcheck(txs, p) and len(txs) == 2
    _, header, keys, _, ixs = txwire.decode(txs[1])
    prog, metas, data = ixs[-1]
    assert prog == AMM and data == sp.SWAPS["sell"][0] + struct.pack("<QQ", 500_000_000, 1)
    roles = [r for r, _, _ in sp.SWAPS["sell"][1]]
    got = dict(zip(roles, [m[0] for m in metas[:21]]))
    assert got["user"] == p and got["user_base_token_account"] == pda.ata(p, WSOL, TOKEN)
    assert got["user_quote_token_account"] == pda.ata(p, acc["quote_mint"], T22)
    assert all(got[r] == acc[r] for r in roles if not r.startswith("user"))
    assert [m[0] for m in metas[21:]] == acc["_extra"] and header[0] == 1
    writable = {r for r, _, w in sp.SWAPS["sell"][1] if w}
    assert all(m[2] == (r in writable) for r, m in zip(roles, metas[:21]))  # no account is writable unless the IDL says so
    txs, user = sp.exit_bundle(p, acc, 12_345, 750_000_000)
    assert sp.selfcheck(txs, p) and len(txs) == 3
    assert txwire.decode(txs[1])[4][-1][2] == sp.SWAPS["sell"][0] + struct.pack("<QQ", 750_000_000, 12_345)
    exit_ix = txwire.decode(txs[2])[4][-1]
    assert exit_ix[2] == sp.SWAPS["buy_exact_quote_in"][0] + struct.pack("<QQ", 12_345, 1)  # 24 bytes: no track_volume
    roles = [r for r, _, _ in sp.SWAPS["buy_exact_quote_in"][1]]
    got = dict(zip(roles, [m[0] for m in exit_ix[1][:23]]))
    assert got["user_volume_accumulator"] == pda._pda([b"user_volume_accumulator", pda.b58decode(p)], AMM)
    assert got["global_volume_accumulator"] == pda._pda([b"global_volume_accumulator"], AMM)
    req = sp.bundle_request(txs, [None, ["a"], ["b"]], [None, ["c"], ["d"]], 1)
    cfg = req["params"][1]
    assert req["method"] == "simulateBundle" and len(req["params"][0]["encodedTransactions"]) == 3
    assert (cfg["simulationBank"], cfg["skipSigVerify"], cfg["replaceRecentBlockhash"], cfg["transactionEncoding"]) == ("tip", True, True, "base64")
    assert len(cfg["preExecutionAccountsConfigs"]) == len(cfg["postExecutionAccountsConfigs"]) == 3


@pytest.mark.parametrize("change,why", [({"cashback_fee_basis_points": 50}, "unsupported_layout"), ({"coin_creator": key(9)}, "unsupported_layout")])
def test_unsupported_pools_are_skipped(change, why):
    row, pool = fake_acc()
    assert sp.pool_accounts(dict(row, **change), pool) == (None, why)


def test_the_pool_accounts_are_checked():
    row, pool = fake_acc()
    acc = json.loads(row["ix_accounts"])
    assert sp.pool_accounts(dict(row, ix_accounts=json.dumps(dict(acc, base_mint=key(9)))), pool)[1] == "unsupported_not_reversed"
    assert sp.pool_accounts(dict(row, ix_accounts=json.dumps(dict(acc, _extra=[key(4), key(5)]))), pool)[1] == "layout_derivation"
    assert sp.pool_accounts(dict(row, ix_accounts=json.dumps(dict(acc, _extra=[key(4)]))), pool)[1] == "unsupported_remaining_accounts"
    assert sp.pool_accounts(row, key(10))[1] == "layout_pool"


def test_the_meter_counts_before_the_call_and_stops(tmp_path):
    t = [1_792_800_000.0]
    path = str(tmp_path / "state.json")
    m = sp.Meter(path, now=lambda: t[0])
    assert m.reserve("sim") and json.load(open(path))["calls"] == 1  # saved before the call is made
    m.s["calls"] = sp.FEE_UNTIL
    assert not m.reserve("fee") and m.reserve("sim")
    m.s["calls"] = sp.CAP_DAY
    with pytest.raises(sp.Stop) as e:
        m.reserve("sim")
    assert e.value.code == sp.EXIT_CAP
    t[0] += 86_400  # the next UTC day starts at 0
    m.reserve("sim")
    assert m.s["calls"] == 1
    m.s["hour"] = [t[0]] * sp.HOUR_CAP
    assert m.hour_wait() >= 3599  # a throttle: Rpc.call waits for room
    with pytest.raises(sp.Stop):
        m.reserve("sim")
    day = m.s["day"]
    t[0] = (t[0] // 86_400 + 1) * 86_400 - 60  # a minute before midnight UTC: a full hour window
    m._day()
    m.s["hour"] = [t[0] - 10] * sp.HOUR_CAP
    t[0] += 120  # two minutes later: a new UTC day, and the hour window survives it
    m._day()
    assert m.s["day"] != day and len(m.s["hour"]) == sp.HOUR_CAP
    open(path, "w").write("{broken")
    with pytest.raises(sp.Stop) as e:
        sp.Meter(path)
    assert e.value.code == sp.EXIT_ERRORS
    run = sp.Meter(str(tmp_path / "s2.json"), max_run=2)
    run.reserve("sim"), run.reserve("sim")
    with pytest.raises(sp.Stop):
        run.reserve("sim")


def test_the_rpc_allows_two_methods_backs_off_and_stops(tmp_path):
    m = sp.Meter(str(tmp_path / "state.json"))
    answers = [(429, {"x-ratelimit-remaining": "0"}, b""), (200, {}, b'{"result": 1}')]
    slept, sent = [], []

    def post(url, body):
        sent.append(json.loads(body))
        return answers.pop(0)

    rpc = sp.Rpc(CANARY, m, post=post, sleep=slept.append, log=lambda s: None)
    assert rpc.call(sp.fee_request(key(1), 5000, 1), "fee") == {"result": 1}
    assert slept[0] >= 10 and m.s["http_429"] == 1 and m.s["calls"] == 2  # the retried call counts too
    with pytest.raises(ValueError):
        rpc.call({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": []}, "sim")
    rpc2 = sp.Rpc(CANARY, m, post=lambda u, b: (500, {}, b""), sleep=lambda s: None, log=lambda s: None)
    with pytest.raises(sp.Stop) as e:
        for _ in range(sp.MAX_CONSEC):
            rpc2.call(sp.fee_request(key(1), 5000, 1), "sim")
    assert e.value.code == sp.EXIT_ERRORS
    rpc3 = sp.Rpc(CANARY, m, post=lambda u, b: (429, {}, b""), sleep=lambda s: None, log=lambda s: None)
    with pytest.raises(sp.Stop) as e:  # a persistent 429 stops too
        rpc3.call(sp.fee_request(key(1), 5000, 1), "sim")
    assert e.value.code == sp.EXIT_ERRORS
    dead = sp.Rpc(CANARY, m, post=lambda u, b: (200, {}, b"{}"), sleep=lambda s: None, log=lambda s: None, alive=lambda: False)
    with pytest.raises(sp.Stop) as e:
        dead.call(sp.fee_request(key(1), 5000, 1), "sim")
    assert e.value.code == 0


def test_a_row_is_marked_only_once_its_call_is_counted(tmp_path):
    m = sp.Meter(str(tmp_path / "state.json"), max_run=1)
    marked = []
    rpc = sp.Rpc(CANARY, m, post=lambda u, b: (200, {}, b'{"result": []}'), sleep=lambda s: None, log=lambda s: None)
    rpc.call(sp.fee_request(key(1), 5000, 1), "fee", on_reserved=lambda: marked.append(1))
    with pytest.raises(sp.Stop):
        rpc.call(sp.fee_request(key(1), 7500, 2), "fee", on_reserved=lambda: marked.append(2))
    assert marked == [1]  # the second was never counted, so never marked


def test_the_url_never_leaks(monkeypatch, capsys):
    def boom(req, timeout):
        raise OSError(f"cannot reach {CANARY}")

    monkeypatch.setattr(sp.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        sp.http_post(CANARY, b"{}")
    assert "SECRETTOKEN" not in str(e.value) and "canary" not in str(e.value)
    for bad in ("http://x.rpcpool.com/t", "https://example.com/t", None):
        with pytest.raises(SystemExit) as e:
            sp.check_url(bad)
        assert "SECRETTOKEN" not in str(e.value)
    assert sp.check_url(CANARY) == CANARY


def test_requests_carry_the_fixed_user_agent(monkeypatch):
    seen = {}

    class R:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def urlopen(req, timeout):
        seen.update(req.header_items())
        return R()

    monkeypatch.setattr(sp.urllib.request, "urlopen", urlopen)
    sp.http_post(CANARY, b"{}")
    assert seen["User-agent"] == "solana-lab-research" and "@" not in seen["User-agent"] and "Accept-encoding" not in seen


def test_the_source_has_no_way_to_send():
    for mod in (sp, txwire):
        src = open(mod.__file__).read()
        assert not any(m in src for m in ("sendTransaction", "sendBundle", "requestAirdrop", "signTransaction"))
    assert sp.METHODS == {"simulateBundle", "getRecentPrioritizationFees"}


def test_classify():
    ok = {"result": {"value": {"summary": "succeeded", "transactionResults": [{"err": None}, {"err": None}]}}}
    assert sp.classify(ok, 1) == ("ok", None)
    mkt = {"result": {"value": {"summary": {"failed": {}}, "transactionResults": [{"err": None}, {"err": {"InstructionError": [1, {"Custom": 6063}]}}]}}}
    assert sp.classify(mkt, 1)[0] == "market"
    setup = {"result": {"value": {"summary": {"failed": {}}, "transactionResults": [{"err": "InvalidAccountForFee"}]}}}
    assert sp.classify(setup, 1)[0] == "structural"
    acct = {"result": {"value": {"summary": {"failed": {}}, "transactionResults": [{"err": None}, {"err": {"InstructionError": [1, {"Custom": 6057}]}}]}}}
    assert sp.classify(acct, 1)[0] == "structural"
    assert sp.classify({"error": {"code": -32602}}, 1) == ("rpc", {"code": -32602})


def spl(amount):
    return base64.b64encode(bytes(64) + struct.pack("<Q", amount) + bytes(93)).decode()


def pool_data(acc, vqr):
    d = bytearray(287)
    for off, k in ((43, WSOL), (75, acc["quote_mint"]), (139, acc["pool_base_token_account"]), (171, acc["pool_quote_token_account"]),
                   (211, pda.DEFAULT_KEY)):
        d[off:off + 32] = pda.b58decode(k)
    d[245:261] = vqr.to_bytes(16, "little", signed=True)
    return base64.b64encode(bytes(d)).decode()


class FakeRpc:
    def __init__(self, res):
        self.res, self.reqs = res, []

    def call(self, req, kind, on_reserved=None):
        self.reqs.append(req)
        if on_reserved:
            on_reserved()
        return self.res


def ui(b64):
    return {"owner": TOKEN, "lamports": 1, "data": [b64, "base64"], "executable": False}


def test_the_entry_readout_against_the_engine_on_the_same_state():
    row, pool = fake_acc()
    acc, _ = sp.pool_accounts(row, pool)
    b, q, vqr, size = 80_000_000_000, 900_000_000_000_000, 5_000_000, 500_000_000
    eng = rev_buy_token(Pool(b, q + vqr, 25, 5, 0, 0, virt=vqr), size).tokens
    res = {"result": {"context": {"slot": 460_300_010}, "value": {"summary": "succeeded", "transactionResults": [
        {"err": None, "unitsConsumed": 30_000},
        {"err": None, "unitsConsumed": 130_000,
         "preExecutionAccounts": [ui(pool_data(acc, vqr)), ui(spl(b)), ui(spl(q)), ui(spl(0))],
         "postExecutionAccounts": [ui(spl(eng + 50)), ui(spl(0))]}]}}}
    e = {"size": size, "j_tokens": eng, "family": "F7", "variant": "tp4_T600", "pool": pool, "t": 1}
    req, out = sp.simulate_entry(e, acc, row, payer(), FakeRpc(res), 7)
    assert out["cls"] == "ok" and out["sim_out"] == eng + 50 and out["eng_same_state"] == eng
    assert out["M_bps"] == round(1e4 * 50 / eng, 2) and out["pool_decode_ok"] and out["pool_state"]["vqr"] == vqr
    assert out["context_slot"] == 460_300_010 and out["units"] == [30_000, 130_000]
    cfg = req["params"][1]
    assert cfg["preExecutionAccountsConfigs"][0] is None and len(cfg["preExecutionAccountsConfigs"][1]["addresses"]) == 4


def test_an_acquire_the_market_refuses_is_its_own_class():
    row, pool = fake_acc()
    acc, _ = sp.pool_accounts(row, pool)
    res = {"result": {"context": {"slot": 9}, "value": {"summary": {"failed": {}}, "transactionResults": [
        {"err": None}, {"err": {"InstructionError": [1, {"Custom": 6004}]}}]}}}
    _, out = sp.simulate_exit({"tokens": 5, "j_sol": 500_000_000, "pool": pool}, acc, row, payer(), FakeRpc(res), 1)
    assert out["cls"] == "acquire_market" and out["detail"]["stage"] == "acquire"
    res["result"]["value"]["transactionResults"][1]["err"] = {"InstructionError": [1, {"Custom": 6057}]}
    _, out = sp.simulate_exit({"tokens": 5, "j_sol": 500_000_000, "pool": pool}, acc, row, payer(), FakeRpc(res), 1)
    assert out["cls"] == "structural"
    _, out = sp.simulate_exit({"tokens": 5, "j_sol": 900_000_000, "pool": pool}, acc, row, payer(), FakeRpc(res), 1, budget=2_000_000_000)
    assert (out["acquire"], out["acquire_reason"]) == (2_000_000_000, "no_model")


def test_the_acquire_is_sized_from_the_model():
    row = {"pool_base_token_reserves": 80_000_000_000, "pool_quote_token_reserves": 900_000_000_000_000, "virtual_quote_reserves": "0",
           "lp_fee_basis_points": 25, "protocol_fee_basis_points": 5, "coin_creator_fee_basis_points": 0, "cashback_fee_basis_points": 0}
    p = Pool(80_000_000_000, 900_000_000_000_000, 25, 5, 0, 0)
    T = rev_buy_token(p, 500_000_000).tokens
    lam, why, model = sp.acquire_plan(row, T, sp.PAYER_BUDGET)
    assert why == "model" and rev_buy_token(p, model).tokens >= T > rev_buy_token(p, model - 1).tokens
    assert lam == max(sp.ACQUIRE_MIN, -(-model * 3 // 2)) and abs(model - 500_000_000) <= 1
    assert sp.acquire_plan(row, T, 600_000_000) == (600_000_000, "budget_cap", model)  # 1.5 x the model is past the budget
    assert sp.acquire_plan(row, rev_buy_token(p, 3_000_000_000).tokens, 2_000_000_000) == (2_000_000_000, "budget_cap", None)
    assert sp.acquire_plan({}, T, 2_000_000_000) == (2_000_000_000, "no_model", None)


def test_the_percentile_guard_compares_common_slots(tmp_path):
    m = sp.Meter(str(tmp_path / "state.json"))
    same = {p: {"values": [(s, 7 if s % 3 else 0) for s in range(1000 + i, 1150 + i)]} for i, p in enumerate(sp.PCTS)}
    for _ in range(9):
        sp._percentile_guard(m, same, lambda s: None)
    assert not m.s.get("fee_off")
    sp._percentile_guard(m, same, lambda s: None)
    assert m.s["fee_off"]
    m2 = sp.Meter(str(tmp_path / "s2.json"))
    differ = {5000: {"values": [(s, 5) for s in range(150)]}, 7500: {"values": []}, 9000: {"values": [(s, 9) for s in range(150)]}}
    for _ in range(12):
        sp._percentile_guard(m2, differ, lambda s: None)
    assert not m2.s.get("fee_off")


def test_a_second_instance_is_refused(tmp_path):
    lab = tmp_path / "lab"
    (lab / "probe" / "simfee").mkdir(parents=True)
    held = open(lab / "probe" / "simfee" / "probe.lock", "w")
    sp.fcntl.flock(held, sp.fcntl.LOCK_EX | sp.fcntl.LOCK_NB)
    with pytest.raises(SystemExit) as e:
        sp.main(["--lab", str(lab), "--payer", payer()])
    assert e.value.code == sp.EXIT_LOCKED


def test_the_exit_readout_on_the_state_after_the_acquire():
    row, pool = fake_acc()
    acc, _ = sp.pool_accounts(row, pool)
    b, q, vqr, T = 80_000_000_000, 900_000_000_000_000, 0, 5_000_000_000_000
    p1 = Pool(b, q, 25, 5, 0, 0)
    after = rev_buy_token(p1, 760_000_000).state  # our acquire moved the pool
    eng = rev_sell_token(after, T).trader_quote
    res = {"result": {"context": {"slot": 9}, "value": {"summary": "succeeded", "transactionResults": [
        {"err": None, "unitsConsumed": 1},
        {"err": None, "unitsConsumed": 2, "preExecutionAccounts": [ui(pool_data(acc, vqr)), ui(spl(b)), ui(spl(q))],
         "postExecutionAccounts": [ui(spl(T + 7))]},
        {"err": None, "unitsConsumed": 3,
         "preExecutionAccounts": [ui(pool_data(acc, vqr)), ui(spl(after.b)), ui(spl(after.e)), ui(spl(0)), ui(spl(T + 7))],
         "postExecutionAccounts": [ui(spl(eng)), ui(spl(7))]}]}}}
    x = {"tokens": T, "j_sol": 500_000_000, "pool": pool}
    _, out = sp.simulate_exit(x, acc, row, payer(), FakeRpc(res), 1)
    assert out["cls"] == "ok" and out["sim_out"] == eng and out["M_bps"] == 0.0
    assert out["exit_est"] == rev_sell_token(p1, T).trader_quote
    assert (out["acquire"], out["acquire_reason"]) == (sp.PAYER_BUDGET, "no_model")  # the fake row has no reserves


def make_journal(path):
    con = sqlite3.connect(path)
    for name, (cols, pk) in J.TABLES.items():
        con.execute(f"CREATE TABLE {name} ({cols}, PRIMARY KEY ({pk}))")
    F = sp.FLOOR
    sig = "INSERT INTO signals (family, variant, key, t, day, venue, orientation, status) VALUES (?, ?, ?, ?, ?, 'pool', 'reversed', 'entered')"
    rows = [("F7", "tp4_T600", "P1", F + 600_000, "2026-10-23"), ("F7", "tp4_T600", "P2", F + 500_000, "2026-10-22"),
            ("N2_F7", "tp4_T600", "P3", F + 600_100, "2026-10-23"), ("F7", "tp4_T600", "P4", F - 10, "2026-10-23"),
            ("F7", "flow300_H6", "P5", F + 600_200, "2026-10-23"), ("F7", "tp4_T600", "P6", F + 600_300, "2026-10-23")]
    for r in rows:
        con.execute(sig, r)
        con.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, 500000000, 'filled', NULL)", r + (r[3] + 3,))
        con.execute("INSERT INTO fills VALUES (?, ?, ?, ?, 'buy', ?, ?, 1000, 500000000)", r + (r[3] + 3,))
    for k, reason, q in (("P1", "take_profit", 520_000_000), ("P5", "restart_lost", 0), ("P6", "stop_loss", 400_000_000)):
        r = next(x for x in rows if x[2] == k)
        con.execute("INSERT INTO exits (family, variant, key, t, day, entry_slot, exit_decision_slot, exit_slot, exit_reason, tokens, "
                    "proceeds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1000, ?)", r + (r[3] + 3, r[3] + 50, r[3] + 53, reason, q))
        if q:
            con.execute("INSERT INTO fills VALUES (?, ?, ?, ?, 'sell', ?, ?, 1000, ?)", r + (r[3] + 53, q))
    con.commit()
    con.close()


def test_the_journal_copy_and_its_filters(tmp_path):
    live = str(tmp_path / "journal.sqlite")
    make_journal(live)
    copy = sp.snapshot(live, str(tmp_path / "copy.sqlite"))
    assert oct(os.stat(copy).st_mode & 0o777) == "0o600"
    entries, exits, triggers = sp.journal_rows(copy)
    assert sorted(e["pool"] for e in entries) == ["P1", "P5", "P6"]  # not 22 Oct, not N2, not below the floor
    assert sorted(x["pool"] for x in exits) == ["P1", "P6"] and {x["j_sol"] for x in exits} == {520_000_000, 400_000_000}
    assert sorted(t["pool"] for t in triggers) == ["P1", "P5", "P6"]


def test_a_dry_run_cannot_reach_the_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    monkeypatch.setenv("LAB_TRITON_RPC_URL", CANARY)
    sp._dry_guard()
    assert "LAB_TRITON_RPC_URL" not in os.environ
    with pytest.raises(OSError, match="dry run"):
        socket.create_connection(("127.0.0.1", 9))


def test_the_payer_must_be_a_wallet():
    assert sp._check_payer(payer()) == payer()
    for bad in (AMM, pda._pda([b"global_config"], AMM), "not-base58!"):
        with pytest.raises(SystemExit):
            sp._check_payer(bad)


def test_the_rule_evaluation(tmp_path):
    d = tmp_path / "probe" / "simfee-x"
    d.mkdir(parents=True)
    rows = [{"side": "entry", "cls": "ok", "M_bps": 30.0 + (i % 5), "D_bps": 40.0, "slots_late": 100} for i in range(40)]
    rows += [{"side": "entry", "cls": "market"}] * 2 + [{"side": "entry", "cls": "structural"}]
    rows += [{"side": "exit", "cls": "ok", "M_bps": (-1) ** i * 3.0} for i in range(40)] + [{"side": "exit", "cls": "skipped"}]
    rows += [{"side": "exit", "cls": "acquire_market", "acquire_reason": "budget_cap"}, {"side": "exit", "cls": "late"},
             {"side": "exit", "cls": "market", "test_late": True}, {"side": "exit", "cls": "skipped", "why": "layout_derivation"}]
    rows = [dict(r, family="F7", variant="tp4_T600", pool=f"p{i}", t=i) for i, r in enumerate(rows)]
    rows.append(dict(rows[0], M_bps=-500.0))  # the same position side twice: the first counts
    (d / "sims.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = sp.summary(str(tmp_path))
    assert s["entry"]["systematic"] and s["entry"]["uitval_pct"] == round(100 * 2 / 42, 2) and s["entry"]["uitval"]
    assert s["entry"]["ok_with_M"] == 40 and s["entry"]["M_median"] > 25
    assert not s["exit"]["systematic"] and s["exit"]["attempted"] == 40 and not s["exit"]["uitval"]
    assert s["exit"]["not_in_sample"] == {"late": 1, "skipped": 2, "acquire_market": 1, "declined": 0}
    assert s["exit"]["why"] == {"acquire_market:budget_cap": 1, "late:": 1, "skipped:": 1, "skipped:layout_derivation": 1}
    assert s["rule"]["sha256"] == sp.RULE_SHA
