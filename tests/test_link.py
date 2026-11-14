import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from address_link.link import link
from address_link.pipeline import run
ROOT=Path(__file__).resolve().parents[1]
class LinkTests(unittest.TestCase):
    def setUp(self): self.data=json.loads((ROOT/'fixtures/addresses.json').read_text())
    def test_known_groups_and_conflict(self):
        r=link(self.data);self.assertEqual(r['group_count'],4)
        self.assertEqual([g['status'] for g in r['groups']],['linked','review','singleton','singleton'])
        self.assertEqual(r['groups'][1]['review_reasons'],['conflicting_registry_ids'])
    def test_unknown_unit_never_wildcard(self):
        self.assertEqual([g['members'] for g in link(self.data)['groups']][-2:],[['E'],['F']])
    def test_permutation_invariance(self):
        before=link(self.data);self.data['records'].reverse();self.assertEqual(before,link(self.data))
    def test_transitive_conflict_is_not_silently_merged(self):
        self.data['records'][2]['registry_id']='R1'
        r=link(self.data);g=r['groups'][0]
        self.assertEqual(g['members'],['A','B','C','D']);self.assertEqual(g['status'],'review')
        self.assertIn('different_address_assertions',g['review_reasons'])
    def test_duplicate_id_rejected(self):
        self.data['records'].append(copy.deepcopy(self.data['records'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):link(self.data)
    def test_building_zero_is_significant(self):
        self.data['records'][1]['building']='012';self.assertEqual(link(self.data)['group_count'],5)
    def test_empty_and_invalid_fields(self):
        self.assertEqual(link({'records':[]})['groups'],[])
        self.data['records'][0]['postal']='!!!'
        with self.assertRaises(ValueError):link(self.data)
    def test_cli_preserves_previous_report_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'bad.json';out=p/'out.json';out.write_text('old')
            source.write_text('{"records":[],"records":[]}')
            r=subprocess.run([sys.executable,'-m','address_link.cli','--source',str(source),'--output',str(out)],capture_output=True)
            self.assertEqual(r.returncode,2);self.assertEqual(out.read_text(),'old')
    def test_cli_installed_success(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'result.json'
            r=subprocess.run([sys.executable,'-m','address_link.cli','--source',str(ROOT/'fixtures/addresses.json'),'--output',str(out)],capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(out.read_text()),run(ROOT/'fixtures/addresses.json'))

def rec(i,unit='1',reg=None,city='T',postal='P',street='S',building='B'):
    return {'id':i,'registry_id':reg,'city':city,'postal':postal,'street':street,
            'building':building,'unit':unit}

class DecisionsTests(unittest.TestCase):
    def setUp(self):
        # shared registry R1 gives registry candidate edges A-B, A-C; distinct units keep them otherwise separate
        self.data={'records':[rec('A',reg='R1',unit='1'),rec('B',reg='R1',unit='2'),
                              rec('C',reg='R1',unit='3')]}
    def test_no_decisions_keeps_original_contract(self):
        r=link(self.data)
        self.assertEqual(set(r),{'record_count','group_count','groups','evidence_edges','normalized_records'})
        self.assertNotIn('decision_audit',r)
        self.assertEqual([g['members'] for g in r['groups']],[['A','B','C']])
    def test_same_closure_rejects_different_in_transitive_contradiction(self):
        with self.assertRaisesRegex(ValueError,'same closure'):
            link(self.data,{'same':[['A','B'],['B','C']],'different':[['A','C']]})
        # contradiction discovered transitively even when the different pair is no direct same edge
        with self.assertRaisesRegex(ValueError,'same closure'):
            link(self.data,{'same':[['B','C'],['A','B']],'different':[['C','A']]})
    def test_different_blocks_candidate_edge_and_records_min_pair(self):
        r=link(self.data,{'same':[],'different':[['A','B']]})
        self.assertEqual([g['members'] for g in r['groups']],[['A','C'],['B']])
        audit=r['decision_audit']['candidate_edges']
        blocked=[e for e in audit if e['outcome']=='blocked']
        self.assertEqual([(e['left'],e['right'],e['blocked_by']) for e in blocked],[('A','B',['A','B'])])
        self.assertEqual(r['evidence_edges'],
                         [{'left':e['left'],'right':e['right'],'reason':e['reason']}
                          for e in audit if e['outcome']=='accepted'])
    def test_indirect_detour_blocked_by_transitive_different(self):
        # A-B joins first; B-C would then put different pair A,C in one group, so it is skipped
        r=link(self.data,{'same':[],'different':[['A','C']]})
        self.assertEqual([g['members'] for g in r['groups']],[['A','B'],['C']])
        outcomes=[(e['left'],e['right'],e['outcome'],e.get('blocked_by'))
                  for e in r['decision_audit']['candidate_edges']]
        self.assertEqual(outcomes,[('A','B','accepted',None),('A','C','blocked',['A','C'])])
    def test_blocked_by_is_lexicographically_smallest_pair(self):
        decisions={'same':[['A','B']],'different':[['B','C'],['A','C']]}
        r=link(self.data,decisions)
        blocked=[e for e in r['decision_audit']['candidate_edges'] if e['outcome']=='blocked']
        self.assertTrue(blocked);self.assertEqual({tuple(e['blocked_by']) for e in blocked},{('A','C')})
    def test_manual_same_keeps_registry_and_address_conflicts_in_review(self):
        data={'records':[rec('A',reg='R1',unit='1'),rec('B',reg='R2',unit='2')]}
        r=link(data,{'same':[['A','B']],'different':[]})
        g=r['groups'][0]
        self.assertEqual(g['members'],['A','B']);self.assertEqual(g['status'],'review')
        self.assertEqual(g['review_reasons'],['conflicting_registry_ids','different_address_assertions'])
    def test_group_id_is_smallest_member_under_manual_same(self):
        data={'records':[rec('Z',reg='R9',unit='9'),rec('A',reg='R9',unit='8')]}
        r=link(data,{'same':[],'different':[]})
        self.assertEqual(r['groups'][0]['group_id'],'A')
        self.assertEqual(r['groups'][0]['members'],['A','Z'])
    def test_unicode_addresses_and_decisions(self):
        data={'records':[
            rec('A',street='Ｍaple  Road',reg='R1',unit='1'),
            rec('B',street='Maple Road',reg='R1',unit='2'),
            rec('C',street='Maple Road',reg='R1',unit='3')]}
        r=link(data,{'same':[['A','B']],'different':[['B','C']]})
        # NFKC + casefold lets A and B share the registry candidate; manual same keeps them together,
        # C must stay out because B-C is a different pair
        self.assertEqual([g['members'] for g in r['groups']],[['A','B'],['C']])
        self.assertEqual(r['decision_audit']['same'],[['A','B']])
        self.assertEqual(r['decision_audit']['different'],[['B','C']])
    def test_unordered_records_and_reversed_decisions_are_identical(self):
        decisions={'same':[['B','A']],'different':[['C','A']]}
        first=link(copy.deepcopy(self.data),decisions)
        shuffled=copy.deepcopy(self.data);shuffled['records'].reverse()
        reversed_decisions={'same':[list(reversed(p)) for p in reversed(decisions['same'])],
                            'different':[list(reversed(p)) for p in reversed(decisions['different'])]}
        second=link(shuffled,reversed_decisions)
        self.assertEqual(first,second)
        # audit and evidence ordering itself is fixed
        self.assertEqual([(e['reason'],e['left'],e['right']) for e in first['decision_audit']['candidate_edges']],
                         sorted((e['reason'],e['left'],e['right']) for e in first['decision_audit']['candidate_edges']))
    def test_decision_validation_errors(self):
        cases=[
            ({'same':[['A','X']],'different':[]},'unknown id'),
            ({'same':[['A','A']],'different':[]},'self pair'),
            ({'same':[['A','B'],['B','A']],'different':[]},'duplicate decision'),
            ({'same':[],'different':[['A','B'],['B','A']]},'duplicate decision'),
            ({'same':[['A','B']],'different':[['A','B']]},'both same and different'),
            ({'same':[]},'only same and different'),
            ({'same':[],'different':[],'extra':[]},'only same and different'),
            ({'same':['AB'],'different':[]},'pair of id strings'),
        ]
        for decisions,message in cases:
            with self.subTest(message),self.assertRaisesRegex(ValueError,message):
                link(self.data,decisions)
    def test_empty_decisions_are_accepted_and_audited(self):
        r=link(self.data,{'same':[],'different':[]})
        self.assertEqual(r['decision_audit'],{'same':[],'different':[],
                         'candidate_edges':r['decision_audit']['candidate_edges']})
        self.assertTrue(all(e['outcome']=='accepted' for e in r['decision_audit']['candidate_edges']))

class DecisionsCliTests(unittest.TestCase):
    def test_cli_runs_decisions_and_preserves_report_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'source.json';decisions=p/'decisions.json';out=p/'out.json'
            source.write_text(json.dumps({'records':[rec('A',reg='R1',unit='1'),
                                                     rec('B',reg='R1',unit='2'),
                                                     rec('C',reg='R1',unit='3')]}))
            decisions.write_text(json.dumps({'same':[['A','B'],['B','C']],'different':[['A','C']]}))
            out.write_text('old-report')
            r=subprocess.run([sys.executable,'-m','address_link.cli','--source',str(source),
                              '--output',str(out),'--decisions',str(decisions)],capture_output=True,text=True)
            self.assertEqual(r.returncode,2,r.stderr)
            self.assertIn('same closure',r.stderr)
            self.assertEqual(out.read_text(),'old-report')
    def test_cli_decisions_success_matches_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'source.json';decisions=p/'decisions.json';out=p/'out.json'
            source.write_text(json.dumps({'records':[rec('A',reg='R1',unit='1'),
                                                     rec('B',reg='R1',unit='2'),
                                                     rec('C',reg='R1',unit='3')]}))
            decisions.write_text(json.dumps({'same':[],'different':[['A','C']]}))
            r=subprocess.run([sys.executable,'-m','address_link.cli','--source',str(source),
                              '--output',str(out),'--decisions',str(decisions)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(out.read_text()),run(source,decisions))
            self.assertEqual(json.loads(r.stdout)['decision_audit']['different'],[['A','C']])
if __name__=='__main__':unittest.main()
