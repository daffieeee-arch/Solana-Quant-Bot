"""Query only synthetic Rust-result shapes; never decode/admit protocol bytes."""
import json
import pathlib
import unittest

from coverage import buy_sell_question, objects, suitability, render
from query import connect, rows
from test_coverage import synthetic_silver, side_summary


class ExactQuoteQueryTests(unittest.TestCase):
    def test_buy_card_preserves_requested_vs_reported_integers_and_unknown_state(self):
        # Table values mirror query output shape only; not constructed protocol evidence.
        names = ['slot','transaction_index','record_schema','mint','transaction_status',
                 'spendable_quote_in_raw_u64','min_tokens_out_raw_u64','token_amount_raw_u64',
                 'account_contents_verified','committed_account_state']
        query = {'columns':[{'name':n} for n in names], 'sql':'synthetic query-result fixture',
                 'rows':[['9','7','SEPARATE_BUY_SCHEMA','<unsafe&mint>','OK',str(2**64-1),
                          '9007199254740993','9007199254740994',False,None]]}
        summary={'slots':[], 'silver':{'facts':1,'parent_packages':1}, 'buy_diagnostics':0,
                 'integrity_errors':[], 'present_envelopes':1,'expected_envelopes':1,
                 'pump_positive':1,'status_ok':1,'status_error':0,'status_unknown':0}
        result={'summary':summary,'queries':{'buys':query},'suitability_matrix':[],
                'evidence':{'slice_class':'RESEARCH_SAMPLING'},'inventory':{},
                'selection':{'status':'ACCOUNTED'},'sample_identity':{},'bindings':{},'pilot':None}
        output=render(result)
        self.assertEqual(output,render(result))
        self.assertIn('Brongebonden exact-quote buys',output)
        self.assertIn('Instructiegrenzen: spendable_quote_in <b>18446744073709551615</b>',output)
        self.assertIn('min_tokens_out <b>9007199254740993</b>',output)
        self.assertIn('token_amount <b>9007199254740994</b>',output)
        self.assertIn('UNAVAILABLE. Dit is geen prijs, fill',output)
        self.assertIn('&lt;unsafe&amp;mint&gt;',output)
        self.assertNotIn('<unsafe&mint>',output)
        self.assertIn('geen ontbrekende bonding-curve-accountinhoud',output)

    def test_diagnostics_preserve_failed_unknown_and_duplicate_packages(self):
        sql = json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        diagnosis = {'evaluated_profile': 'separate-exact-quote', 'disposition': 'ADMITTED_RECORDED_FACTS',
                     'account_count': 27, 'instruction': {'spendable_quote_in_raw_u64': str(2**64-1),
                                                       'min_tokens_out_raw_u64': '9007199254740993'},
                     'event_reported': {'mint_address': 'observed-mint', 'token_amount_raw_u64': '9007199254740994'}}
        with connect() as db:
            db.execute('CREATE TABLE bronze(slot UBIGINT,transaction_index UBIGINT,record_ordinal UBIGINT,transaction_status VARCHAR,record_bytes BLOB)')
            for n, status in enumerate(['OK', 'ERROR', None]):
                observed = dict(diagnosis, disposition='NOT_ADMITTED' if status != 'OK' else diagnosis['disposition'])
                body = {'transaction': {'pump_buy_exact_quote_v2_analysis': [observed, observed]}}
                db.execute('INSERT INTO bronze VALUES (9,?,?,?,?)', [n,n,status,json.dumps(body).encode()])
            for n, absent in enumerate([{}, {'pump_buy_exact_quote_v2_analysis': None}, {'pump_buy_exact_quote_v2_analysis': []}],3):
                db.execute('INSERT INTO bronze VALUES (9,?,?,NULL,?)',[n,n,json.dumps({'transaction':absent}).encode()])
            result = objects(rows(db.execute(sql['exact_quote_buy_details'])))
            self.assertEqual(len(result),6)
            self.assertEqual([r['transaction_status'] for r in result],['OK','OK','ERROR','ERROR',None,None])
            self.assertTrue(all(r['spendable_quote_in_raw_u64']==str(2**64-1) for r in result))
            self.assertTrue(all(r['min_tokens_out_raw_u64']=='9007199254740993' for r in result))
            self.assertTrue(all(r['token_amount_raw_u64']=='9007199254740994' for r in result))
            self.assertTrue(all(r['event_inner_order'] is None for r in result))

    def test_buy_sells_are_separate_and_do_not_invent_pairs_or_economics(self):
        sql = json.loads(pathlib.Path(__file__).with_name('coverage.sql.json').read_bytes())
        with connect() as db:
            synthetic_silver(db,[{'is_buy': True, 'mint':'A','token_amount_raw_u64':2**64-1,
                                 'transaction_status':'OK','slot':9,'transaction_index':1,'outer_index':3,'event_inner_order':6},
                                {'is_buy':False,'mint':'B','token_amount_raw_u64':7,
                                 'transaction_status':'OK','slot':9,'transaction_index':2,'outer_index':4,'event_inner_order':2}])
            summary = side_summary(db,sql)
            summary.update(slots=[],silver={'facts':2},buy_diagnostics=3)
            question = buy_sell_question(summary,objects(rows(db.execute(sql['mint_side_inventory']))))
            self.assertEqual(summary['supported_sides']['buy_facts'],1)
            self.assertEqual(summary['supported_sides']['sell_facts'],1)
            self.assertEqual(question['present']['mints_with_both_admitted_sides'],[])
            self.assertEqual(question['result_kind'],'INSUFFICIENT_SUITABLE_DATA')
            self.assertIn('Admitted exact-quote buys',question['next_step'])
            matrix = suitability(summary)
            self.assertEqual(matrix[2]['present']['supported_sides']['buy_facts'],1)
            self.assertIn('economic unit evidence',matrix[2]['missing'])
            self.assertFalse(question['research_ready'])


if __name__ == '__main__':
    unittest.main()
