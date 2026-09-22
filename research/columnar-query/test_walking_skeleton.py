"""Contract tests for the bounded Raw -> Bronze -> Silver presenter."""
import copy
import json
import pathlib
import unittest

import walking_skeleton as skeleton


def manifest_fixture():
    sample = {
        "schema": "OF1_FIXED_PILOT_SAMPLE_1",
        "sample_class": "RESEARCH_SAMPLING",
        "start_slot": 100,
        "end_slot_exclusive": 103,
        "selected_center": 101,
    }
    return {
        "state": "COMPLETE",
        "plan_sha256": "a" * 64,
        "plan": {
            "logical_selection": [
                {"slot": 100, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
                {"slot": 101, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
                {"slot": 102, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
            ],
            "sources": [{"source_id": "pilot", "sample_identity": sample, "run_id": "run-1", "run_root": "/fixture", "bindings": {"receipts": []}}],
        },
        "batches": [
            {"batch_id": f"batch-{slot}", "source_id": "pilot", "selected_slots": [slot], "receipt_sequences": [slot - 96], "parquet_manifest_path": f"batch-{slot}/parquet/manifest.json", "parquet_manifest_sha256": "b" * 64}
            for slot in (100, 101, 102)
        ],
    }


class WalkingSkeletonTests(unittest.TestCase):
    def test_fixed_range_is_contiguous_and_preserves_native_sample_role(self):
        selected, batches = skeleton.selected_inventory(manifest_fixture(), 100, 103)
        self.assertEqual([row["slot"] for row in selected], [100, 101, 102])
        self.assertEqual([batch["selected_slots"] for batch in batches], [[100], [101], [102]])

    def test_gaps_context_reclassification_and_oversized_ranges_fail_closed(self):
        for start, end in [(99, 102), (100, 104)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                skeleton.selected_inventory(manifest_fixture(), start, end)
        changed = copy.deepcopy(manifest_fixture())
        changed["plan"]["logical_selection"][1]["role"] = "POSTHOC_DESCRIPTIVE_CONTEXT"
        with self.assertRaisesRegex(ValueError, "native original"):
            skeleton.selected_inventory(changed, 100, 103)

    def test_engineering_validation_is_never_reclassified_as_research(self):
        manifest = manifest_fixture()
        manifest["plan"]["sources"][0]["sample_identity"] = None
        manifest["plan"]["logical_selection"] = [
            {"slot": 100, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
            {"slot": 101, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
            {"slot": 102, "source_id": "pilot", "role": "ORIGINAL_SELECTION"},
        ]
        selected, _ = skeleton.selected_inventory(manifest, 100, 103)
        self.assertIsNone(manifest["plan"]["sources"][0]["sample_identity"])
        self.assertEqual([row["role"] for row in selected], ["ORIGINAL_SELECTION"] * 3)
        self.assertFalse(any(row.get("sample_class") == "RESEARCH_SAMPLING" for row in selected))
        self.assertFalse(skeleton.research_input_allowed("ENGINEERING_VALIDATION_ONLY", True))
        self.assertFalse(skeleton.research_input_allowed("RESEARCH_SAMPLING", False))

    def test_receipt_sequences_are_scoped_to_the_selected_source(self):
        plan = {
            "sources": [
                {"source_id": "pilot", "bindings": {"receipts": [{"sequence": 4, "raw_bytes": 10, "raw_sha256": "pilot"}]}},
                {"source_id": "context", "bindings": {"receipts": [{"sequence": 4, "raw_bytes": 20, "raw_sha256": "context"}]}},
            ],
            "batches": [{"batch_id": "pilot-batch", "source_id": "pilot", "receipt_sequences": [4]}],
        }
        receipts = skeleton._source_receipts(plan, [{"batch_id": "pilot-batch"}])
        self.assertEqual(receipts, [{"sequence": 4, "raw_bytes": 10, "raw_sha256": "pilot", "source_id": "pilot"}])

    def test_canonical_output_preserves_exact_integers_and_replays_identically(self):
        value = {"u64": 18446744073709551615, "signed": -9007199254740993, "missing": None}
        first = skeleton.canonical(value)
        second = skeleton.canonical(json.loads(first))
        self.assertEqual(first, second)
        self.assertIn(b'"18446744073709551615"', first)
        self.assertIn(b'"-9007199254740993"', first)
        self.assertEqual(skeleton.canonical_line(value).count(b"\n"), 1)
        with self.assertRaisesRegex(ValueError, "floating-point"):
            skeleton.canonical({"bad": 1.5})
        self.assertEqual(skeleton._logical_identity([b"a", b"b"]), skeleton._logical_identity([b"a", b"b"]))
        self.assertNotEqual(skeleton._logical_identity([b"a", b"b"]), skeleton._logical_identity([b"b", b"a"]))


if __name__ == "__main__":
    unittest.main()
