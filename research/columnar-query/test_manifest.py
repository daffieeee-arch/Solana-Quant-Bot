"""Manifest inventory tests; real Parquet cases consume only Rust-written fixtures."""
import copy
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

from manifest_reader import (attach_dataset, inventory, load_manifest,
                             physical_hash, sample_inventory, selection_inventory, sha)
from query import connect, rows, render


def publish_manifest(root, manifest):
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    (root / "manifest.json").write_bytes(data)
    (root / "COMPLETE").write_text(sha(data) + "\n")


def manifest_fixture(root):
    """Synthetic inventory bytes only, not a valid Parquet/decode assertion."""
    files, layers = {}, {}
    for layer in ["bronze", "silver"]:
        name = layer + "-000000.parquet"
        data = b"SYNTHETIC_PHYSICAL_INVENTORY_TEST_NOT_PARQUET"
        (root / name).write_bytes(data)
        files[name] = {"layer": layer, "shard_index": 0, "bytes": len(data), "sha256": sha(data),
                       "ordinal_start": 0, "ordinal_end_exclusive": 0, "audit": {"rows": 0}}
        layers[layer] = {"files": [name], "rows": 0}
    result = {"schema": "OF1_PARQUET_DATASET_2", "files": files, "layers": layers,
              "evidence": {"slice_class": "ENGINEERING_VALIDATION_ONLY", "research_ready": False},
              "selection": {"selected_slots": [10], "slots": [
                  {"slot": 10, "expected_packages": 0, "present_packages": 0,
                   "decoded_packages": 0, "outcomes": {}, "accounted": True}],
                  "all_expected_packages_accounted": True, "decoded_packages": 0,
                  "package_outcomes": {}, "status": "ACCOUNTED"}, "sample_identity": None, "input": {"execution": {}}}
    publish_manifest(root, result)
    return result


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.manifest = manifest_fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_inventory_ignores_unlisted_disk_file(self):
        (self.root / "unlisted.parquet").write_bytes(b"never opened")
        self.assertEqual(inventory(load_manifest(self.root)[0])["bronze"]["file_count"], 1)

    def test_missing_shard_rejected(self):
        (self.root / "bronze-000000.parquet").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            load_manifest(self.root)

    def test_corrupt_shard_rejected(self):
        (self.root / "bronze-000000.parquet").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_manifest(self.root)

    def test_duplicate_file_reference_rejected(self):
        self.manifest["layers"]["bronze"]["files"] *= 2
        publish_manifest(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "duplicate shard"):
            load_manifest(self.root)

    def test_duplicate_json_key_rejected(self):
        data = b'{"schema":1,"schema":2}'
        (self.root / "manifest.json").write_bytes(data)
        (self.root / "COMPLETE").write_text(sha(data))
        with self.assertRaisesRegex(ValueError, "duplicate manifest"):
            load_manifest(self.root)

    def test_unlisted_manifest_file_rejected(self):
        self.manifest["files"]["other.parquet"] = {}
        publish_manifest(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "inventory"):
            load_manifest(self.root)

    def test_missing_complete_never_counts_as_publication(self):
        (self.root / "COMPLETE").unlink()
        with self.assertRaises(ValueError):
            load_manifest(self.root)

    def test_paths_cannot_escape_and_symlink_rejected(self):
        self.manifest["layers"]["bronze"]["files"] = ["../bronze-000000.parquet"]
        publish_manifest(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "canonical"):
            load_manifest(self.root)
        path = self.root / "bronze-000000.parquet"
        path.unlink()
        path.symlink_to(self.root / "silver-000000.parquet")
        with self.assertRaises(ValueError):
            physical_hash(path)

    def test_cap_order_and_row_total_cannot_be_faked(self):
        for path, value in [("rows", 5001), ("ordinal", 1), ("total", 1), ("bytes", 64*1024*1024+1)]:
            manifest = copy.deepcopy(self.manifest)
            if path == "rows": manifest["files"]["bronze-000000.parquet"]["audit"]["rows"] = value
            elif path == "ordinal": manifest["files"]["bronze-000000.parquet"]["ordinal_start"] = value
            elif path == "total": manifest["layers"]["bronze"]["rows"] = value
            else: manifest["files"]["bronze-000000.parquet"]["bytes"] = value
            publish_manifest(self.root, manifest)
            with self.assertRaises(ValueError): load_manifest(self.root)

    def test_unknown_or_incomplete_selection_cannot_claim_complete(self):
        self.manifest["selection"]["slots"][0]["expected_packages"] = None
        with self.assertRaisesRegex(ValueError, "accounting"):
            selection_inventory(self.manifest)
        self.manifest["selection"]["slots"][0]["accounted"] = False
        self.manifest["selection"]["status"] = "INCOMPLETE"
        self.manifest["selection"]["all_expected_packages_accounted"] = False
        self.assertFalse(selection_inventory(self.manifest)["all_expected_packages_accounted"])

    def test_missing_entire_selected_slot_is_not_silently_dropped(self):
        self.manifest["selection"]["selected_slots"].append(11)
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            selection_inventory(self.manifest)

    def test_class_and_ready_relabel_rejected(self):
        self.manifest["evidence"]["slice_class"] = "RESEARCH_SAMPLING"
        with self.assertRaisesRegex(ValueError, "reclassification"):
            sample_inventory(self.manifest)
        self.manifest["evidence"]["research_ready"] = True
        with self.assertRaisesRegex(ValueError, "Research Ready"):
            sample_inventory(self.manifest)

    def test_sample_requires_original_execution_and_exact_fixed_identity(self):
        proposal = pathlib.Path(__file__).with_name("pilot-proposal.json").read_bytes()
        sample = {"schema": "OF1_FIXED_PILOT_SAMPLE_1", "sample_class": "RESEARCH_SAMPLING",
                  "selection_plan_sha256": sha(proposal), "epoch": 978,
                  "seed": "solana-quant-epoch978-pilot-v1-20260912", "algorithm": "SHA256_MIN_CENTER_1",
                  "selected_center": 422669517, "start_slot": 422669516, "end_slot_exclusive": 422669519}
        self.manifest["sample_identity"] = sample
        self.manifest["evidence"]["slice_class"] = "RESEARCH_SAMPLING"
        with self.assertRaisesRegex(ValueError, "original decoder"):
            sample_inventory(self.manifest)
        self.manifest["input"]["execution"] = {"sample_identity": sample, "slice_class": "RESEARCH_SAMPLING"}
        self.manifest["selection"]["selected_slots"] = list(range(422669516, 422669519))
        self.assertEqual(sample_inventory(self.manifest), sample)
        self.manifest["sample_identity"] = dict(sample, seed="redraw")
        with self.assertRaisesRegex(ValueError, "altered"):
            sample_inventory(self.manifest)

    def test_display_row_budget_fails_without_truncated_success(self):
        with connect() as db:
            self.assertEqual(len(rows(db.execute("SELECT range FROM range(10000)"))["rows"]), 10000)
            with self.assertRaisesRegex(ValueError, "no truncated report"):
                rows(db.execute("SELECT range FROM range(10001)"))

    def test_compact_renderer_preserves_inventory_and_full_details(self):
        result = {"duckdb_version": "1.5.5", "manifest_sha256": "a"*64, "writer": {"version": "fixture"},
                  "files": {"bronze-000000.parquet": {"bytes": 13, "sha256": "b"*64}},
                  "inventory": {"bronze": {"files": ["bronze-000000.parquet"], "file_count": 1, "rows": 0, "bytes": 13}},
                  "selection": self.manifest["selection"], "sample_identity": None,
                  "evidence": {"slice_class": "ENGINEERING_VALIDATION_ONLY", "receipt_evidence": "Fixture", "research_ready": False},
                  "queries": {"input_evidence": {"rows": [], "columns": [], "sql": "SELECT 1 -- fixture"}}}
        operations = {"query_seconds": 0.125, "dataset_path": "<test-path>"}
        first = render(result, operations)
        self.assertEqual(first, render(result, operations))
        self.assertIn("<th>Bestanden</th>", first)
        self.assertIn("<th>Alle uitkomsten</th>", first)
        self.assertIn("<td>10</td>", first)
        self.assertIn("<td>13</td>", first)
        self.assertIn("Volledige sample-identiteit", first)
        self.assertIn("bronze-000000.parquet", first)
        self.assertIn("&lt;test-path&gt;", first)
        self.assertNotIn("<test-path>", first)


def actual_shard_regression(root):
    dataset = root / "multi-shard"
    manifest, _ = load_manifest(dataset)
    assert len(manifest["layers"]["bronze"]["files"]) > 1
    assert manifest["layers"]["bronze"]["rows"] > 5000
    with connect() as db:
        attach_dataset(db, dataset, manifest)
        assert db.execute("SELECT COUNT(*) FROM bronze").fetchone()[0] == manifest["layers"]["bronze"]["rows"]
        assert db.execute("SELECT COUNT(DISTINCT record_ordinal) FROM bronze").fetchone()[0] == manifest["layers"]["bronze"]["rows"]
        queries = json.loads(pathlib.Path(__file__).with_name("coverage.sql.json").read_text())
        for sql in queries.values(): db.execute(sql).fetchall()
        actual_slots = db.execute("SELECT slot, disposition, COUNT(*) FROM bronze GROUP BY ALL ORDER BY ALL").fetchall()
    with tempfile.TemporaryDirectory() as tmp:
        clone = pathlib.Path(tmp) / "copy"
        shutil.copytree(dataset, clone)
        for mutation in ["slot", "outcome", "logical", "evidence"]:
            altered = copy.deepcopy(manifest)
            if mutation in ["slot", "outcome"]:
                grouped = {}
                for slot, outcome, count in actual_slots:
                    target = grouped.setdefault(slot + (1 if mutation == "slot" else 0), {})
                    label = outcome if mutation == "slot" else "UNSUPPORTED"
                    target[label] = target.get(label, 0) + count
                expected_rows = [{"slot": slot, "expected_packages": sum(outcomes.values()),
                                  "present_packages": sum(outcomes.values()), "decoded_packages": outcomes.get("DECODED", 0),
                                  "outcomes": outcomes, "accounted": True} for slot, outcomes in sorted(grouped.items())]
                altered["selection"] = {"selected_slots": [r["slot"] for r in expected_rows],
                                        "slots": expected_rows, "all_expected_packages_accounted": True,
                                        "decoded_packages": sum(r["decoded_packages"] for r in expected_rows),
                                        "package_outcomes": {label: sum(row["outcomes"].get(label, 0) for row in expected_rows)
                                                             for label in {label for row in expected_rows for label in row["outcomes"]}},
                                        "status": "ACCOUNTED"}
            elif mutation == "logical":
                altered["layers"]["bronze"]["ordered_logical_sha256"] = "0" * 64
            else:
                altered["evidence"]["receipt_evidence"] = "Research Ready"
            publish_manifest(clone, altered)
            checked, _ = load_manifest(clone)
            with connect() as db, unittest.TestCase().assertRaisesRegex(ValueError, "actual Parquet|logical identity|receipt evidence"):
                attach_dataset(db, clone, checked)
        altered = copy.deepcopy(manifest)
        publish_manifest(clone, altered)
        first = altered["layers"]["bronze"]["files"][0]
        (clone / first).unlink()
        with unittest.TestCase().assertRaises(ValueError): load_manifest(clone)
    print("DuckDB Rust multi-shard >5000 rows; missing shard, same-total slot/outcome substitution and logical drift fail-closed PASS")


def actual_duplicate_accounting_regression(root):
    """Resealed-manifest attack on old Rust-written bytes, never a new dataset.

    Construct only synthetic test inventory over the unchanged two duplicate
    Bronze rows. No Python Parquet serialization or domain interpretation.
    """
    with tempfile.TemporaryDirectory() as tmp, connect() as db:
        directory = pathlib.Path(tmp)
        manifest = manifest_fixture(directory)
        observed_evidence = []
        for layer, original in [("bronze", "duplicate-bronze.parquet"), ("silver", "one-silver.parquet")]:
            name = layer + "-000000.parquet"
            shutil.copyfile(root / original, directory / name)
            db.from_parquet(str(directory / name)).create_view("_fixture", replace=True)
            evidence = db.execute("SELECT DISTINCT receipt_evidence FROM _fixture").fetchmany(2)
            assert len(evidence) == 1
            observed_evidence.append(evidence[0][0])
            original_digest, logical = hashlib.sha256(), hashlib.sha256()
            size = count = 0
            cursor = db.execute("SELECT record_bytes FROM _fixture ORDER BY record_ordinal")
            while row := cursor.fetchone():
                line = row[0]
                original_digest.update(line)
                logical.update((len(line)-1).to_bytes(8, "little")); logical.update(line[:-1])
                size += len(line); count += 1
            audit = {"rows": count, "reconstructed_jsonl_bytes": size,
                     "reconstructed_jsonl_sha256": original_digest.hexdigest(), "ordered_logical_sha256": logical.hexdigest()}
            digest, physical_bytes = physical_hash(directory / name)
            manifest["files"][name].update(sha256=digest, bytes=physical_bytes, ordinal_end_exclusive=count, audit=audit)
            manifest["layers"][layer].update(audit)
        assert observed_evidence[0] == observed_evidence[1]
        # Preserve the original fixture's value: old fixture bytes omit it,
        # whereas the current explicit synthetic fixture correctly says Fixture.
        manifest["evidence"]["receipt_evidence"] = observed_evidence[0]
        manifest["selection"] = {"selected_slots": [422496004], "slots": [
            {"slot": 422496004, "expected_packages": 2, "present_packages": 2,
             "decoded_packages": 2, "outcomes": {"DECODED": 2}, "accounted": True}],
            "all_expected_packages_accounted": True, "decoded_packages": 2,
            "package_outcomes": {"DECODED": 2}, "status": "ACCOUNTED"}
        publish_manifest(directory, manifest)
        checked, _ = load_manifest(directory)
        with unittest.TestCase().assertRaisesRegex(ValueError, "duplicate/missing/shifted transaction indexes"):
            attach_dataset(db, directory, checked)
    print("Actual duplicate Rust Bronze rows remain present; resealed ACCOUNTED claim rejected PASS")


if __name__ == "__main__":
    fixture_root = pathlib.Path(sys.argv.pop()).resolve() if len(sys.argv) == 2 else None
    test = unittest.main(exit=False)
    if not test.result.wasSuccessful(): raise SystemExit(1)
    if fixture_root is not None:
        actual_shard_regression(fixture_root)
        actual_duplicate_accounting_regression(fixture_root)
