"""Denominators and missing-data semantics; synthetic counts, no domain decode."""
import pathlib
import tempfile
import unittest
from coverage import coverage, bound_json, sha, rust_slots, suitability


def actual(slot, total, decoded=0, missing=0, unsupported=0, quarantined=0):
    return dict(slot=str(slot), present_envelopes=str(total), decoded=str(decoded), missing=str(missing),
                unsupported=str(unsupported), quarantined=str(quarantined))


def proof(slot, envelopes):
    return dict(slot=str(slot),transaction_envelopes=envelopes,raw_sha256='a'*64,stages={'car_slot':'VERIFIED'})


def extent(slot, count):
    return dict(slot=str(slot),known_indices=str(count),first_index='0' if count else None,last_index=str(count-1) if count else None)


class CoverageTests(unittest.TestCase):
    def test_verified_empty_slot_does_not_disappear(self):
        rows=coverage([10,11],[proof(10,0),proof(11,1)],[actual(11,1,decoded=1)],[extent(11,1)])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['inventory_state'],'VERIFIED_EMPTY')

    def test_missing_whole_slot_has_unknown_envelope_denominator(self):
        row=coverage([10],[],[])[0]
        self.assertIsNone(row['expected_envelopes'])
        self.assertEqual(row['inventory_state'],'INCOMPLETE')
        self.assertIsNone(row['missing_rows'])

    def test_missing_records_not_confused_with_missing_domain_fields(self):
        row=coverage([10],[proof(10,3)],[actual(10,2,decoded=1,missing=1)])[0]
        self.assertEqual(row['missing_rows'],1)
        self.assertFalse(row['reconciled'])
        self.assertEqual(row['outcomes']['missing'],'1')

    def test_all_outcomes_stay_in_denominator(self):
        row=coverage([10],[proof(10,4)],[actual(10,4,decoded=1,missing=1,unsupported=1,quarantined=1)],[extent(10,4)])[0]
        self.assertTrue(row['reconciled'])
        self.assertEqual(row['present_envelopes'],4)

    def test_unexplained_outcome_fails(self):
        with self.assertRaisesRegex(ValueError,'unexplained'):
            coverage([10],[proof(10,2)],[actual(10,2,decoded=1)])

    def test_unselected_slot_rejected(self):
        with self.assertRaisesRegex(ValueError,'unselected'):
            coverage([10],[],[actual(11,1,decoded=1)])

    def test_duplicate_inventory_rejected(self):
        with self.assertRaises(ValueError): coverage([10,10],[],[])
        with self.assertRaises(ValueError): coverage([10],[proof(10,1),proof(10,1)],[])
        with self.assertRaises(ValueError): coverage([10],[],[actual(10,1,decoded=1)]*2)

    def test_extra_rows_not_misreported_complete(self):
        row=coverage([10],[proof(10,1)],[actual(10,2,decoded=2)])[0]
        self.assertEqual(row['unaccounted_extra_rows'],1)
        self.assertFalse(row['reconciled'])

    def test_original_rust_hash_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'quality.json'
            raw=b'{"original":true}'
            path.write_bytes(raw)
            self.assertTrue(bound_json(path,sha(raw))['original'])
            path.write_bytes(b'{"original":false}')
            with self.assertRaisesRegex(ValueError,'hash mismatch'): bound_json(path,sha(raw))

    def test_inventory_bounds(self):
        with self.assertRaises(ValueError): coverage(list(range(129)),[],[])

    def test_rust_single_and_multi_slot_shapes(self):
        single=proof(10,0)
        self.assertEqual(rust_slots(single),[single])
        self.assertEqual(rust_slots({'slots':[single]}),[single])
        with self.assertRaises(ValueError): rust_slots({})

    def test_matching_counts_cannot_hide_integrity_failure(self):
        for reason in ['DUPLICATE_TRANSACTION_IDENTITY','PARQUET_RUST_SOURCE_BINDING_MISMATCH']:
            summary={'slots':[{'reconciled':True}], 'silver':{'facts':4}, 'buy_diagnostics':1, 'integrity_errors':[reason]}
            matrix=suitability(summary)
            self.assertEqual(matrix[0]['result_kind'],'ENGINEERING_FAILURE')
            self.assertIn(reason,matrix[0]['missing'])

    def test_matching_count_without_expected_indices_is_incomplete(self):
        shifted=dict(extent(10,2),first_index='1',last_index='2')
        row=coverage([10],[proof(10,2)],[actual(10,2,decoded=2)],[shifted])[0]
        self.assertFalse(row['reconciled'])
        self.assertFalse(row['transaction_index_extent_exact'])
        unknown=dict(extent(10,2),known_indices='1')
        self.assertFalse(coverage([10],[proof(10,2)],[actual(10,2,decoded=2)],[unknown])[0]['reconciled'])


if __name__=='__main__': unittest.main()
