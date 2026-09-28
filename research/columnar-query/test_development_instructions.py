"""Synthetic inventory fixtures; never authentic counts or protocol evidence."""
import copy
import json
import unittest
from development_instructions import RULE, build_report, summarize, produce, COHORT_PATH, COHORT_SHA
from development_cohort import build_report as cohort_report
from test_development_cohort import full_admission
from manifest_reader import sha
from mint_timeline import canonical


def fixture():
    a=full_admission()
    for w in a['windows']:
        w['collection_path']='fixture-only/collection.json';w['slot_outcomes']=[]
        for f in w['facts']:
            f['record']['instruction_sha256']='1'*64
            f['record']['event_context']={'event_cpi_sha256':'2'*64}
            f['canonical_record_json']=json.dumps(f['record'],sort_keys=True,separators=(',',':'))
            f['record_sha256']=sha(f['canonical_record_json'].encode())
    c=cohort_report(a)
    a['schema']='OF1_B7_DEVELOPMENT_INSTRUCTIONS_ADMISSION_1'
    for w,cw in zip(a['windows'],c['windows'],strict=True):
        packages=[];links=[]
        for f in [f for m in cw['mints'] for f in m['facts']]:
            pid=f['package_id'];h=f['record_sha256'];trade=f'{pid}:0:TOP';event=f'{pid}:0:CPI:0'
            rows=[]
            for key,kind,inner,cat,data,tf,ef in [(trade,'DECLARED_TOP_LEVEL',None,'ADMITTED_TRADE','1'*64,[h],[]),(event,'RECORDED_CPI',0,'SUPPORTING_EVENT_CPI','2'*64,[],[h])]:
                rows.append({'instruction_id':key,'location':{'kind':kind,'outer_index':0,'inner_order':inner},'category':cat,'instruction_sha256':data,'reference_count':1,
                    'may_hide_trade_observation':False,'reasons':['FIXTURE'],'probes':[],'diagnoses':[], 'trade_fact_sha256s':tf,'event_fact_sha256s':ef,'execution':'NOT_ESTABLISHED_BY_INSTRUCTION_PRESENCE'})
            packages.append({'package_id':pid,'bronze_record_sha256':f['record']['bronze_record_sha256'],'transaction_status':'OK','instructions':rows})
            links.append({'fact_sha256':h,'package_id':pid,'trade_instruction_id':trade,'event_instruction_id':event,'instruction_sha256':'1'*64,'event_cpi_sha256':'2'*64})
        w['instruction_inventory']={'rule_version':RULE,'packages':packages,'fact_links':links,'uncertainty':[],
            'unique_pump_instructions':len(packages)*2,'all_packages_checked':w['counts']['packages'],'failed_packages_checked':w['counts']['failures']}
        w['facts']=[]
    return a,c

class InstructionTests(unittest.TestCase):
    def test_unapproved_cohort_path_or_hash_fails_before_opening_inputs(self):
        for path, digest in [(COHORT_PATH.parent / 'evaluation.json', COHORT_SHA), (COHORT_PATH, '0'*64)]:
            with self.assertRaisesRegex(ValueError, 'approved existing DEVELOPMENT'):
                produce('/nonexistent-reader', 'a'*64, path, digest, '/nonexistent-output')

    def test_complete_reproducible_original_facts_and_pair_evidence(self):
        a,c=fixture();r=build_report(a,c,'a'*64,'b'*64)
        self.assertEqual(r['totals'],c['totals']);self.assertEqual(canonical(r),canonical(build_report(a,c,'a'*64,'b'*64)))
        for w,cw in zip(r['windows'],c['windows'],strict=True):
            self.assertEqual(w['observed_pair_mints'],cw['observed_pair_mints'])
            self.assertEqual(w['summary']['silver_facts'],cw['counts']['silver_facts'])
            self.assertEqual(w['summary']['category_counts']['ADMITTED_TRADE'],cw['counts']['silver_facts'])
            self.assertEqual(w['summary']['negative_conclusion'],'NO_GAP_IDENTIFIED_BY_THIS_INVENTORY_NOT_A_NEGATIVE_LABEL')

    def test_probes_failures_and_unknowns_are_separate_denominators(self):
        a,c=fixture();w=a['windows'][0];inv=w['instruction_inventory'];row=inv['packages'][0]['instructions'][0]
        probe={'evidence_sha256':'3'*64,'original':{'kind':'BUY_INSTRUCTION_LAYOUT','layout_error':{'reason':'WRONG_DISCRIMINATOR'}}}
        row['probes']=[probe,copy.deepcopy(probe)];row['reference_count']=2
        inv['uncertainty']=[{'package_id':'unknown:'+'4'*64,'transaction_status':None,'items':[{'reason':'CPI_INFORMATION_UNAVAILABLE'}]},
            {'package_id':'failed:'+'5'*64,'transaction_status':'ERROR','items':[{'reason':'PROGRAM_ID_UNRESOLVED'},{'reason':'PUMP_LOCATION_UNAVAILABLE'}]}]
        facts={f['record_sha256']:f for m in c['windows'][0]['mints'] for f in m['facts']}
        s=summarize(inv,facts)
        self.assertEqual(s['probe_references'],2);self.assertEqual(s['distinct_instruction_probe_values'],1)
        self.assertEqual(s['duplicate_instruction_references'],1);self.assertEqual(s['uncertainty_items'],3)
        self.assertEqual(s['potentially_hiding_packages'],1);self.assertEqual(s['negative_conclusion'],'UNAVAILABLE')
        self.assertEqual(s['category_counts']['ADMITTED_TRADE'],57)

    def test_mismatched_roles_snapshots_locations_and_fact_bindings_fail(self):
        for variant in ['role','snapshot','mixed','duplicate','missing-cpi','failed','hash','phantom','wrong-event']:
            with self.subTest(variant=variant):
                a,c=fixture();w=a['windows'][0];inv=w['instruction_inventory'];p=inv['packages'][0]
                if variant=='role':w['sample_identity']['b7']['cohort_role']='RESERVED_EVALUATION'
                if variant=='snapshot':w['collection_sha256']='0'*64
                if variant=='mixed':a['windows'][1]=copy.deepcopy(w)
                if variant=='duplicate':p['instructions'].append(copy.deepcopy(p['instructions'][0]))
                if variant=='missing-cpi':p['instructions'].pop()
                if variant=='failed':p['transaction_status']='ERROR'
                if variant=='hash':inv['fact_links'][0]['fact_sha256']='0'*64
                if variant=='phantom':p['instructions'][0]['trade_fact_sha256s'].append('0'*64)
                if variant=='wrong-event':inv['fact_links'][0]['event_instruction_id']=inv['fact_links'][0]['trade_instruction_id']
                with self.assertRaises((ValueError,KeyError)):build_report(a,c,'a'*64,'b'*64)

    def test_many_facts_one_instruction_does_not_multiply_instruction_count(self):
        a,c=fixture();inv=a['windows'][0]['instruction_inventory'];facts={f['record_sha256']:f for m in c['windows'][0]['mints'] for f in m['facts']}
        first=inv['packages'][0];second=inv['packages'].pop(1);link=inv['fact_links'][1]
        link['package_id']=first['package_id'];link['trade_instruction_id']=first['instructions'][0]['instruction_id'];link['event_instruction_id']=first['instructions'][1]['instruction_id']
        facts[link['fact_sha256']]['package_id']=first['package_id']
        first['instructions'][0]['trade_fact_sha256s'].append(link['fact_sha256']);first['instructions'][1]['event_fact_sha256s'].append(link['fact_sha256'])
        inv['unique_pump_instructions']-=2
        s=summarize(inv,facts);self.assertEqual(s['silver_facts'],57);self.assertEqual(s['unique_pump_instructions'],112);self.assertEqual(s['category_counts']['ADMITTED_TRADE'],56)

if __name__=='__main__':unittest.main()
