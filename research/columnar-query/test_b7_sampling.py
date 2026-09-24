"""Synthetic proposal regressions; no acquisition or protocol interpretation."""
import copy
import hashlib
import json
import unittest
from b7_sampling import PROPOSAL, budgets, canonical, select, precision_bound


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(PROPOSAL.read_bytes())
        self.selection = select(self.plan)

    def synthetic_ranges(self):
        return {'schema':'OF1_OFFLINE_WINDOW_RANGES_1','network_authorized':False,
                'selection_sha256':hashlib.sha256(canonical(self.selection)).hexdigest(),
                'windows':[{'selection':w,'status':'PLANNED','prepared':{
                    'start_slot':w['start_slot'],'end_slot':w['end_slot_exclusive'],
                    'requests':[{'kind':{'kind':'CAR_RANGE','slot':s,'start':s*10,'end_exclusive':s*10+10}}
                                for s in range(w['start_slot'],w['end_slot_exclusive'])],
                    'index_reported_absent':[]}} for w in self.selection['windows']]}

    def test_fixed_draw_independent_of_index_and_outcomes(self):
        self.assertEqual(canonical(self.selection), canonical(select(self.plan)))
        self.assertEqual([w['start_slot'] for w in self.selection['windows']],
            [422526144,422552336,422692432,422659936,422802704,422778400,422901312,422760864,
             422623328,422579632,422675168,422681872,422730416,422742656,422789360,422781216])
        self.assertEqual([p['eligible_windows'] for p in self.selection['populations']], [13496,13499])
        used=[]
        for w in self.selection['windows']:
            slots=set(range(w['start_slot'],w['end_slot_exclusive']))
            self.assertFalse(set(used)&slots); used.extend(slots)
            for a,b in self.plan['inspected_exclusions']:
                self.assertFalse(slots&set(range(a,b)))
        self.assertEqual(len(used),256)
        self.assertEqual(sum(w['stage']==1 for w in self.selection['windows']),8)
        self.assertLess(max(w['end_slot_exclusive'] for w in self.selection['windows'] if w['role']=='DEVELOPMENT'),
                        min(w['start_slot'] for w in self.selection['windows'] if w['role']=='RESERVED_EVALUATION'))

    def test_denied_authority_and_unsupported_scope(self):
        for key in ['approved','network_enabled','payload_approved','metadata_approved','execution_approved','research_ready']:
            p=copy.deepcopy(self.plan);p[key]=True
            with self.assertRaises(ValueError):select(p)
        p=copy.deepcopy(self.plan);p['strata'][1]=p['strata'][0]
        with self.assertRaises(ValueError):select(p)

    def test_retry_bytes_and_campaign_stop_no_redraw(self):
        ranges=self.synthetic_ranges(); b=budgets(self.plan,self.selection,ranges)
        self.assertEqual(b['unique_payload_entity_bytes'],2560)
        self.assertEqual(b['combined_reserved_entity_bytes'],2560*3+16*15576576)
        self.assertEqual(b['combined_max_attempts'],960)
        p=copy.deepcopy(self.plan);p['budget']['campaign_reserved_entity_bytes_cap']=1
        with self.assertRaises(ValueError):budgets(p,self.selection,ranges)
        self.assertEqual(self.selection,select(self.plan))

    def test_stop_unknown_and_index_absence_are_not_zero_or_replacement(self):
        r=self.synthetic_ranges(); r['windows'][0]={'selection':self.selection['windows'][0],
            'status':'STOP_NO_REPLACEMENT','error':'bounded planner rejected'}
        b=budgets(self.plan,self.selection,r)
        self.assertIsNone(b['unique_payload_entity_bytes'])
        self.assertEqual(len(b['windows']),16)
        r=self.synthetic_ranges();p=r['windows'][0]['prepared'];q=p['requests'].pop(0)
        p['index_reported_absent']=[{'slot':q['kind']['slot'],'reason':'INDEX_REPORTED_ABSENT'}]
        b=budgets(self.plan,self.selection,r)
        self.assertEqual(b['windows'][0]['stop'],'INDEX_ABSENCE_NOT_PROVEN_EMPTY')
        self.assertFalse(b['execution_ready'])

    def test_wrong_binding_missing_duplicate_and_changed_order_reject(self):
        for change in ['hash','missing','duplicate','order']:
            r=self.synthetic_ranges()
            if change=='hash':r['selection_sha256']='0'*64
            elif change=='missing':r['windows'].pop()
            elif change=='duplicate':r['windows'][0]['prepared']['requests'][1]=r['windows'][0]['prepared']['requests'][0]
            else:r['windows'].reverse()
            with self.assertRaises(ValueError):budgets(self.plan,self.selection,r)

    def test_missingness_widens_bound_and_tiny_pilot_is_not_sufficient(self):
        self.assertTrue(precision_bound(8,0,0)['precision_target_met'])
        self.assertFalse(precision_bound(8,0,8)['precision_target_met'])
        self.assertFalse(precision_bound(8,4,0)['precision_target_met'])
        self.assertFalse(precision_bound(1,0,0)['precision_target_met'])
        self.assertEqual(precision_bound(8,0,8)['upper'],1)
        self.assertTrue(precision_bound(8,0,0)['semantic_and_source_gates_not_evaluated'])
        with self.assertRaises(ValueError):precision_bound(8,4,5)
        p=copy.deepcopy(self.plan);p['new_sample_class_proposed']='ENGINEERING_VALIDATION_ONLY'
        with self.assertRaises(ValueError):select(p)


if __name__=='__main__':unittest.main()
