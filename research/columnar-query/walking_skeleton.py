#!/usr/bin/env python3
"""Build a bounded Raw -> Bronze -> Silver evidence dossier from Rust output.

This module reads the explicit collection/Parquet manifests and never decodes
wire bytes or writes a canonical Bronze/Silver record. Rust remains the
semantic owner; this process verifies and presents its already-authorized
records for one fixed range.
"""
import argparse
import datetime
import hashlib
import html
import json
import pathlib
import resource
import sys
import time

from collection_reader import attach_collection, load_collection
from manifest_reader import load_manifest, sha
from mint_timeline import query_timeline, summarize as timeline_summary
from query import connect

MAX_SLOTS = 3
MAX_ROWS = 10000
MINT = "4aG2APjGceKMLjyhFLjAEYaqu4w2meifWb2hAy4Wpump"
COLLECTION_SHA256 = "39b4856b9f5b942ef263d7bda142968b5f645a0706f07a9353c59b55babba1ab"


def exact(value):
    if isinstance(value, float):
        raise ValueError("floating-point value is not canonical evidence")
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return {key: exact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact(item) for item in value]
    return value


def canonical(value):
    return (json.dumps(exact(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def canonical_line(value):
    return (json.dumps(exact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def research_input_allowed(slice_class, research_ready=False):
    """Mechanical gate: engineering slices can never enter research inputs."""
    return bool(research_ready) and slice_class == "RESEARCH_SAMPLING"


def selected_inventory(manifest, start_slot, end_slot):
    if type(start_slot) is not int or type(end_slot) is not int:
        raise ValueError("integer slot range required")
    if not 0 <= start_slot < end_slot or end_slot - start_slot > MAX_SLOTS:
        raise ValueError("bounded nonempty range required")
    wanted = list(range(start_slot, end_slot))
    logical = manifest["plan"]["logical_selection"]
    selected = [row for row in logical if start_slot <= row["slot"] < end_slot]
    if [row["slot"] for row in selected] != wanted:
        raise ValueError("selected range is not fully present in the logical inventory")
    if any(row["role"] != "ORIGINAL_SELECTION" for row in selected):
        raise ValueError("walking skeleton requires the native original selection")
    batches = [batch for batch in manifest["batches"] if any(slot in wanted for slot in batch["selected_slots"])]
    if len(batches) != len(wanted) or any(batch["selected_slots"] != [slot] for batch, slot in zip(batches, wanted, strict=True)):
        raise ValueError("selected range is not a one-slot physical partition")
    return selected, batches


def _rows(db, sql, params):
    cursor = db.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    result = []
    while row := cursor.fetchone():
        if len(result) >= MAX_ROWS:
            raise ValueError("bounded report query exceeded cap; no truncated success")
        result.append(dict(zip(columns, row, strict=True)))
    return result


def _json_record(raw):
    if not isinstance(raw, bytes) or not raw.endswith(b"\n"):
        raise ValueError("canonical Rust record bytes required")
    return json.loads(raw[:-1])


def _logical_identity(lines):
    chain = hashlib.sha256(b"OF1_ORDERED_RECORD_CHAIN_1").digest()
    json_digest = hashlib.sha256()
    bytes_total = 0
    for line in lines:
        chain = hashlib.sha256(chain + len(line).to_bytes(8, "little") + line).digest()
        json_digest.update(line + b"\n")
        bytes_total += len(line) + 1
    return {"rows": len(lines), "jsonl_bytes": bytes_total, "jsonl_sha256": json_digest.hexdigest(), "ordered_logical_sha256": chain.hex()}


def _child_bindings(root, collection, batches):
    children = []
    files = {}
    planned = {batch["batch_id"]: batch for batch in collection["plan"]["batches"]}
    for batch in batches:
        plan_batch = planned[batch["batch_id"]]
        relative = batch["parquet_manifest_path"]
        child_root = root / pathlib.PurePosixPath(relative).parent
        child, child_hash = load_manifest(child_root)
        if child_hash != batch["parquet_manifest_sha256"]:
            raise ValueError("selected child manifest hash mismatch")
        listed_files = []
        for name, entry in child["files"].items():
            listed_files.append(name)
            files[f"{relative.rsplit('/', 1)[0]}/{name}"] = {
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "layer": entry["layer"],
                "rows": entry["audit"]["rows"],
            }
        children.append({
            "batch_id": batch["batch_id"],
            "source_id": batch["source_id"],
            "selected_slots": batch["selected_slots"],
            "receipt_sequences": plan_batch["receipt_sequences"],
            "manifest_path": relative,
            "manifest_sha256": child_hash,
            "decoder_execution_sha256": child["input"]["execution_sha256"],
            "writer": child["writer"],
            "files": listed_files,
        })
    return children, files


def _source_receipts(plan, batches):
    planned = {batch["batch_id"]: batch for batch in plan["batches"]}
    selected_sources = {planned[batch["batch_id"]]["source_id"] for batch in batches}
    sequences_by_source = {
        source_id: {
            sequence
            for batch in batches
            if planned[batch["batch_id"]]["source_id"] == source_id
            for sequence in planned[batch["batch_id"]]["receipt_sequences"]
        }
        for source_id in selected_sources
    }
    result = []
    for source in plan["sources"]:
        source_id = source["source_id"]
        sequences = sequences_by_source.get(source_id, set())
        result.extend(
            {**receipt, "source_id": source_id}
            for receipt in source["bindings"]["receipts"]
            if receipt["sequence"] in sequences
        )
    return sorted(result, key=lambda receipt: (receipt["source_id"], receipt["sequence"], receipt["raw_sha256"]))


def _raw_index(db, start_slot, end_slot):
    columns = (
        "record_ordinal,collection_batch_id,collection_source_id,collection_role,slot,transaction_index,"
        "entry_index,transaction_index_in_entry,source_transaction_index,disposition,reason,transaction_status,"
        "atomic_observation_package,run_id,raw_sha256,raw_path,transaction_node_cid_hex,receipt_sequence,"
        "raw_section_offset,raw_section_length,decoder_source_sha256,record_sha256,record_bytes"
    )
    rows = _rows(db, f"SELECT {columns} FROM bronze WHERE slot>=? AND slot<? ORDER BY slot,transaction_index,record_ordinal", [start_slot, end_slot])
    output = []
    lines = []
    instructions = 0
    cpis = 0
    for row in rows:
        raw = row.pop("record_bytes")
        record = _json_record(raw)
        lines.append(raw[:-1])
        tx = record.get("transaction") or {}
        top = tx.get("instructions") or []
        inner = tx.get("inner_instructions") or []
        instructions += len(top)
        cpis += len(inner)
        row["top_level_instruction_count"] = len(top)
        row["recorded_cpi_count"] = len(inner)
        output.append(row)
    return exact(output), {"top_level": instructions, "recorded_cpi": cpis}, _logical_identity(lines)


def _silver_rows(db, start_slot, end_slot):
    columns = (
        "record_ordinal,slot,transaction_index,transaction_status,bronze_record_sha256,record_sha256,"
        "mint,user_address,is_buy,event_instruction_name,outer_index,instruction_inner_order,event_inner_order,"
        "token_amount_raw_u64,sol_amount_raw_u64,quote_amount_raw_u64,amount_raw_u64,base_decimals,quote_decimals,"
        "units_decimals,units_decimals_evidence,units_binding_status,quote_mint_identity,executable_price,"
        "net_proceeds,observed_at_state,actionable_at_state,execution_opportunity_at_state,cpi_flags_evidence,"
        "cpi_signer,cpi_writable,raw_sha256,raw_section_offset,raw_section_length,slice_class,receipt_evidence"
        ",record_bytes"
    )
    rows = _rows(db, f"SELECT {columns} FROM silver WHERE slot>=? AND slot<? ORDER BY slot,transaction_index,record_ordinal", [start_slot, end_slot])
    lines = []
    for row in rows:
        lines.append(row.pop("record_bytes")[:-1])
    return exact(rows), _logical_identity(lines)


def _counts(db, start_slot, end_slot, instruction_counts):
    row = db.execute(
        """SELECT COUNT(*) AS transactions,
          COUNT(*) FILTER (WHERE disposition='DECODED') AS decoded,
          COUNT(*) FILTER (WHERE disposition='MISSING') AS missing,
          COUNT(*) FILTER (WHERE disposition='UNSUPPORTED') AS unsupported,
          COUNT(*) FILTER (WHERE disposition='QUARANTINED') AS quarantined,
          COUNT(*) FILTER (WHERE transaction_status='ERROR') AS failed_transactions,
          COUNT(*) FILTER (WHERE transaction_status='OK') AS successful_transactions,
          COUNT(*) FILTER (WHERE atomic_observation_package IS TRUE) AS atomic_packages,
          SUM(len(token_balance_observations)) AS balance_observations,
          COUNT(*) FILTER (WHERE token_balance_observations_state='VALUE' AND len(token_balance_observations)>0) AS balance_packages
        FROM bronze WHERE slot>=? AND slot<?""",
        [start_slot, end_slot],
    ).fetchone()
    silver = db.execute("SELECT COUNT(*) FROM silver WHERE slot>=? AND slot<?", [start_slot, end_slot]).fetchone()[0]
    silver_failed = db.execute("SELECT COUNT(*) FROM silver WHERE slot>=? AND slot<? AND transaction_status='ERROR'", [start_slot, end_slot]).fetchone()[0]
    if silver_failed:
        raise ValueError("failed transaction has a Silver state transition")
    values = dict(zip([
        "transactions", "decoded", "missing", "unsupported", "quarantined", "failed_transactions",
        "successful_transactions", "atomic_packages", "balance_observations", "balance_packages",
    ], row, strict=True))
    values["silver_facts"] = silver
    values["silver_facts_on_failed_transactions"] = silver_failed
    values["selected_blocks"] = end_slot - start_slot
    values["top_level_instructions"] = instruction_counts["top_level"]
    values["recorded_cpi_instructions"] = instruction_counts["recorded_cpi"]
    values["successful_state_transitions"] = 0
    values["failed_transactions_have_no_state_transition"] = silver_failed == 0
    values["gap_slots"] = []
    values["unavailable_state"] = "UNAVAILABLE"
    return exact(values)


def _html(result):
    esc = lambda value: html.escape("UNAVAILABLE" if value is None else str(value))
    counts = result["counts"]
    silver = result["silver_records"]
    raw = result["raw_records"]
    life = result["lifecycle"]
    silver_rows = "".join(
        "<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key in ["slot", "transaction_index", "is_buy", "mint", "token_amount_raw_u64", "sol_amount_raw_u64", "bronze_record_sha256"]) + "</tr>"
        for row in silver
    )
    raw_preview = "".join(
        "<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key in ["slot", "transaction_index", "receipt_sequence", "raw_sha256", "raw_section_offset", "raw_section_length", "disposition", "transaction_status"]) + "</tr>"
        for row in raw[:25]
    )
    life_rows = []
    for card in life["transactions"]:
        life_rows.append(
            "<tr>" + "".join(f"<td>{esc(card.get(key))}</td>" for key in ["slot", "transaction_index", "transaction_status", "inclusion", "bronze_disposition"]) + "</tr>"
        )
    return f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>B5 Raw to Bronze to Silver walking skeleton</title><style>
body{{margin:0;background:#101820;color:#e8f0f7;font:15px system-ui}}main{{max-width:1500px;margin:auto;padding:30px}}
section,.metric{{background:#192833;border:1px solid #314756;border-radius:10px;padding:18px;margin:16px 0}}.metrics{{display:flex;gap:14px;flex-wrap:wrap}}.metric{{flex:1;min-width:165px}}.metric b{{display:block;color:#8ae0cf;font-size:26px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}td,th{{padding:8px;border-bottom:1px solid #314756;text-align:left}}th,a,summary{{color:#8ae0cf}}.scroll{{overflow:auto}}.warn{{color:#ffd18c}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
</style><main><p>RUST-AUTHORIZED RAW → BRONZE → SILVER · READ-ONLY PRESENTATION</p>
<h1>Bounded walking skeleton</h1><p>Native range [{esc(result['range']['start_slot'])}, {esc(result['range']['end_slot_exclusive'])}) · source class {esc(result['slice_class'])} · Research Ready: false.</p>
<p class='warn'>This is a technical, receipt-bound result. First/last observation is not a launch or lifecycle boundary. Event reserves are not historical account state. Acquired/processed clocks are operational provenance only. ENGINEERING_VALIDATION_ONLY is mechanically excluded from research inputs.</p>
<div class='metrics'><div class='metric'><b>{esc(counts['selected_blocks'])}</b>selected slots/blocks</div><div class='metric'><b>{esc(counts['transactions'])}</b>Bronze transaction packages</div><div class='metric'><b>{esc(counts['top_level_instructions'])}</b>top-level instructions</div><div class='metric'><b>{esc(counts['recorded_cpi_instructions'])}</b>recorded CPI references</div><div class='metric'><b>{esc(counts['silver_facts'])}</b>Silver facts</div></div>
<section><h2>Counts and evidence states</h2><pre>{esc(json.dumps(counts,indent=2))}</pre><p>Failures remain evidence and produce zero successful state transitions. GAP, UNAVAILABLE and QUARANTINED are separate states; no missing row is treated as zero.</p></section>
<section><h2>Raw source and byte bindings</h2><p>All {esc(len(raw))} selected Bronze packages are in <a href='raw-records.jsonl'>raw-records.jsonl</a>; the table below is a bounded preview only. The JSONL contains receipt sequence, raw hash, CID, section offset/length and disposition for every package.</p><div class='scroll'><table><tr><th>Slot</th><th>Tx</th><th>Receipt</th><th>Raw SHA</th><th>Offset</th><th>Length</th><th>Disposition</th><th>Status</th></tr>{raw_preview}</table></div></section>
<section><h2>Rust-authored Silver output</h2><p>All {esc(len(silver))} selected facts are in <a href='silver-records.jsonl'>silver-records.jsonl</a>. Parent hashes remain explicit and exact integer fields are serialized as decimal strings.</p><div class='scroll'><table><tr><th>Slot</th><th>Tx</th><th>Buy</th><th>Mint</th><th>Token raw</th><th>Quote/SOL raw</th><th>Bronze parent</th></tr>{silver_rows}</table></div></section>
<section><h2>Visible lifecycle fragment</h2><p>Mint <code>{esc(result['lifecycle']['mint'])}</code>; {esc(life['counts']['transactions'])} unique packages, {esc(life['counts']['silver_facts'])} Silver facts, {esc(life['counts']['balance_observations'])} balance observations. This fragment has no proven launch, migration, end, executable fill or return.</p><div class='scroll'><table><tr><th>Slot</th><th>Tx</th><th>Status</th><th>Inclusion channels</th><th>Bronze state</th></tr>{''.join(life_rows)}</table></div><p><a href='lifecycle.json'>Full lifecycle JSON</a></p></section>
<section><h2>Manifest and provenance</h2><p><a href='manifest.json'>Dataset manifest</a> · <a href='skeleton.json'>Canonical skeleton JSON</a> · <a href='execution.json'>Operational execution receipt</a></p><p>Bronze logical hash: <code>{esc(result['logical_identities']['bronze']['ordered_logical_sha256'])}</code><br>Silver logical hash: <code>{esc(result['logical_identities']['silver']['ordered_logical_sha256'])}</code></p><pre>{esc(json.dumps(result['provenance'],indent=2))}</pre></section>
</main></html>"""


def run(collection_root, output, start_slot, end_slot, collection_sha256=COLLECTION_SHA256, mint=MINT):
    root = collection_root.resolve(strict=True)
    if output.exists() or output.resolve().is_relative_to(root):
        raise ValueError("new output directory must be outside the preserved collection")
    manifest, digest = load_collection(root)
    if digest != collection_sha256 or manifest["state"] != "COMPLETE":
        raise ValueError("unexpected or incomplete collection identity")
    selected, batches = selected_inventory(manifest, start_slot, end_slot)
    children, files = _child_bindings(root, manifest, batches)
    started = time.perf_counter()
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with connect() as db:
        collection = attach_collection(db, root, manifest)
        raw_records, instruction_counts, raw_identity = _raw_index(db, start_slot, end_slot)
        silver_records, silver_identity = _silver_rows(db, start_slot, end_slot)
        counts = _counts(db, start_slot, end_slot, instruction_counts)
        definitions = json.loads((pathlib.Path(__file__).with_name("mint-timeline.sql.json")).read_text())
        lifecycle_cards = [card for card in query_timeline(db, mint, definitions) if start_slot <= card["slot"] < end_slot]
    if load_collection(root)[1] != digest:
        raise ValueError("collection changed during read-only verification")
    lifecycle = {"mint": mint, "counts": timeline_summary(lifecycle_cards), "transactions": lifecycle_cards}
    writer_ids = sorted({json.dumps(child["writer"], sort_keys=True) for child in children})
    plan = manifest["plan"]
    selected_source = next(source for source in plan["sources"] if source["source_id"] == selected[0]["source_id"])
    sample_identity = selected_source.get("sample_identity")
    slice_class = sample_identity.get("sample_class") if sample_identity else "ENGINEERING_VALIDATION_ONLY"
    selected_receipts = _source_receipts(plan, batches)
    selected_raw_hashes = {
        slot["raw_sha256"]
        for batch in batches
        for slot in batch["slots"]
        if slot["slot"] in {row["slot"] for row in selected}
    }
    receipt_raw_hashes = {receipt["raw_sha256"] for receipt in selected_receipts if receipt["raw_bytes"]}
    if selected_raw_hashes != receipt_raw_hashes:
        raise ValueError("selected source receipt/raw bindings are incomplete or ambiguous")
    provenance = {
        "collection_sha256": digest,
        "plan_sha256": manifest["plan_sha256"],
        "selected_batches": children,
        "selected_receipts": selected_receipts,
        "selected_source": {"source_id": selected_source["source_id"], "run_id": selected_source["run_id"], "run_root_provenance": selected_source["run_root"], "bindings": selected_source["bindings"], "historical_paths_are_provenance_only": True},
        "writer_identities": [json.loads(value) for value in writer_ids],
        "research_ready": False,
        "provider_calls": False,
        "engineering_validation_exclusion": {"mechanical_rule": "slice_class == ENGINEERING_VALIDATION_ONLY is never a research input", "selected_slice_class": slice_class, "research_input_allowed": research_input_allowed(slice_class, manifest.get("research_ready", False))},
    }
    code_files = ["walking_skeleton.py", "collection_reader.py", "manifest_reader.py", "mint_timeline.py", "mint-timeline.sql.json", "query.py", "requirements.lock"]
    code_sha = {name: sha((pathlib.Path(__file__).with_name(name)).read_bytes()) for name in code_files}
    result = exact({
        "schema": "OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_1",
        "range": {"start_slot": start_slot, "end_slot_exclusive": end_slot, "selected_slots": [row["slot"] for row in selected], "gap_slots": []},
        "slice_class": slice_class,
        "sample_identity": sample_identity,
        "counts": counts,
        "raw_records": raw_records,
        "silver_records": silver_records,
        "logical_identities": {"bronze": raw_identity, "silver": silver_identity},
        "lifecycle": lifecycle,
        "files": files,
        "provenance": {**provenance, "code_sha256": code_sha, "sql_sha256": sha((pathlib.Path(__file__).with_name("mint-timeline.sql.json")).read_bytes())},
        "limits": ["No Raw bytes or receipts are rewritten", "Rust owns protocol meaning/order/exact integers/evidence/coverage/quarantine", "Python only reads and presents explicit Rust outputs", "Event reserves are not account state", "No Research Ready or edge claim"],
    })
    raw = canonical(result)
    html_bytes = _html(result).encode()
    execution = {"schema": "OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_EXECUTION_1", "started_at_utc": started_at, "completed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter() - started, "skeleton_sha256": sha(raw), "html_sha256": sha(html_bytes), "collection_path": str(root), "python_version": sys.version.split()[0], "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "threads": 1, "duckdb_memory_limit": "256MB", "provider_calls": False}
    output.mkdir(parents=False)
    artifacts = {
        "skeleton.json": raw,
        "manifest.json": canonical({"schema": result["schema"], "range": result["range"], "slice_class": result["slice_class"], "sample_identity": result["sample_identity"], "counts": result["counts"], "logical_identities": result["logical_identities"], "files": result["files"], "provenance": result["provenance"], "research_ready": False}),
        "raw-records.jsonl": b"".join(canonical_line(row) for row in raw_records),
        "silver-records.jsonl": b"".join(canonical_line(row) for row in silver_records),
        "lifecycle.json": canonical(lifecycle),
        "index.html": html_bytes,
        "execution.json": (json.dumps(execution, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
    }
    for name, content in artifacts.items():
        with (output / name).open("xb") as handle:
            handle.write(content)
    print(json.dumps({"report": str(output / "index.html"), "skeleton_sha256": execution["skeleton_sha256"], "counts": result["counts"]}, sort_keys=True))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--start-slot", type=int, required=True)
    parser.add_argument("--end-slot", type=int, required=True)
    parser.add_argument("--collection-sha256", default=COLLECTION_SHA256)
    parser.add_argument("--mint", default=MINT)
    args = parser.parse_args()
    run(args.collection, args.output, args.start_slot, args.end_slot, args.collection_sha256, args.mint)
