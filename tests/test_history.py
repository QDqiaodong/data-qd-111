import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from address_link import history
from address_link.history import as_of, import_report
from address_link.link import link

ROOT = Path(__file__).resolve().parents[1]


def rec(i, unit='1', reg=None, city='T', postal='P', street='S', building='B'):
    return {'id': i, 'registry_id': reg, 'city': city, 'postal': postal,
            'street': street, 'building': building, 'unit': unit}


def report(records):
    return link({'records': records})


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def intervals(database):
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            'SELECT id, version_from, version_to, registry_id, city, postal, '
            'street, building, unit FROM history_interval '
            'ORDER BY id, version_from').fetchall()
    finally:
        connection.close()


def versions(database):
    connection = sqlite3.connect(database)
    try:
        return [row[0] for row in connection.execute(
            'SELECT version FROM history_version ORDER BY version')]
    finally:
        connection.close()


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.db = self.dir / 'history.sqlite3'

    def commit(self, version, records, name=None):
        path = self.dir / (name or 'report-%s.json' % version)
        write_json(path, report(records))
        return import_report(self.db, path, version)

    def export(self, version):
        return as_of(self.db, version)


class ImportExportTests(HistoryTestCase):
    def test_import_then_as_of_returns_original_ids_and_six_fields(self):
        records = [rec('B', unit='2', reg='R2'), rec('A', unit='1', reg='R1')]
        result = self.commit(1, records)
        self.assertEqual(result, {'version': 1, 'record_count': 2, 'idempotent': False,
                                  'opened': 2, 'closed': 0})
        exported = self.export(1)
        self.assertEqual(exported, {'version': 1, 'record_count': 2, 'records': [
            {'id': 'A', 'registry_id': 'R1', 'city': 't', 'postal': 'p',
             'street': 's', 'building': 'b', 'unit': '1'},
            {'id': 'B', 'registry_id': 'R2', 'city': 't', 'postal': 'p',
             'street': 's', 'building': 'b', 'unit': '2'},
        ]})
        self.assertNotIn('group_id', exported['records'][0])

    def test_only_normalized_records_is_consumed(self):
        # Other report members are irrelevant: tampering with groups, evidence
        # edges and a decision audit must not affect the snapshot or replay.
        records = [rec('B', reg='R1', unit='1'), rec('A', reg='R1', unit='1')]
        base = report(records)
        path1 = self.dir / 'base.json'
        write_json(path1, base)
        import_report(self.db, path1, 1)

        tampered = dict(base, groups=[], evidence_edges=[],
                        decision_audit={'same': [], 'different': [], 'candidate_edges': []})
        tampered['group_count'] = 0
        path2 = self.dir / 'tampered.json'
        write_json(path2, tampered)
        result = import_report(self.db, path2, 1)
        self.assertTrue(result['idempotent'])
        self.assertEqual([row['id'] for row in self.export(1)['records']], ['A', 'B'])
        self.assertEqual(len(intervals(self.db)), 2)

    def test_group_id_is_never_persisted_as_identity(self):
        # A and B link into one group whose group_id is A.  B must remain keyed by B.
        records = [rec('B', reg='R1', unit='1'), rec('A', reg='R1', unit='1')]
        linked = report(records)
        self.assertEqual(linked['groups'][0]['group_id'], 'A')
        path = self.dir / 'linked.json'
        write_json(path, linked)
        import_report(self.db, path, 1)
        self.assertEqual([row['id'] for row in self.export(1)['records']], ['A', 'B'])
        self.assertEqual([row[0] for row in intervals(self.db)], ['A', 'B'])

    def test_record_order_does_not_change_identity_or_storage(self):
        records = [rec('A', unit='1'), rec('B', unit='2'), rec('C', unit='3')]
        write_json(self.dir / 'a.json', report(records))
        write_json(self.dir / 'b.json', report(list(reversed(records))))
        import_report(self.db, self.dir / 'a.json', 1)
        import_report(self.db, self.dir / 'b.json', 2)
        self.assertEqual(len(intervals(self.db)), 3)
        self.assertEqual({k: v for k, v in self.export(1).items() if k != 'version'},
                         {k: v for k, v in self.export(2).items() if k != 'version'})

    def test_unchanged_snapshot_does_not_add_rows(self):
        records = [rec('A', unit='1'), rec('B', unit='2')]
        self.commit(1, records)
        before = intervals(self.db)
        second = self.commit(2, records)
        self.assertEqual(second['opened'], 0)
        self.assertEqual(second['closed'], 0)
        self.assertEqual(intervals(self.db), before)
        self.assertEqual(versions(self.db), [1, 2])
        self.assertEqual(self.export(1), {**self.export(2), 'version': 1})

    def test_only_normalized_address_and_raw_registry_decide_change(self):
        self.commit(1, [rec('A', reg='R1', street='Ｍaple  Road', unit='02')])
        # Raw formatting differs but the normalized five fields are identical:
        # no interval may be opened.
        result = self.commit(2, [rec('A', reg='R1', street='Maple Road', unit='02')])
        self.assertEqual((result['opened'], result['closed']), (0, 0))
        self.assertEqual(len(intervals(self.db)), 1)
        # A registry string is opaque and compared raw, so case is significant.
        result = self.commit(3, [rec('A', reg='r1', street='Maple Road', unit='02')])
        self.assertEqual((result['opened'], result['closed']), (1, 1))
        self.assertEqual(self.export(3)['records'][0]['registry_id'], 'r1')
        self.assertEqual(self.export(2)['records'][0]['registry_id'], 'R1')

    def test_consecutive_changes_use_half_open_intervals(self):
        self.commit(1, [rec('A', unit='1')])
        self.commit(2, [rec('A', unit='2')])
        self.commit(3, [rec('A', unit='3')])
        self.assertEqual(intervals(self.db), [
            ('A', 1, 2, None, 't', 'p', 's', 'b', '1'),
            ('A', 2, 3, None, 't', 'p', 's', 'b', '2'),
            ('A', 3, None, None, 't', 'p', 's', 'b', '3'),
        ])
        self.assertEqual([self.export(v)['records'][0]['unit'] for v in (1, 2, 3)],
                         ['1', '2', '3'])

    def test_disappearance_closes_and_reappearance_opens_new_interval(self):
        self.commit(1, [rec('A', unit='1'), rec('B', unit='2')])
        result = self.commit(2, [rec('A', unit='1')])
        self.assertEqual((result['opened'], result['closed']), (0, 1))
        self.assertEqual(self.export(2)['record_count'], 1)
        self.assertEqual(intervals(self.db), [
            ('A', 1, None, None, 't', 'p', 's', 'b', '1'),
            ('B', 1, 2, None, 't', 'p', 's', 'b', '2'),
        ])
        result = self.commit(3, [rec('A', unit='1'), rec('B', unit='9')])
        self.assertEqual((result['opened'], result['closed']), (1, 0))
        self.assertEqual(intervals(self.db)[-1], ('B', 3, None, None, 't', 'p', 's', 'b', '9'))
        self.assertEqual(self.export(2)['record_count'], 1)
        self.assertEqual(len(self.export(3)['records']), 2)
        self.assertEqual(self.export(2)['records'][0]['id'], 'A')

    def test_empty_batch_closes_everything_and_is_exportable(self):
        self.commit(1, [rec('A'), rec('B', unit='2')])
        result = self.commit(2, [])
        self.assertEqual((result['opened'], result['closed'], result['record_count']),
                         (0, 2, 0))
        self.assertEqual(self.export(2), {'version': 2, 'record_count': 0, 'records': []})
        self.assertEqual(intervals(self.db)[0][2], 2)
        # An empty version then a reappearance starts a fresh interval.
        result = self.commit(3, [rec('A')])
        self.assertEqual((result['opened'], result['closed']), (1, 0))
        self.assertEqual(self.export(3)['record_count'], 1)

    def test_versions_with_gaps_are_independent_snapshots(self):
        self.commit(2, [rec('A', unit='1')])
        self.commit(7, [rec('A', unit='2')])
        self.assertEqual(self.export(2)['records'][0]['unit'], '1')
        self.assertEqual(self.export(7)['records'][0]['unit'], '2')
        with self.assertRaisesRegex(ValueError, 'unknown version'):
            self.export(6)

    def test_cross_process_reopen_sees_committed_history(self):
        records = [rec('A', unit='1'), rec('B', unit='2')]
        report_path = self.dir / 'r1.json'
        write_json(report_path, report(records))
        database = self.dir / 'shared.sqlite3'
        out = self.dir / 'out.json'
        env = {'PYTHONPATH': str(ROOT / 'src')}
        def run_cli(*args):
            return subprocess.run([sys.executable, '-m', 'address_link.history_cli', *args],
                                  capture_output=True, text=True, env={**os.environ, **env})
        first = run_cli('import', '--database', str(database),
                        '--report', str(report_path), '--version', '1')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)['version'], 1)
        # A second process re-imports the same version and then exports it, proving
        # the committed database survives closing the first connection.
        replay = run_cli('import', '--database', str(database),
                         '--report', str(report_path), '--version', '1')
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertTrue(json.loads(replay.stdout)['idempotent'])
        second = run_cli('as-of', '--database', str(database),
                         '--version', '1', '--output', str(out))
        third = run_cli('as-of', '--database', str(database),
                        '--version', '1', '--output', str(out))
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(third.stdout, second.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(out.read_text()), as_of(database, 1))
        self.assertEqual([row['id'] for row in json.loads(out.read_text())['records']],
                         ['A', 'B'])


class IdempotencyAndVersionTests(HistoryTestCase):
    def test_same_version_same_projection_is_idempotent_case(self):
        records = [rec('A', unit='1'), rec('B', unit='2')]
        self.commit(1, records)
        before = intervals(self.db)
        shuffled = [rec('B', unit='2'), rec('A', unit='1')]
        result = self.commit(1, shuffled, name='same-version.json')
        self.assertEqual(result, {'version': 1, 'record_count': 2, 'idempotent': True,
                                  'opened': 0, 'closed': 0})
        self.assertEqual(intervals(self.db), before)

    def test_same_version_different_projection_is_rejected_without_changes(self):
        self.commit(1, [rec('A', unit='1')])
        before = intervals(self.db)
        with self.assertRaisesRegex(ValueError, 'different six-field projection'):
            self.commit(1, [rec('A', unit='2')])
        self.assertEqual(intervals(self.db), before)
        self.assertEqual(versions(self.db), [1])
        self.assertEqual(self.export(1)['records'][0]['unit'], '1')

    def test_same_version_extra_record_is_a_conflict(self):
        self.commit(1, [rec('A')])
        with self.assertRaisesRegex(ValueError, 'different six-field projection'):
            self.commit(1, [rec('A'), rec('B', unit='2')])

    def test_new_version_must_be_strictly_greater(self):
        self.commit(5, [rec('A', unit='1')])
        with self.assertRaisesRegex(ValueError, 'greater than latest'):
            self.commit(3, [rec('A', unit='2')])
        self.assertEqual(versions(self.db), [5])
        # A re-import of the current head with new content is still a conflict,
        # not a silent overwrite.
        with self.assertRaises(ValueError):
            self.commit(5, [rec('A', unit='9')])
        self.assertEqual(self.export(5)['records'][0]['unit'], '1')
        self.commit(6, [rec('A', unit='2')])
        self.assertEqual(versions(self.db), [5, 6])

    def test_version_validation_rejects_non_positive_and_non_integers(self):
        path = self.dir / 'r.json'
        write_json(path, report([rec('A')]))
        for bad in (0, -1, True, '1', 2**63):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'positive integer'):
                import_report(self.db, path, bad)
        self.assertFalse(self.db.exists())
        for bad in (0, True, 2**63):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'positive integer'):
                as_of(self.db, bad)


class ReportValidationTests(HistoryTestCase):
    def import_raw(self, value, version=1):
        path = self.dir / ('raw-%s.json' % version)
        write_json(path, value)
        return import_report(self.db, path, version)

    def test_diagnostic_report_is_rejected(self):
        from address_link.audit import audit
        diagnostic = audit({'records': [rec('A')]})
        with self.assertRaisesRegex(ValueError, 'audit report'):
            self.import_raw(diagnostic)
        with self.assertRaisesRegex(ValueError, 'normalized_records'):
            self.import_raw({'records': [rec('A')]})
        self.assertFalse(self.db.exists())

    def test_audit_markers_reject_even_with_normalized_records(self):
        data = report([rec('A')])
        data['rows'] = []
        with self.assertRaisesRegex(ValueError, 'audit report'):
            self.import_raw(data)

    def test_duplicate_ids_are_rejected(self):
        data = report([rec('A')])
        data['normalized_records'].append(dict(data['normalized_records'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate record id'):
            self.import_raw(data)

    def test_field_set_and_types_are_checked(self):
        good = report([rec('A')])['normalized_records'][0]
        cases = [
            (dict(good, extra=1), 'expected fields'),
            ({k: v for k, v in good.items() if k != 'unit'}, 'expected fields'),
            (dict(good, id=''), 'nonempty text'),
            (dict(good, registry_id=''), 'nonempty text'),
            (dict(good, normalized='bad'), 'normalized must contain'),
            (dict(good, normalized={**good['normalized'], 'unit': '9'}), 'unit does not match'),
            (dict(good, normalized={**good['normalized'], 'city': 'other'}), 'city does not match'),
        ]
        for record, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.import_raw({'normalized_records': [record]})

    def test_normalized_empty_and_inconsistent_unit_are_rejected(self):
        good = report([rec('A', unit=None)])['normalized_records'][0]
        with self.assertRaisesRegex(ValueError, 'unit does not match'):
            self.import_raw({'normalized_records': [dict(
                good, normalized={**good['normalized'], 'unit': '1'})]})
        data = {'normalized_records': [dict(good, city='!!!',
                normalized={**good['normalized'], 'city': ''})]}
        with self.assertRaisesRegex(ValueError, 'normalization produced empty'):
            self.import_raw(data)

    def test_empty_normalized_records_is_a_valid_empty_batch(self):
        result = self.import_raw({'normalized_records': []})
        self.assertEqual(result['record_count'], 0)
        self.assertEqual(self.export(1)['records'], [])

    def test_unknown_version_is_an_error_and_leaves_no_output(self):
        self.commit(1, [rec('A')])
        with self.assertRaisesRegex(ValueError, 'unknown version'):
            self.export(2)
        with self.assertRaisesRegex(ValueError, 'database does not exist'):
            as_of(self.dir / 'missing.sqlite3', 1)


class TransactionTests(HistoryTestCase):
    def test_failure_during_interval_write_rolls_back_the_whole_version(self):
        self.commit(1, [rec('A', unit='1'), rec('B', unit='2')])
        before = intervals(self.db)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TRIGGER boom BEFORE INSERT ON history_interval "
                "WHEN NEW.id='C' BEGIN SELECT RAISE(ABORT,'boom'); END")
            connection.commit()
        finally:
            connection.close()

        failing = self.dir / 'failing.json'
        write_json(failing, report([rec('A', unit='9'), rec('C', unit='3')]))
        with self.assertRaises(sqlite3.IntegrityError):
            import_report(self.db, failing, 2)

        # A is closed in this version and C is opened; both writes must be gone.
        self.assertEqual(intervals(self.db), before)
        self.assertEqual(versions(self.db), [1])
        self.assertEqual(self.export(1)['records'][0]['unit'], '1')

    def test_validation_failure_never_creates_the_database(self):
        bad = self.dir / 'bad.json'
        write_json(bad, {'normalized_records': [{'id': 'A'}]})
        with self.assertRaises(ValueError):
            import_report(self.db, bad, 1)
        self.assertFalse(self.db.exists())

    def test_version_registration_failure_rolls_back_intervals(self):
        # The trigger aborts on the final version insert, after interval rows
        # were already changed.  The whole version must disappear.
        self.commit(1, [rec('A', unit='1')])
        before = intervals(self.db)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TRIGGER block_version BEFORE INSERT ON history_version "
                "BEGIN SELECT RAISE(ABORT,'blocked'); END")
            connection.commit()
        finally:
            connection.close()
        failing = self.dir / 'failing2.json'
        write_json(failing, report([rec('A', unit='2')]))
        with self.assertRaises(sqlite3.IntegrityError):
            import_report(self.db, failing, 2)
        self.assertEqual(intervals(self.db), before)
        self.assertEqual(versions(self.db), [1])
        self.assertEqual(self.export(1)['records'][0]['unit'], '1')
        connection = sqlite3.connect(self.db)
        try:
            connection.execute('DROP TRIGGER block_version')
            connection.commit()
        finally:
            connection.close()
        self.commit(2, [rec('A', unit='2')])
        self.assertEqual(self.export(2)['records'][0]['unit'], '2')

    def test_failure_after_rollback_leaves_database_usable(self):
        self.commit(1, [rec('A')])
        with mock.patch.object(history, '_apply_projection',
                               side_effect=ValueError('forced failure')):
            with self.assertRaisesRegex(ValueError, 'forced failure'):
                self.commit(2, [rec('A', unit='2')])
        self.assertEqual(versions(self.db), [1])
        self.commit(2, [rec('A', unit='2')])
        self.assertEqual(self.export(2)['records'][0]['unit'], '2')

    def test_as_of_does_not_modify_the_database(self):
        self.commit(1, [rec('A')])
        before = self.db.read_bytes()
        self.export(1)
        with self.assertRaises(ValueError):
            self.export(99)
        self.assertEqual(self.db.read_bytes(), before)


class HistoryCliTests(HistoryTestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'address_link.history_cli', *args],
                              capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})

    def test_cli_import_and_as_of_match_library(self):
        source = self.dir / 'r1.json'
        write_json(source, report([rec('B', unit='2'), rec('A', unit='1')]))
        out = self.dir / 'out.json'
        imported = self.run_cli('import', '--database', str(self.db),
                                '--report', str(source), '--version', '1')
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)['record_count'], 2)
        exported = self.run_cli('as-of', '--database', str(self.db),
                                '--version', '1', '--output', str(out))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(json.loads(exported.stdout), json.loads(out.read_text()))
        self.assertEqual(json.loads(out.read_text()), as_of(self.db, 1))

    def test_cli_unknown_version_keeps_old_output(self):
        self.commit(1, [rec('A')])
        out = self.dir / 'out.json'
        out.write_text('old-export')
        result = self.run_cli('as-of', '--database', str(self.db),
                              '--version', '2', '--output', str(out))
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown version', result.stderr)
        self.assertEqual(out.read_text(), 'old-export')

    def test_cli_rejects_invalid_report_and_keeps_database_unchanged(self):
        from address_link.audit import audit
        diagnostic = self.dir / 'audit.json'
        write_json(diagnostic, audit({'records': [rec('A')]}))
        result = self.run_cli('import', '--database', str(self.db),
                              '--report', str(diagnostic), '--version', '1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('audit report', result.stderr)
        self.assertFalse(self.db.exists())

    def test_cli_import_conflict_keeps_committed_version(self):
        source = self.dir / 'r1.json'
        write_json(source, report([rec('A', unit='1')]))
        self.assertEqual(self.run_cli('import', '--database', str(self.db),
                                      '--report', str(source),
                                      '--version', '1').returncode, 0)
        conflicting = self.dir / 'r2.json'
        write_json(conflicting, report([rec('A', unit='2')]))
        result = self.run_cli('import', '--database', str(self.db),
                              '--report', str(conflicting), '--version', '1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('different six-field projection', result.stderr)
        self.assertEqual(self.export(1)['records'][0]['unit'], '1')

    def test_cli_requires_subcommand_and_rejects_database_as_report(self):
        self.assertEqual(self.run_cli().returncode, 2)
        source = self.dir / 'r.json'
        write_json(source, report([rec('A')]))
        database = self.dir / 'db.sqlite3'
        result = self.run_cli('import', '--database', str(database),
                              '--report', str(database), '--version', '1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('must differ', result.stderr)


if __name__ == '__main__':
    unittest.main()
