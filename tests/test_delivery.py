import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

from address_link import delivery
from address_link.delivery import ingest, summary
from address_link.history import import_report
from address_link.link import link

ROOT = Path(__file__).resolve().parents[1]


def rec(i, unit='1', reg=None, city='North Town', postal='A1',
        street='Maple Road', building='12'):
    return {'id': i, 'registry_id': reg, 'city': city, 'postal': postal,
            'street': street, 'building': building, 'unit': unit}


def write_history(database, snapshots):
    """Commit ``(version, records)`` pairs into an address-history database."""
    directory = Path(database).parent
    for version, records in snapshots:
        path = directory / ('history-%s.json' % version)
        path.write_text(json.dumps(link({'records': records}), ensure_ascii=False),
                        encoding='utf-8')
        import_report(database, path, version)
    return Path(database)


def event(event_id, source_id='A', version=1, cents=100):
    return {'event_id': event_id, 'source_id': source_id,
            'version': version, 'cents': cents}


def write_events(path, events):
    lines = [json.dumps(item, ensure_ascii=False) for item in events]
    Path(path).write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
    return Path(path)


def facts(database):
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            'SELECT event_id, source_id, version, cents, registry_id, city, '
            'postal, street, building, unit FROM delivery_fact '
            'ORDER BY event_id').fetchall()
    finally:
        connection.close()


class DeliveryTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        self.db = self.dir / 'facts.sqlite3'
        self.history = self.dir / 'history.sqlite3'

    def history_with_versions(self):
        # v1: A/B in North Town A1. v2: A moves to South Town A2 and changes
        # unit; B disappears. v3: B reappears and A is back to its v1 unit.
        return write_history(self.history, [
            (1, [rec('A', unit='1', reg='R1'),
                 rec('B', unit='2', reg='R2')]),
            (2, [rec('A', unit='9', reg='R1', city='South Town', postal='A2')]),
            (3, [rec('A', unit='1', reg='R1'),
                 rec('B', unit='7', reg='R2')]),
        ])

    def ingest_events(self, events, name='events.jsonl'):
        return ingest(self.db, write_events(self.dir / name, events), self.history)


class IngestTests(DeliveryTestCase):
    def test_events_freeze_the_six_fields_effective_at_their_version(self):
        self.history_with_versions()
        result = self.ingest_events([
            event('E1', 'A', version=1),
            event('E2', 'A', version=2),
            event('E3', 'B', version=1),
        ])
        self.assertEqual(result, {'event_count': 3, 'inserted': 3, 'idempotent': 0})
        rows = facts(self.db)
        self.assertEqual(rows[0][1:], ('A', 1, 100, 'R1', 'north town', 'a1',
                                       'maple road', '12', '1'))
        self.assertEqual(rows[1][1:], ('A', 2, 100, 'R1', 'south town', 'a2',
                                       'maple road', '12', '9'))
        self.assertEqual(rows[2][1:], ('B', 1, 100, 'R2', 'north town', 'a1',
                                       'maple road', '12', '2'))

    def test_refunds_keep_their_sign_and_sum_exactly(self):
        self.history_with_versions()
        self.ingest_events([
            event('charge', 'A', version=1, cents=500),
            event('refund', 'A', version=1, cents=-500),
            event('refund2', 'B', version=1, cents=-1),
        ])
        self.assertEqual([row[3] for row in facts(self.db)], [500, -500, -1])
        result = summary(self.db)
        self.assertEqual(result['event_count'], 3)
        self.assertEqual(result['cents'], -1)
        self.assertEqual(result['groups'], [
            {'city': 'north town', 'postal': 'a1', 'event_count': 3, 'cents': -1}])

    def test_summary_groups_and_sorts_by_city_then_postal(self):
        self.history_with_versions()
        self.ingest_events([
            event('E1', 'A', version=1, cents=3),
            event('E2', 'A', version=2, cents=4),
            event('E3', 'B', version=1, cents=5),
            event('E4', 'A', version=2, cents=-2),
        ])
        self.assertEqual(summary(self.db), {
            'event_count': 4, 'cents': 10, 'group_count': 2,
            'groups': [
                {'city': 'north town', 'postal': 'a1', 'event_count': 2, 'cents': 8},
                {'city': 'south town', 'postal': 'a2', 'event_count': 2, 'cents': 2},
            ]})

    def test_summary_uses_exact_integers_beyond_signed_64_bits(self):
        self.history_with_versions()
        maximum = 2 ** 63 - 1
        # Two maximum charges in one group overflow a signed 64-bit sum; the
        # refund sits in a different group so the batch total stays in range.
        self.ingest_events([
            event('E1', 'A', version=1, cents=maximum),
            event('E2', 'A', version=1, cents=maximum),
            event('E3', 'A', version=2, cents=-maximum),
        ])
        result = summary(self.db)
        self.assertEqual(result['cents'], maximum)
        self.assertEqual(result['groups'], [
            {'city': 'north town', 'postal': 'a1', 'event_count': 2,
             'cents': 2 * maximum},
            {'city': 'south town', 'postal': 'a2', 'event_count': 1,
             'cents': -maximum},
        ])
        self.assertGreater(2 * maximum, 2 ** 63 - 1)

    def test_each_event_is_attributed_independently_across_versions(self):
        # A at v1 and v2 has different city/postal; B is absent at v2 but
        # reappears at v3 with new fields.
        self.history_with_versions()
        self.ingest_events([
            event('E1', 'A', 1, 1),
            event('E2', 'A', 2, 2),
            event('E3', 'B', 3, 3),
        ])
        rows = {row[0]: row for row in facts(self.db)}
        self.assertEqual((rows['E1'][5], rows['E1'][6]), ('north town', 'a1'))
        self.assertEqual((rows['E2'][5], rows['E2'][6]), ('south town', 'a2'))
        self.assertEqual(rows['E3'][4:], ('R2', 'north town', 'a1',
                                          'maple road', '12', '7'))

    def test_history_database_is_never_modified(self):
        self.history_with_versions()
        before = self.history.read_bytes()
        self.ingest_events([event('E1', 'A', 1)])
        self.assertEqual(self.history.read_bytes(), before)

    def test_blank_lines_are_ignored(self):
        self.history_with_versions()
        path = self.dir / 'blanks.jsonl'
        path.write_text('\n%s\n\n' % json.dumps(event('E1')), encoding='utf-8')
        result = ingest(self.db, path, self.history)
        self.assertEqual(result['event_count'], 1)

    def test_empty_batch_is_valid(self):
        self.history_with_versions()
        result = self.ingest_events([])
        self.assertEqual(result, {'event_count': 0, 'inserted': 0, 'idempotent': 0})
        self.assertEqual(summary(self.db),
                         {'event_count': 0, 'cents': 0, 'group_count': 0, 'groups': []})


class ValidationTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.history_with_versions()

    def reject(self, events, message):
        path = write_events(self.dir / 'bad.jsonl', events)
        with self.assertRaisesRegex(ValueError, message):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())

    def test_unknown_version_rejects_the_whole_batch(self):
        self.reject([event('E1'), event('E2', version=99)],
                    'unknown history version 99')

    def test_source_id_absent_at_the_event_version_rejects_the_batch(self):
        # B exists at v1 and v3 but not at v2.
        self.reject([event('E1', 'B', version=2)], 'does not exist at version 2')

    def test_source_created_after_the_event_version_rejects_the_batch(self):
        # C first appears at v4, so version 3 has no interval for it.
        write_history(self.history, [(4, [rec('C', unit='3')])])
        with self.assertRaisesRegex(ValueError, 'does not exist at version 3'):
            ingest(self.db, write_events(self.dir / 'c.jsonl',
                                         [event('E1', 'C', version=3)]), self.history)

    def test_event_field_set_is_exact(self):
        self.reject([{'event_id': 'E1', 'source_id': 'A', 'version': 1}],
                    'expected fields')
        self.reject([dict(event('E1'), extra=1)], 'expected fields')
        self.reject([{'event_id': 'E1', 'source_id': 'A',
                      'version': 1, 'cents': 1.5}], 'signed 64-bit')

    def test_ids_follow_the_original_text_rules(self):
        for bad in ('', '   ', 'x' * 257, 'bad\x01id'):
            with self.subTest(bad=bad):
                self.reject([event(bad)], 'nonempty text')

    def test_version_must_be_a_positive_integer_without_booleans(self):
        for bad in (0, -1, True, '1'):
            with self.subTest(bad=bad):
                self.reject([event('E1', version=bad)], 'positive integer')

    def test_cents_is_a_signed_64_bit_integer_without_booleans(self):
        for bad in (True, False, '100', 2 ** 63, -(2 ** 63) - 1):
            with self.subTest(bad=bad):
                self.reject([event('E1', cents=bad)], 'signed 64-bit')

    def test_malformed_json_line_is_reported_with_its_number(self):
        path = self.dir / 'malformed.jsonl'
        path.write_text('{"event_id":"E1","source_id":"A","version":1,"cents":1}\n'
                        '{"event_id":\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'line 2'):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())

    def test_missing_history_database_is_rejected(self):
        missing = self.dir / 'missing.sqlite3'
        with self.assertRaisesRegex(ValueError, 'history database does not exist'):
            ingest(self.db, write_events(self.dir / 'e.jsonl', [event('E1')]), missing)
        self.assertFalse(self.db.exists())

    def test_database_must_differ_from_history(self):
        path = write_events(self.dir / 'e.jsonl', [event('E1')])
        with self.assertRaisesRegex(ValueError, 'must differ'):
            ingest(self.history, path, self.history)
        with self.assertRaisesRegex(ValueError, 'must differ'):
            ingest(self.db, self.history, self.history)

    def test_overlapping_history_intervals_are_rejected(self):
        # A corrupt dimension with two intervals covering the event version
        # cannot attribute the event unambiguously.
        connection = sqlite3.connect(self.history)
        try:
            connection.execute(
                'INSERT INTO history_interval(id, version_from, version_to, city, '
                'postal, street, building, unit) VALUES(?,?,?,?,?,?,?,?)',
                ('A', 4, 6, 'north town', 'a1', 'maple road', '12', '1'))
        finally:
            connection.commit()
            connection.close()
        path = write_events(self.dir / 'e.jsonl', [event('E1')])
        with self.assertRaisesRegex(ValueError, 'overlapping intervals'):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())


class IdempotencyTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.history_with_versions()

    def test_same_content_replay_is_idempotent_across_batches(self):
        first = self.ingest_events([event('E1', cents=5), event('E2', cents=7)], 'a.jsonl')
        self.assertEqual(first['inserted'], 2)
        before = facts(self.db)
        second = self.ingest_events(
            [event('E2', cents=7), event('E1', cents=5)], 'b.jsonl')
        self.assertEqual(second, {'event_count': 2, 'inserted': 0, 'idempotent': 2})
        self.assertEqual(facts(self.db), before)

    def test_out_of_order_replay_adds_only_new_events(self):
        self.ingest_events([event('E1'), event('E2')], 'a.jsonl')
        result = self.ingest_events(
            [event('E3'), event('E2'), event('E1')], 'b.jsonl')
        self.assertEqual(result, {'event_count': 3, 'inserted': 1, 'idempotent': 2})
        self.assertEqual([row[0] for row in facts(self.db)], ['E1', 'E2', 'E3'])

    def test_intra_batch_duplicates_with_same_content_are_idempotent(self):
        result = self.ingest_events([event('E1'), event('E1')])
        self.assertEqual(result, {'event_count': 1, 'inserted': 1, 'idempotent': 0})
        self.assertEqual(len(facts(self.db)), 1)

    def test_intra_batch_conflict_rejects_before_touching_the_database(self):
        path = write_events(self.dir / 'conflict.jsonl',
                            [event('E1', cents=1), event('E1', cents=2)])
        with self.assertRaisesRegex(ValueError, 'different content'):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())

    def test_cross_batch_conflict_rolls_back_and_keeps_committed_facts(self):
        self.ingest_events([event('E1', cents=1), event('E2', cents=2)], 'a.jsonl')
        before = facts(self.db)
        path = write_events(self.dir / 'b.jsonl',
                            [event('E1', cents=1), event('E3', cents=3),
                             event('E2', cents=99)])
        with self.assertRaisesRegex(ValueError, 'different content'):
            ingest(self.db, path, self.history)
        self.assertEqual(facts(self.db), before)
        self.assertEqual(summary(self.db)['cents'], 3)

    def test_replay_after_history_evolves_stays_idempotent(self):
        # Committed intervals are immutable, so a later history version must not
        # change how an earlier event is attributed.
        self.history_with_versions()
        self.ingest_events([event('E1', 'A', version=1, cents=5)], 'a.jsonl')
        before = facts(self.db)
        write_history(self.history, [(4, [rec('A', unit='4', city='East Town',
                                             postal='A4')])])
        replay = self.ingest_events([event('E1', 'A', version=1, cents=5)], 'b.jsonl')
        self.assertEqual(replay, {'event_count': 1, 'inserted': 0, 'idempotent': 1})
        self.assertEqual(facts(self.db), before)

    def test_conflict_on_source_or_version_is_detected_too(self):
        self.ingest_events([event('E1', 'A', version=1, cents=1)], 'a.jsonl')
        for changed in (event('E1', 'B', version=1, cents=1),
                        event('E1', 'A', version=2, cents=1)):
            with self.subTest(changed=changed):
                path = write_events(self.dir / 'b.jsonl', [changed])
                with self.assertRaisesRegex(ValueError, 'different content'):
                    ingest(self.db, path, self.history)
        self.assertEqual(facts(self.db), [('E1', 'A', 1, 1, 'R1', 'north town',
                                          'a1', 'maple road', '12', '1')])


class TransactionTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.history_with_versions()

    def test_failure_mid_insert_rolls_back_every_fact_of_the_batch(self):
        self.ingest_events([event('E1', cents=1)], 'a.jsonl')
        before = facts(self.db)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TRIGGER boom BEFORE INSERT ON delivery_fact "
                "WHEN NEW.event_id='E3' BEGIN SELECT RAISE(ABORT,'boom'); END")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.ingest_events([event('E2', cents=2), event('E3', cents=3)], 'b.jsonl')
        self.assertEqual(facts(self.db), before)
        self.assertEqual(summary(self.db)['event_count'], 1)

        connection = sqlite3.connect(self.db)
        try:
            connection.execute('DROP TRIGGER boom')
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.ingest_events(
            [event('E2', cents=2), event('E3', cents=3)], 'c.jsonl')['inserted'], 2)

    def test_transaction_is_all_or_nothing(self):
        # The second batch has a conflict with itself: nothing at all may land.
        path = write_events(self.dir / 'mixed.jsonl',
                            [event('E1', cents=1), event('E2', cents=2),
                             event('E1', cents=5)])
        with self.assertRaises(ValueError):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())

    def test_summary_on_missing_or_uninitialised_database(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            summary(self.dir / 'missing.sqlite3')
        empty = self.dir / 'empty.sqlite3'
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(ValueError, 'fact table does not exist'):
            summary(empty)


class ScaleTests(DeliveryTestCase):
    def test_more_than_one_hundred_thousand_events_is_rejected(self):
        self.history_with_versions()
        events = [event('E%d' % index) for index in range(100001)]
        path = write_events(self.dir / 'too-many.jsonl', events)
        with self.assertRaisesRegex(ValueError, 'exceed 100000'):
            ingest(self.db, path, self.history)
        self.assertFalse(self.db.exists())

    def test_one_hundred_thousand_events_across_versions(self):
        write_history(self.history, [
            (1, [rec('A', unit='1')]),
            (2, [rec('A', unit='2', city='South Town', postal='A2')]),
        ])
        events = [event('E%06d' % index, 'A', 1 + index % 2, index % 7)
                  for index in range(100000)]
        result = self.ingest_events(events, 'many.jsonl')
        self.assertEqual(result['event_count'], 100000)
        self.assertEqual(result['inserted'], 100000)
        total = sum(item['cents'] for item in events)
        report = summary(self.db)
        self.assertEqual(report['event_count'], 100000)
        self.assertEqual(report['cents'], total)
        self.assertEqual(sum(group['event_count'] for group in report['groups']), 100000)


class DeliveryCliTests(DeliveryTestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'address_link.delivery_cli', *args],
                              capture_output=True, text=True,
                              env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})

    def test_cross_process_ingest_replay_and_summary(self):
        self.history_with_versions()
        events = self.dir / 'events.jsonl'
        write_events(events, [event('E1', 'A', 1, 100), event('E2', 'A', 2, -30)])

        first = self.run_cli('ingest', '--database', str(self.db),
                             '--events', str(events), '--history', str(self.history))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout),
                         {'event_count': 2, 'inserted': 2, 'idempotent': 0})

        # A separate process replays the batch and must report idempotency.
        replay = self.run_cli('ingest', '--database', str(self.db),
                              '--events', str(events), '--history', str(self.history))
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout),
                         {'event_count': 2, 'inserted': 0, 'idempotent': 2})

        out = self.dir / 'summary.json'
        third = self.run_cli('summary', '--database', str(self.db),
                             '--output', str(out))
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(json.loads(out.read_text()), summary(self.db))
        self.assertEqual(json.loads(third.stdout), json.loads(out.read_text()))
        self.assertEqual(json.loads(out.read_text())['cents'], 70)

    def test_cli_conflict_keeps_committed_state_and_old_summary(self):
        self.history_with_versions()
        write_events(self.dir / 'a.jsonl', [event('E1', cents=1)])
        self.assertEqual(self.run_cli('ingest', '--database', str(self.db),
                                      '--events', str(self.dir / 'a.jsonl'),
                                      '--history', str(self.history)).returncode, 0)
        write_events(self.dir / 'b.jsonl', [event('E1', cents=2)])
        failed = self.run_cli('ingest', '--database', str(self.db),
                              '--events', str(self.dir / 'b.jsonl'),
                              '--history', str(self.history))
        self.assertEqual(failed.returncode, 2)
        self.assertIn('different content', failed.stderr)
        self.assertEqual(summary(self.db)['cents'], 1)

        out = self.dir / 's.json'
        out.write_text('old-summary')
        missing = self.run_cli('summary', '--database', str(self.dir / 'none.sqlite3'),
                               '--output', str(out))
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(out.read_text(), 'old-summary')

    def test_cli_rejects_unknown_version_without_creating_database(self):
        self.history_with_versions()
        write_events(self.dir / 'e.jsonl', [event('E1', version=99)])
        result = self.run_cli('ingest', '--database', str(self.db),
                              '--events', str(self.dir / 'e.jsonl'),
                              '--history', str(self.history))
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown history version', result.stderr)
        self.assertFalse(self.db.exists())

    def test_cli_requires_subcommand(self):
        self.assertEqual(self.run_cli().returncode, 2)

    def test_cli_missing_events_file_exits_two_without_database(self):
        self.history_with_versions()
        result = self.run_cli('ingest', '--database', str(self.db),
                              '--events', str(self.dir / 'missing.jsonl'),
                              '--history', str(self.history))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
