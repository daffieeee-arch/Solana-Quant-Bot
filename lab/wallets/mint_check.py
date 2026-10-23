"""Mint sellability check on the live pools of the paper journal (owner OK 17 Oct 2026 "mint rugcheck";
spec research/spec-mint-rugcheck-live-2026-10-17.md §1; audit 23 Oct action 2). Counts only, no P&L, no
strategy input: nothing here feeds a signal, a filter or a parameter.

Three steps, so the paid calls are counted before they are made:

  list   the token mints of the journal's pools (signals.key, t >= --from-slot) per family, plus the
         probe pools of the explore window; pool -> (mint, coin_creator) from the journal's `learned`
         table (what the engine itself learned from CreatePoolEvents / the store snapshot). Writes
         mints.parquet and prints the number of getMultipleAccounts calls (100 keys per call).
  fetch  the mint accounts through the existing token-meta RPC path (backtest.token_meta_rpc.rpc_accounts:
         Triton RPC, LAB_TRITON_RPC_URL holds the token, never printed). Only with --enable. Shares the
         daily cap with backtest.token_meta_rpc one way: the recorder's rpc/state.json call count of the
         UTC day plus the calls needed must stay <= --max-calls-per-day (5,000), else it refuses (the
         side process does not read this script's count, so run it when the side process is far from its
         cap). Every call is counted in <out>/rpc-calls.json before its answer is used, and every call's
         raw account bytes are appended to accounts.parquet (base64) right after it, so a failure at call
         k keeps the k-1 paid answers and the parse can be redone without a call.
  report parse the mint accounts (SPL Token or Token-2022 base state + TLV extensions) and count the
         flags per family: flags.parquet, counts.json, counts.md.

  python -m wallets.mint_check list --out DIR [--journal J] [--probes P] [--from-slot 459648000]
  python -m wallets.mint_check fetch --out DIR --enable [--max-calls-per-day 5000]
  python -m wallets.mint_check report --out DIR
"""

import argparse
import base64
import collections
import datetime as dt
import json
import os
import sqlite3
import struct
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from backtest.token_meta_rpc import TOKEN_2022, b58encode, rpc_accounts

JOURNAL = "/home/chupa/Solana-project/data-old-faithful-one/lab/journal/paper-k3/journal/journal.sqlite"
PROBES = "/home/chupa/Solana-project/data-old-faithful-one/lab/wallets/regime-1064-inputs/probe-pools-explore.parquet"
RPC_STATE = "/home/chupa/Solana-project/data-old-faithful-one/lab/recorder/rpc/state.json"
SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SYSTEM = "11111111111111111111111111111111"
KEYS_PER_CALL = 100  # getMultipleAccounts takes at most 100 keys
# Owner strategy names (lab/reports/woordenlijst.md): K3 = tp4_T600, H4 = flow300_H6, H5 = flow300_tp100_H6.
FAMILIES = {("F7", "tp4_T600"): "K3", ("F7", "flow300_H6"): "H4", ("F7", "flow300_tp100_H6"): "H5",
            ("N2_F7", "tp4_T600"): "N2"}
# Token-2022 ExtensionType (u16; solana-program/token-2022 clients/js/src/generated/types/extensionType.ts)
EXT = {0: "uninitialized", 1: "transfer_fee_config", 2: "transfer_fee_amount", 3: "mint_close_authority",
       4: "confidential_transfer_mint", 5: "confidential_transfer_account", 6: "default_account_state",
       7: "immutable_owner", 8: "memo_transfer", 9: "non_transferable", 10: "interest_bearing_config",
       11: "cpi_guard", 12: "permanent_delegate", 13: "non_transferable_account", 14: "transfer_hook",
       15: "transfer_hook_account", 16: "confidential_transfer_fee", 17: "confidential_transfer_fee_amount",
       18: "metadata_pointer", 19: "token_metadata", 20: "group_pointer", 21: "token_group",
       22: "group_member_pointer", 23: "token_group_member", 24: "confidential_mint_burn",
       25: "scaled_ui_amount", 26: "pausable", 27: "pausable_account", 28: "permissioned_burn"}
# The flags the spec vetoes (§1) plus pausable/paused (not in the spec; a paused mint cannot be sold, so the veto set
# here is one wider than the spec), and the ones the spec only logs; counted per family, never applied.
VETO_FLAGS = ["mint_authority", "freeze_authority", "transfer_fee", "transfer_hook", "permanent_delegate",
              "non_transferable", "default_frozen", "pausable", "paused"]
INFO_FLAGS = ["token2022", "mint_close_authority", "metadata_pointer", "token_metadata", "interest_bearing",
              "scaled_ui_amount", "confidential", "permissioned_burn", "other_extension"]

FLAG_SCHEMA = pa.schema([("mint", pa.string()), ("found", pa.bool_()), ("program", pa.string()), ("data_len", pa.int64()),
                         ("supply", pa.uint64()), ("decimals", pa.int64()), ("mint_authority_key", pa.string()),
                         ("freeze_authority_key", pa.string()), ("extensions", pa.list_(pa.int64())),
                         ("transfer_fee_bps", pa.int64()), ("transfer_fee_max", pa.uint64()), ("transfer_fee_bps_older", pa.int64()),
                         ("transfer_hook_program", pa.string()), ("permanent_delegate_key", pa.string()),
                         ("default_state", pa.int64()), ("pausable_authority", pa.string()),
                         ("mint_close_authority_key", pa.string()), ("parse_error", pa.string())]
                        + [(f, pa.bool_()) for f in VETO_FLAGS + INFO_FLAGS] + [("veto", pa.bool_())])


# ----------------------------------------------------------------------------- parsing


def _opt_key(b):
    """A 32-byte OptionalNonZeroPubkey: None when all zero."""
    return b58encode(bytes(b)) if any(b) else None


def _coption_key(data, pos):
    """COption<Pubkey>: u32 tag + 32 bytes (SPL Token base state)."""
    (tag,) = struct.unpack_from("<I", data, pos)
    return b58encode(data[pos + 4:pos + 36]) if tag == 1 else None


def parse_mint(owner, data):
    """The fields of a mint account (SPL Token or Token-2022): base state and the TLV extensions."""
    r = {"found": True, "program": {SPL_TOKEN: "token", TOKEN_2022: "token2022"}.get(owner, "other:" + owner), "data_len": len(data),
         "supply": None, "decimals": None, "mint_authority_key": None, "freeze_authority_key": None, "extensions": [],
         "transfer_fee_bps": None, "transfer_fee_max": None, "transfer_fee_bps_older": None, "transfer_hook_program": None, "permanent_delegate_key": None,
         "default_state": None, "pausable_authority": None, "paused": None, "mint_close_authority_key": None, "parse_error": None}
    if owner not in (SPL_TOKEN, TOKEN_2022) or len(data) < 82:
        r["parse_error"] = "not a token mint"
        return r
    r["mint_authority_key"] = _coption_key(data, 0)
    (r["supply"],) = struct.unpack_from("<Q", data, 36)
    r["decimals"] = data[44]
    if data[45] != 1:
        r["parse_error"] = "not initialized"
    r["freeze_authority_key"] = _coption_key(data, 46)
    if len(data) > 165:
        if data[165] != 1:  # AccountType::Mint after the 165-byte padded base
            r["parse_error"] = f"account type {data[165]}"
            return r
        pos = 166
        while pos + 4 <= len(data):
            ext, length = struct.unpack_from("<HH", data, pos)
            v = data[pos + 4:pos + 4 + length]
            if ext == 0 and length == 0:
                break
            if len(v) < length:
                r["parse_error"] = f"truncated extension {ext}"
                break
            r["extensions"].append(ext)
            if ext == 1 and length >= 108:  # older_transfer_fee at 72, newer_transfer_fee at 90: epoch u64, maximum_fee u64, bps u16
                _, _, r["transfer_fee_bps_older"] = struct.unpack_from("<QQH", v, 72)
                _, r["transfer_fee_max"], r["transfer_fee_bps"] = struct.unpack_from("<QQH", v, 90)
            elif ext == 3 and length >= 32:
                r["mint_close_authority_key"] = _opt_key(v[:32])
            elif ext == 6 and length >= 1:
                r["default_state"] = v[0]  # 1 initialized, 2 frozen
            elif ext == 12 and length >= 32:
                r["permanent_delegate_key"] = _opt_key(v[:32])
            elif ext == 14 and length >= 64:
                r["transfer_hook_program"] = _opt_key(v[32:64])
            elif ext == 26 and length >= 33:
                r["pausable_authority"], r["paused"] = _opt_key(v[:32]), bool(v[32])
            pos += 4 + length
    return r


def flags(r):
    """The spec's veto flags and the informational ones from a parsed mint (a missing account: all None)."""
    if not r.get("found"):
        return {f: None for f in VETO_FLAGS + INFO_FLAGS} | {"veto": None}
    e = set(r["extensions"])
    f = {"mint_authority": r["mint_authority_key"] is not None, "freeze_authority": r["freeze_authority_key"] is not None,
         "transfer_fee": bool(r["transfer_fee_bps"]) or bool(r["transfer_fee_bps_older"]), "transfer_hook": r["transfer_hook_program"] is not None,
         "permanent_delegate": r["permanent_delegate_key"] is not None, "non_transferable": 9 in e,
         "default_frozen": r["default_state"] == 2, "pausable": 26 in e, "paused": bool(r["paused"]),
         "token2022": r["program"] == "token2022", "mint_close_authority": r["mint_close_authority_key"] is not None,
         "metadata_pointer": 18 in e, "token_metadata": 19 in e, "interest_bearing": 10 in e, "scaled_ui_amount": 25 in e,
         "confidential": bool(e & {4, 16, 24}), "permissioned_burn": 28 in e,
         "other_extension": bool(e - {1, 3, 6, 9, 10, 12, 14, 18, 19, 20, 22, 25, 26, 28, 4, 16, 24})}
    f["veto"] = any(f[k] for k in VETO_FLAGS)
    return f


# ----------------------------------------------------------------------------- list


def list_mints(journal, probes, from_slot):
    """One row per mint: pool, families (K3/H4/H5/N2/probe), coin_creator, from the journal's learned pools."""
    con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    learned = {k: json.loads(info) for k, info in con.execute("SELECT key, info FROM learned WHERE kind = 'pool'")}
    fam = collections.defaultdict(set)
    for family, variant, key in con.execute("SELECT DISTINCT family, variant, key FROM signals WHERE t >= ?", (from_slot,)):
        name = FAMILIES.get((family, variant))
        if name:
            fam[key].add(name)
    if probes:
        for p in pq.read_table(probes).column("pool").to_pylist():
            fam[p].add("probe")
    rows, missing = [], []
    for pool, names in sorted(fam.items()):
        info = learned.get(pool)
        if not info or len(info) < 9:
            missing.append(pool)
            continue
        rows.append({"pool": pool, "mint": info[3], "quote_class": info[0], "orientation": info[1], "coin_creator": info[7],
                     "learned_slot": info[8], "families": sorted(names)})
    return rows, missing


def calls_needed(n):
    return -(-n // KEYS_PER_CALL)


# ----------------------------------------------------------------------------- fetch


def _today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def shared_calls_today(rpc_state=RPC_STATE):
    """The token-meta side process's call count of the UTC day (0 when it has none)."""
    if not os.path.exists(rpc_state):
        return 0
    st = json.load(open(rpc_state))
    return int(st.get("calls", 0)) if st.get("day") == _today() else 0


def fetch(out, url, max_calls, call=rpc_accounts, rpc_state=RPC_STATE, log=print):
    """getMultipleAccounts over the mints not fetched yet, 100 per call; raw bytes to accounts.parquet."""
    mints = pq.read_table(os.path.join(out, "mints.parquet")).column("mint").to_pylist()
    acc_path = os.path.join(out, "accounts.parquet")
    have = set(pq.read_table(acc_path).column("mint").to_pylist()) if os.path.exists(acc_path) else set()
    todo = sorted(set(mints) - have)
    need = calls_needed(len(todo))
    state_path = os.path.join(out, "rpc-calls.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    if state.get("day") != _today():
        state = {"day": _today(), "calls": 0, "log": []}
    shared = shared_calls_today(rpc_state)
    log(f"mints {len(mints)}, fetched {len(have)}, to do {len(todo)} = {need} calls; today: token_meta_rpc {shared} + this {state['calls']} of cap {max_calls}")
    if shared + state["calls"] + need > max_calls:
        raise SystemExit(f"refused: {shared + state['calls']} + {need} calls would pass the daily cap {max_calls}")
    schema = pa.schema([("mint", pa.string()), ("found", pa.bool_()), ("owner", pa.string()), ("data_b64", pa.string()), ("fetched_unix", pa.int64())])
    written = 0
    for i in range(0, len(todo), KEYS_PER_CALL):
        keys = todo[i:i + KEYS_PER_CALL]
        state["calls"] += 1
        state["log"].append({"unix": int(time.time()), "keys": len(keys)})
        with open(state_path + ".tmp", "w") as f:  # counted before the answer is used
            json.dump(state, f)
        os.replace(state_path + ".tmp", state_path)
        res = call(url, keys)
        rows = []
        for k in keys:
            v = res.get(k)
            rows.append({"mint": k, "found": v is not None, "owner": v[0] if v else None,
                         "data_b64": base64.b64encode(v[1]).decode() if v else None, "fetched_unix": int(time.time())})
        old = pq.read_table(acc_path) if os.path.exists(acc_path) else pa.Table.from_pylist([], schema=schema)
        pq.write_table(pa.concat_tables([old, pa.Table.from_pylist(rows, schema=schema)]), acc_path + ".tmp")
        os.replace(acc_path + ".tmp", acc_path)  # every paid answer is on disk before the next call
        written += len(rows)
        log(f"call {state['calls']}: {len(keys)} keys, {sum(1 for k in keys if res.get(k))} found")
    return written


# ----------------------------------------------------------------------------- report


def report(out):
    mints = pq.read_table(os.path.join(out, "mints.parquet")).to_pylist()
    accounts = {r["mint"]: r for r in pq.read_table(os.path.join(out, "accounts.parquet")).to_pylist()}
    per_mint = {}
    for m in sorted({r["mint"] for r in mints}):
        a = accounts.get(m)
        if a and a["found"]:
            r = parse_mint(a["owner"], base64.b64decode(a["data_b64"]))
        else:
            r = {"found": False, "program": None, "data_len": None, "supply": None, "decimals": None, "mint_authority_key": None,
                 "freeze_authority_key": None, "extensions": [], "transfer_fee_bps": None, "transfer_fee_max": None, "transfer_fee_bps_older": None,
                 "transfer_hook_program": None, "permanent_delegate_key": None, "default_state": None, "pausable_authority": None,
                 "paused": None, "mint_close_authority_key": None, "parse_error": "not fetched" if a is None else "account missing"}
        per_mint[m] = {"mint": m, **r, **flags(r)}
    pq.write_table(pa.Table.from_pylist(list(per_mint.values()), schema=FLAG_SCHEMA), os.path.join(out, "flags.parquet"))
    # counts per family: a mint counts once per family it belongs to
    fams = collections.defaultdict(set)
    for r in mints:
        for f in r["families"]:
            fams[f].add(r["mint"])
    fams["all_journal"] = {r["mint"] for r in mints if set(r["families"]) - {"probe"}}
    counts = {}
    for f, ms in sorted(fams.items()):
        rows = [per_mint[m] for m in ms]
        found = [r for r in rows if r["found"]]
        c = {"mints": len(rows), "found": len(found), "token2022": sum(1 for r in found if r["token2022"]),
             "token": sum(1 for r in found if r["program"] == "token"), "parse_error": sum(1 for r in found if r["parse_error"]),
             "veto_any": sum(1 for r in found if r["veto"])}
        for k in VETO_FLAGS + INFO_FLAGS:
            c[k] = sum(1 for r in found if r[k])
        c["transfer_fee_bps_values"] = sorted({r["transfer_fee_bps"] for r in found if r["transfer_fee"]})
        c["hook_programs"] = sorted({r["transfer_hook_program"] for r in found if r["transfer_hook"]})
        c["extension_ids"] = dict(sorted(collections.Counter(e for r in found for e in r["extensions"]).items()))
        counts[f] = c
    with open(os.path.join(out, "counts.json"), "w") as f:
        json.dump(counts, f, indent=1)
    cols = ["mints", "found", "token2022"] + VETO_FLAGS + ["veto_any"] + INFO_FLAGS
    lines = ["| familie | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for f in ["K3", "H4", "H5", "all_journal", "N2", "probe"]:
        if f in counts:
            lines.append(f"| {f} | " + " | ".join(str(counts[f][c]) for c in cols) + " |")
    with open(os.path.join(out, "counts.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return counts


# ----------------------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["list", "fetch", "report"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--journal", default=JOURNAL)
    ap.add_argument("--probes", default=PROBES, help="probe pools parquet (pool column); '' for none")
    ap.add_argument("--from-slot", type=int, default=459_648_000, help="journal signals from this slot (epoch 1064 start)")
    ap.add_argument("--enable", action="store_true", help="fetch: make the paid RPC calls")
    ap.add_argument("--max-calls-per-day", type=int, default=5000)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.step == "list":
        rows, missing = list_mints(a.journal, a.probes or None, a.from_slot)
        pq.write_table(pa.Table.from_pylist(rows), os.path.join(a.out, "mints.parquet"))
        n = len({r["mint"] for r in rows})
        per = collections.Counter(f for r in rows for f in r["families"])
        print(f"pools {len(rows)} (not in learned: {len(missing)}), mints {n}, calls {calls_needed(n)} "
              f"(journal only: {calls_needed(len({r['mint'] for r in rows if set(r['families']) - {'probe'}}))}); per family {dict(sorted(per.items()))}")
        if missing:
            print("pools without a learned row (skipped):", " ".join(missing))
    elif a.step == "fetch":
        url = os.environ.get("LAB_TRITON_RPC_URL")
        if not a.enable or not url:
            raise SystemExit("fetch needs --enable and LAB_TRITON_RPC_URL (the url is never printed)")
        fetch(a.out, url, a.max_calls_per_day)
    else:
        report(a.out)


if __name__ == "__main__":
    sys.exit(main())
