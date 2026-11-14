import json, subprocess, sys, tempfile, unittest
from pathlib import Path
from address_link.audit import audit
from address_link.link import link
from address_link.pipeline import audit as audit_path, run
ROOT=Path(__file__).resolve().parents[1]

def rec(i,unit='1',reg=None,city='T',postal='P',street='S',building='B'):
    return {'id':i,'registry_id':reg,'city':city,'postal':postal,'street':street,
            'building':building,'unit':unit}

class AuditTests(unittest.TestCase):
    def setUp(self): self.data=json.loads((ROOT/'fixtures/addresses.json').read_text())
    def codes(self,report,index):
        return next(row['rule_codes'] for row in report['rows'] if row['index']==index)
    def test_valid_fixture_has_no_problems_and_no_link_output(self):
        r=audit(self.data)
        self.assertEqual(r,{'record_count':6,'valid_count':6,'problem_count':0,
                             'rule_counts':{},'rows':[]})
        self.assertNotIn('groups',r);self.assertNotIn('evidence_edges',r)
        # The default linkage contract and its result are untouched by audit mode.
        self.assertEqual(set(link(self.data)),{'record_count','group_count','groups',
                                               'evidence_edges','normalized_records'})
    def test_collects_all_independent_problems_of_a_row(self):
        row=rec('A');row['postal']='!!!';row['unit']='­';row['registry_id']=''
        row['extra']='x';row['street']=' '
        r=audit({'records':[row]})
        self.assertEqual(r['rule_counts'],{'postal_empty_normalized':1,
            'registry_id_empty':1,'street_empty':1,'unit_empty_normalized':1,
            'unexpected_fields':1})
        self.assertEqual(self.codes(r,0),
                         ['postal_empty_normalized','registry_id_empty',
                          'street_empty','unexpected_fields','unit_empty_normalized'])
    def test_row_level_codes(self):
        r=audit({'records':[None,{'id':'A','city':'T'}]})
        self.assertEqual(self.codes(r,0),['record_not_object'])
        self.assertEqual(self.codes(r,1),['missing_fields'])
        self.assertEqual(r['rule_counts'],{'missing_fields':1,'record_not_object':1})
    def test_id_checks_are_independent(self):
        data={'records':[rec(''),rec('x'*257),rec(7)]}
        r=audit(data)
        self.assertEqual(self.codes(r,0),['id_empty'])
        self.assertEqual(self.codes(r,1),['id_too_long'])
        self.assertEqual(self.codes(r,2),['id_not_text'])
    def test_duplicate_id_marks_every_involved_row(self):
        r=audit({'records':[rec('A'),rec('B'),rec('A'),rec('A')]})
        self.assertEqual([row['index'] for row in r['rows']],[0,2,3])
        self.assertTrue(all(self.codes(r,i)==['id_duplicate'] for i in (0,2,3)))
        self.assertEqual(r['rule_counts'],{'id_duplicate':3})
        self.assertEqual(r['valid_count'],1);self.assertEqual(r['problem_count'],3)
    def test_duplicate_ids_compare_exact_strings_only(self):
        r=audit({'records':[rec('A'),rec('A'),rec('Ａ'),rec('a')]})
        self.assertEqual([row['index'] for row in r['rows']],[0,1])
    def test_unicode_is_not_double_counted_with_normalized_emptiness(self):
        data={'records':[rec('A',city='Ａ',postal='　',street='­')]}
        # A fullwidth A is a valid city, the ideographic space is raw-empty and the
        # soft hyphen only disappears after NFKC, so each row reports one distinct code.
        r=audit(data)
        self.assertEqual(self.codes(r,0),['postal_empty','street_empty_normalized'])
    def test_non_ascii_valid_values_have_no_codes(self):
        data={'records':[rec('A',city='東京',postal='甲1',street='表参道',building='番地')]}
        self.assertEqual(audit(data)['problem_count'],0)
    def test_order_is_source_index_and_stable_under_permutation(self):
        a=rec('B');a['city']=''
        b=rec('A');b['unit']=''
        r=audit({'records':[a,b]})
        self.assertEqual([row['index'] for row in r['rows']],[0,1])
        self.assertEqual(r['rows'][0]['rule_codes'],['city_empty'])
        self.assertEqual(r['rows'][1]['rule_codes'],['unit_empty'])
        r2=audit({'records':[b,a]})
        self.assertEqual(r2['rows'][0]['rule_codes'],['unit_empty'])
        self.assertEqual(r2['rows'][1]['rule_codes'],['city_empty'])
    def test_root_structure_failure_stays_whole_batch(self):
        with self.assertRaisesRegex(ValueError,'records'):audit({'items':[]})
        with self.assertRaisesRegex(ValueError,'records'):audit([])
        # A present but non-array records member fails before any row is examined.
        with self.assertRaisesRegex(ValueError,'array'):audit({'records':{}})
        with self.assertRaisesRegex(ValueError,'array'):audit({'records':[[]]*100001})
    def test_audit_does_not_link_or_raise_on_row_contract_errors(self):
        data={'records':[rec('A'),rec('A'),rec('B',unit='!!!')]}
        # link still rejects duplicates; audit reports them instead.
        with self.assertRaises(ValueError):link(data)
        self.assertEqual(audit(data)['problem_count'],3)
    def test_value_checks_stay_independent(self):
        # A long, control-bearing value whose normalized form is also empty keeps
        # all three codes on the same row, sorted within that row.
        value='!'*257+'\x01'
        r=audit({'records':[rec('A',street=value)]})
        self.assertEqual(self.codes(r,0),
                         ['street_control_char','street_empty_normalized','street_too_long'])
        self.assertEqual(r['rule_counts'],{'street_control_char':1,
            'street_empty_normalized':1,'street_too_long':1})
    def test_rule_counts_count_rows_not_occurrences(self):
        row=rec('A',city='',postal='')
        r=audit({'records':[row]})
        self.assertEqual(r['rule_counts'],{'city_empty':1,'postal_empty':1})
    def test_empty_batch_is_valid(self):
        self.assertEqual(audit({'records':[]}),
                         {'record_count':0,'valid_count':0,'problem_count':0,
                          'rule_counts':{},'rows':[]})
    def test_unit_and_registry_null_are_allowed(self):
        self.assertEqual(audit({'records':[rec('A',unit=None,reg=None)]})['problem_count'],0)
        self.assertEqual(self.codes(audit({'records':[rec('A',unit='')]}),0),['unit_empty'])

class AuditPipelineTests(unittest.TestCase):
    def test_audit_path_matches_library_audit(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'source.json';p.write_text(json.dumps({'records':[rec('A',city='')]}))
            self.assertEqual(audit_path(p),audit({'records':[rec('A',city='')]}))
    def test_run_audit_only_reads_source_without_linkage(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'source.json';p.write_text(json.dumps({'records':[rec('A'),rec('A')]}))
            self.assertEqual(run(p,audit_only=True),audit(json.loads(p.read_text())))
    def test_run_rejects_decisions_with_audit_only(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'source.json';p.write_text(json.dumps({'records':[rec('A')]}))
            with self.assertRaisesRegex(ValueError,'--audit-only'):
                run(p,Path(td)/'decisions.json',audit_only=True)

class AuditCliTests(unittest.TestCase):
    def run_cli(self,source,out,*extra):
        return subprocess.run([sys.executable,'-m','address_link.cli','--source',str(source),
                               '--output',str(out),*extra],capture_output=True,text=True)
    def test_cli_audit_writes_report_and_preserves_default_output(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'source.json';out=p/'audit.json';link_out=p/'link.json'
            source.write_text(json.dumps({'records':[rec('A',reg='R1',unit='1'),
                                                     rec('A',reg='R2',unit='2')]}))
            out.write_text('old-audit')
            r=self.run_cli(source,out,'--audit-only')
            self.assertEqual(r.returncode,0,r.stderr)
            report=json.loads(out.read_text())
            self.assertEqual(report,audit(json.loads(source.read_text())))
            self.assertNotIn('groups',report)
            r2=self.run_cli(source,link_out)
            self.assertEqual(r2.returncode,2);self.assertIn('duplicate',r2.stderr)
            self.assertFalse(link_out.exists())
            # default result for a valid batch is exactly the pre-existing contract.
            valid=p/'valid.json';valid.write_text(json.dumps({'records':[rec('A')]}))
            self.assertEqual(self.run_cli(valid,p/'v.json').returncode,0)
            self.assertEqual(json.loads((p/'v.json').read_text()),run(valid))
    def test_cli_failure_keeps_old_audit_report(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);bad=p/'bad.json';out=p/'out.json';out.write_text('old-report')
            cases=('{"records":[],"records":[]}','{"items":[]}','[]','{"records":{}}',
                   'not json')
            for raw in cases:
                bad.write_text(raw)
                r=self.run_cli(bad,out,'--audit-only')
                self.assertEqual(r.returncode,2,r.stderr)
                self.assertEqual(out.read_text(),'old-report')
    def test_audit_only_rejects_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'source.json';decisions=p/'decisions.json';out=p/'out.json'
            source.write_text(json.dumps({'records':[rec('A')]}))
            decisions.write_text(json.dumps({'same':[],'different':[]}))
            out.write_text('old')
            r=self.run_cli(source,out,'--audit-only','--decisions',str(decisions))
            self.assertEqual(r.returncode,2);self.assertEqual(out.read_text(),'old')
    def test_audit_only_stdout_matches_report(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);source=p/'source.json';out=p/'out.json'
            source.write_text(json.dumps({'records':[rec('A',city='!!!'),rec('A')]}))
            r=self.run_cli(source,out,'--audit-only')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(r.stdout),json.loads(out.read_text()))

if __name__=='__main__':unittest.main()
