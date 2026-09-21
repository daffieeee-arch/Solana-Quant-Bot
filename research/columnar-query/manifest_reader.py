"""Bounded physical dataset inventory. Never discovers files with a glob.

This reader validates Rust's explicit publication contract, not domain meaning.
Valid duplicate records are preserved; duplicate file references are rejected.
"""
import hashlib
import json
import pathlib
import re

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SHARDS_PER_LAYER = 64
MAX_ROWS_PER_FILE = 5000
MAX_RECORD_BYTES = 16 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest JSON key")
        result[key] = value
    return result


def regular_bytes(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("bounded regular file required")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("bounded regular file grew beyond limit")
    return raw


def physical_hash(path):
    """A file hash needs at most one 1-MiB chunk resident, not the whole shard."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("missing or invalid bounded Parquet shard")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while data := handle.read(1024 * 1024):
            size += len(data)
            if size > MAX_FILE_BYTES:
                raise ValueError("Parquet shard grew beyond byte cap")
            digest.update(data)
    return digest.hexdigest(), size


def count(value, label, maximum=None):
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise ValueError("invalid bounded " + label)
    return value


def layer_names(manifest, layer):
    if manifest["schema"] == "OF1_PARQUET_DATASET_1":
        return [layer + ".parquet"]
    return manifest["layers"][layer]["files"]


def layer_rows(manifest, layer):
    return sum(manifest["files"][name]["audit"]["rows"] for name in layer_names(manifest, layer))


def listed_files(manifest):
    return [name for layer in ["bronze", "silver"] for name in layer_names(manifest, layer)]


def selection_inventory(manifest):
    if manifest["schema"] == "OF1_PARQUET_DATASET_1":
        return {"status": "UNAVAILABLE_LEGACY_MANIFEST", "selected_slots": None,
                "all_expected_packages_accounted": False}
    selection = manifest["selection"]
    slots = selection["selected_slots"]
    if not isinstance(slots, list) or len(slots) > 128 or any(type(s) is not int or not 0 <= s <= 2**64-1 for s in slots):
        raise ValueError("bounded selected slot inventory required")
    if slots != sorted(set(slots)):
        raise ValueError("ordered unique selected slots required")
    rows = selection["slots"]
    if [row["slot"] for row in rows] != slots:
        raise ValueError("selection slot outcome inventory mismatch")
    present = decoded = 0
    accounted = bool(slots)
    for row in rows:
        count(row["present_packages"], "present packages")
        count(row["decoded_packages"], "decoded packages")
        if row["expected_packages"] is not None:
            count(row["expected_packages"], "expected packages")
        if sum(count(n, "outcome count") for n in row["outcomes"].values()) != row["present_packages"]:
            raise ValueError("selection outcomes do not account for packages")
        if row["decoded_packages"] != row["outcomes"].get("DECODED", 0):
            raise ValueError("decoded packages disagree with declared outcomes")
        if type(row["accounted"]) is not bool or row["accounted"] and row["present_packages"] != row["expected_packages"]:
            raise ValueError("incorrect slot accounting claim")
        accounted = accounted and row["accounted"]
        present += row["present_packages"]
        decoded += row["decoded_packages"]
    if present != layer_rows(manifest, "bronze") or decoded != selection["decoded_packages"]:
        # Unavailable original inventory cannot be upgraded by rows alone.
        if slots or selection["status"] != "UNAVAILABLE":
            raise ValueError("selection versus layer row mismatch")
    if type(selection["all_expected_packages_accounted"]) is not bool or selection["all_expected_packages_accounted"] != accounted:
        raise ValueError("incorrect selection completeness claim")
    if selection["status"] not in ["ACCOUNTED", "INCOMPLETE", "UNAVAILABLE"] or (selection["status"] == "ACCOUNTED") != accounted:
        raise ValueError("selection status disagrees with package accounting")
    return selection


def sample_inventory(manifest):
    sample = manifest.get("sample_identity")
    evidence = manifest["evidence"]
    if evidence.get("research_ready") is not False:
        raise ValueError("physical publication cannot promote Research Ready")
    if manifest["schema"] == "OF1_PARQUET_DATASET_1" and sample is not None:
        raise ValueError("legacy dataset cannot acquire retrospective sample identity")
    if sample is None:
        if evidence["slice_class"] != "ENGINEERING_VALIDATION_ONLY":
            raise ValueError("unbound sample class / retrospective reclassification")
    else:
        proposal_raw = regular_bytes(pathlib.Path(__file__).with_name("pilot-proposal.json"), MAX_MANIFEST_BYTES)
        expected = {"schema": "OF1_FIXED_PILOT_SAMPLE_1", "sample_class": "RESEARCH_SAMPLING",
                    "selection_plan_sha256": sha(proposal_raw), "epoch": 978,
                    "seed": "solana-quant-epoch978-pilot-v1-20260912", "algorithm": "SHA256_MIN_CENTER_1",
                    "selected_center": 422669517, "start_slot": 422669516, "end_slot_exclusive": 422669519}
        if json.dumps(sample, sort_keys=True) != json.dumps(expected, sort_keys=True) or evidence["slice_class"] != "RESEARCH_SAMPLING":
            raise ValueError("unsupported or altered fixed source sample identity")
        if manifest["input"]["execution"].get("sample_identity") != sample or manifest["input"]["execution"].get("slice_class") != "RESEARCH_SAMPLING":
            raise ValueError("sample identity is not bound by original decoder execution")
        if manifest.get("batch_binding") is not None:
            batch_inventory(manifest)
        elif manifest["selection"]["selected_slots"] != list(range(sample["start_slot"], sample["end_slot_exclusive"])):
            raise ValueError("sample selection disagrees with original slot inventory")
    return sample


def batch_inventory(manifest):
    """A physical subset retains the complete original native sample identity.

    This mirrors Rust's exact plan binding; no caller label or rewritten source
    receipt can provide an alternative sample identity here.
    """
    binding = manifest.get("batch_binding")
    if binding is None:
        return None
    if binding.get("schema") != "OF1_BATCH_BINDING_1":
        raise ValueError("unsupported physical batch binding")
    original_plan = binding.get("plan_json")
    if not isinstance(original_plan, str) or len(original_plan.encode()) > MAX_MANIFEST_BYTES or sha(original_plan.encode()) != binding.get("plan_sha256"):
        raise ValueError("batch original plan bytes/hash mismatch")
    from collection_reader import plan_inventory
    plan = json.loads(original_plan, object_pairs_hook=pairs_unique)
    sources, _, batches = plan_inventory(plan)
    planned = [b for b in batches if b["batch_id"] == binding.get("batch_id")]
    if len(planned) != 1 or binding.get("source_id") not in sources:
        raise ValueError("batch not in original plan")
    expected, source = planned[0], sources[binding["source_id"]]
    execution = manifest["input"]["execution"]
    if (binding.get("selected_slots"), binding.get("receipt_sequences"), binding.get("source_id")) != (expected["slots"], expected["receipt_sequences"], expected["source_id"]):
        raise ValueError("batch receipt/slot/source assignment mismatch")
    pairs = [(binding.get("original_bindings"), source["bindings"]),
             (binding.get("sample_identity"), source.get("sample_identity")),
             (binding.get("sample_identity"), manifest.get("sample_identity")),
             (binding.get("source_run_id"), source["run_id"]),
             (binding.get("source_run_root"), source["run_root"]),
             (binding.get("source_run_root"), execution["run_root"]),
             (binding.get("logical_selection"), plan["logical_selection"]),
             (binding.get("workers"), plan["workers"]),
             (binding, execution.get("batch_binding")),
             (binding.get("selected_slots"), manifest["selection"]["selected_slots"]),
             (execution["executable_sha256"], plan["workers"]["batch_decoder_sha256"])]
    if any(actual != expected for actual, expected in pairs):
        raise ValueError("batch immutable source/sample/worker identity mismatch")
    if manifest.get("sample_identity") is not None:
        sample = manifest["sample_identity"]
        if any(not sample["start_slot"] <= slot < sample["end_slot_exclusive"] for slot in binding["selected_slots"]):
            raise ValueError("physical subset extends original research sample")
    return binding


def load_manifest(root):
    raw = regular_bytes(root / "manifest.json", MAX_MANIFEST_BYTES)
    if regular_bytes(root / "COMPLETE", 65).decode().strip() != sha(raw):
        raise ValueError("incomplete dataset / manifest hash mismatch")
    manifest = json.loads(raw, object_pairs_hook=pairs_unique)
    if manifest["schema"] not in ["OF1_PARQUET_DATASET_1", "OF1_PARQUET_DATASET_2"]:
        raise ValueError("unsupported dataset schema")
    if manifest["schema"] == "OF1_PARQUET_DATASET_2":
        if set(manifest["layers"]) != {"bronze", "silver"}:
            raise ValueError("exact layer inventory required")
        for layer in ["bronze", "silver"]:
            names = layer_names(manifest, layer)
            if not isinstance(names, list) or not 0 < len(names) <= MAX_SHARDS_PER_LAYER:
                raise ValueError("bounded nonempty shard inventory required")
            for name in names:
                if not isinstance(name, str) or not re.fullmatch(layer + r"-[0-9]{6}\.parquet", name):
                    raise ValueError("canonical shard filename required")
    names = listed_files(manifest)
    if len(names) != len(set(names)):
        raise ValueError("duplicate shard file reference")
    if set(names) != set(manifest["files"]):
        raise ValueError("unlisted or missing shard inventory")
    for layer in ["bronze", "silver"]:
        ordinal = 0
        for index, name in enumerate(layer_names(manifest, layer)):
            entry = manifest["files"][name]
            expected_rows = count(entry["audit"]["rows"], "shard rows", MAX_ROWS_PER_FILE)
            digest, size = physical_hash(root / name)
            if digest != entry["sha256"] or size != count(entry["bytes"], "shard bytes", MAX_FILE_BYTES):
                raise ValueError("Parquet physical hash mismatch")
            if manifest["schema"] == "OF1_PARQUET_DATASET_2":
                if name != f"{layer}-{index:06d}.parquet" or entry["layer"] != layer or count(entry["shard_index"], "shard index") != index:
                    raise ValueError("shard layer/order identity mismatch")
                if count(entry["ordinal_start"], "ordinal start") != ordinal or count(entry["ordinal_end_exclusive"], "ordinal end") != ordinal + expected_rows:
                    raise ValueError("shard ordinal interval gap or overlap")
            ordinal += expected_rows
        if manifest["schema"] == "OF1_PARQUET_DATASET_2":
            if count(manifest["layers"][layer]["rows"], "layer rows") != ordinal:
                raise ValueError("layer row total mismatch")
    selection_inventory(manifest)
    sample_inventory(manifest)
    batch_inventory(manifest)
    return manifest, sha(raw)


def attach_dataset(db, root, manifest):
    """Validate every file separately, then bind only its explicit ordered list.

    Logical ordinal equality is checked across files. Duplicate record bytes are
    allowed and have distinct ordinals; duplicate file/ordinal references are not.
    """
    for layer in ["bronze", "silver"]:
        ordinal = 0
        paths = []
        layer_jsonl, layer_logical = hashlib.sha256(), hashlib.sha256()
        layer_bytes = 0
        for name in layer_names(manifest, layer):
            path = str(root / name)
            if any(character in path for character in "*?[]{}"):
                raise ValueError("literal Parquet path required; no glob metacharacters")
            expected = manifest["files"][name]["audit"]["rows"]
            db.from_parquet(path).create_view("_inventory_shard", replace=True)
            seen, distinct, first, last = db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT record_ordinal), MIN(record_ordinal), "
                "MAX(record_ordinal) FROM _inventory_shard").fetchone()
            wanted_extent = (ordinal, ordinal + expected - 1) if expected else (None, None)
            if seen != expected or distinct != expected or (first, last) != wanted_extent:
                raise ValueError("Parquet rows/ordinals differ from manifest inventory")
            file_jsonl, file_logical = hashlib.sha256(), hashlib.sha256()
            file_bytes, file_rows = 0, 0
            cursor = db.execute("SELECT record_ordinal, record_bytes, record_sha256 FROM _inventory_shard ORDER BY record_ordinal")
            # Never collect a whole layer or shard in Python. Original line bytes
            # and length-framed logical records are independently hashed; hashes
            # differ intentionally from the physical Parquet footer/file hash.
            while row := cursor.fetchone():
                actual_ordinal, raw, record_hash = row
                if type(actual_ordinal) is not int or actual_ordinal != ordinal + file_rows:
                    raise ValueError("record source order mismatch")
                if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RECORD_BYTES or not raw.endswith(b"\n"):
                    raise ValueError("bounded exact record line required")
                record = raw[:-1]
                if sha(record) != record_hash:
                    raise ValueError("record hash differs from preserved bytes")
                frame = len(record).to_bytes(8, "little")
                file_jsonl.update(raw); layer_jsonl.update(raw)
                file_logical.update(frame); file_logical.update(record)
                layer_logical.update(frame); layer_logical.update(record)
                file_rows += 1
                file_bytes += len(raw)
                if file_bytes > MAX_FILE_BYTES:
                    raise ValueError("reconstructed shard exceeds byte cap")
            actual_audit = {"rows": file_rows, "reconstructed_jsonl_bytes": file_bytes,
                            "reconstructed_jsonl_sha256": file_jsonl.hexdigest(),
                            "ordered_logical_sha256": file_logical.hexdigest()}
            if any(manifest["files"][name]["audit"][key] != value for key, value in actual_audit.items()):
                raise ValueError("shard logical identity differs from retained record bytes")
            layer_bytes += file_bytes
            ordinal += expected
            paths.append(path)
        if manifest["schema"] == "OF1_PARQUET_DATASET_2":
            actual_layer = {"rows": ordinal, "reconstructed_jsonl_bytes": layer_bytes,
                            "reconstructed_jsonl_sha256": layer_jsonl.hexdigest(),
                            "ordered_logical_sha256": layer_logical.hexdigest()}
            if any(manifest["layers"][layer][key] != value for key, value in actual_layer.items()):
                raise ValueError("combined layer logical identity mismatch")
        db.from_parquet(paths).create_view(layer)
        sample = sample_inventory(manifest)
        expected_class = manifest["evidence"]["slice_class"]
        wrong_class = db.execute(f"SELECT COUNT(*) FROM {layer} WHERE slice_class IS DISTINCT FROM ?", [expected_class]).fetchone()[0]
        if wrong_class:
            raise ValueError("Parquet sample class differs from source-bound manifest")
        if manifest["schema"] == "OF1_PARQUET_DATASET_2":
            wrong_evidence = db.execute(f"SELECT COUNT(*) FROM {layer} WHERE receipt_evidence IS DISTINCT FROM ?",
                                        [manifest["evidence"]["receipt_evidence"]]).fetchone()[0]
            if wrong_evidence:
                raise ValueError("Parquet receipt evidence differs from source-bound manifest")
        # Equality of pre-existing Rust record fields is not Pump interpretation.
        identity_rows = db.execute(f"SELECT DISTINCT json_extract(decode(record_bytes), '$.sample_identity'), "
                                  f"json_extract(decode(record_bytes), '$.source.bindings.sample_identity') FROM {layer}").fetchmany(3)
        if len(identity_rows) > 2:
            raise ValueError("mixed record sample identities")
        for top, source in identity_rows:
            if (json.loads(top) if top is not None else None) != sample or (json.loads(source) if source is not None else None) != sample:
                raise ValueError("record/sample source binding mismatch")
    db.execute("DROP VIEW _inventory_shard")
    selection = selection_inventory(manifest)
    if manifest["schema"] == "OF1_PARQUET_DATASET_2":
        outcomes = {outcome if outcome is not None else "UNAVAILABLE": total
                    for outcome, total in db.execute("SELECT disposition, COUNT(*) FROM bronze GROUP BY disposition").fetchall()}
        if outcomes != {key: value for key, value in selection["package_outcomes"].items() if value != 0} or outcomes.get("DECODED", 0) != selection["decoded_packages"]:
            raise ValueError("actual Parquet package outcomes differ from manifest totals")
    if manifest["schema"] == "OF1_PARQUET_DATASET_2" and selection["status"] != "UNAVAILABLE":
        actual = {}
        for slot, outcome, total in db.execute("SELECT slot, disposition, COUNT(*) FROM bronze GROUP BY ALL").fetchall():
            if slot is None:
                raise ValueError("missing actual selected-slot identity")
            actual.setdefault(slot, {})[outcome if outcome is not None else "UNAVAILABLE"] = total
        if set(actual) - set(selection["selected_slots"]):
            raise ValueError("actual Parquet has unselected slot")
        index_sets = {row[0]: row[1:] for row in db.execute(
            "SELECT slot,COUNT(transaction_index),COUNT(DISTINCT transaction_index),"
            "MIN(transaction_index),MAX(transaction_index) FROM bronze GROUP BY slot").fetchall()}
        for row in selection["slots"]:
            if actual.get(row["slot"], {}) != {k: v for k, v in row["outcomes"].items() if v != 0}:
                raise ValueError("actual Parquet outcomes differ from selected-slot manifest")
            if row["accounted"]:
                wanted = row["expected_packages"]
                expected_indexes = (wanted, wanted, 0, wanted-1) if wanted else (0, 0, None, None)
                if index_sets.get(row["slot"], (0, 0, None, None)) != expected_indexes:
                    raise ValueError("ACCOUNTED selection has duplicate/missing/shifted transaction indexes")


def reader_source_sha256():
    return sha(pathlib.Path(__file__).read_bytes())


def inventory(manifest):
    return {layer: {"files": layer_names(manifest, layer),
                    "file_count": len(layer_names(manifest, layer)),
                    "rows": layer_rows(manifest, layer),
                    "bytes": sum(manifest["files"][n]["bytes"] for n in layer_names(manifest, layer))}
            for layer in ["bronze", "silver"]}
