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
if __name__=='__main__':unittest.main()
