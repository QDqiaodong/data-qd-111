"""SQLite history dimension for offline address snapshots.

An import consumes only the ``normalized_records`` array of a normal linkage
report.  The original source ID is the durable key; ``group_id`` is never
persisted.  A record changes only when one of the five normalized address
fields (city, postal, street, building, unit) or the raw ``registry_id``
changes.  Intervals are half-open: ``version_from <= version < version_to``.
Open intervals store NULL in ``version_to``.

Version registration and every interval change happen in one SQLite
transaction.  A failed import therefore leaves neither a version row nor a
partially modified interval behind.
"""
import json, sqlite3
from pathlib import Path

from .io import array, fields, load, text
from .link import normalize

SIX_FIELDS = ('registry_id', 'city', 'postal', 'street', 'building', 'unit')
NORMALIZED_FIELDS = ('city', 'postal', 'street', 'building', 'unit')
REPORT_FIELDS = 'id registry_id city postal street building unit normalized'
VERSION_MAX = 2**63-1

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS history_version (
           version INTEGER PRIMARY KEY CHECK (version > 0),
           projection TEXT NOT NULL
       )''',
    '''CREATE TABLE IF NOT EXISTS history_interval (
           id TEXT NOT NULL,
           version_from INTEGER NOT NULL CHECK (version_from > 0),
           version_to INTEGER,
           registry_id TEXT,
           city TEXT NOT NULL,
           postal TEXT NOT NULL,
           street TEXT NOT NULL,
           building TEXT NOT NULL,
           unit TEXT,
           PRIMARY KEY (id, version_from),
           CHECK (version_to IS NULL OR version_to > version_from)
       )''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS history_interval_open
           ON history_interval(id) WHERE version_to IS NULL''',
    '''CREATE INDEX IF NOT EXISTS history_interval_asof
           ON history_interval(version_from, version_to)''',
)


def _version(value):
    """Validate a positive integer version without accepting bools."""
    if type(value) is not int or not 1 <= value <= VERSION_MAX:
        raise ValueError('version must be a positive integer')
    return value


def _record_projection(record):
    """Validate one normalized source record and return id plus six fields."""
    fields(record, REPORT_FIELDS)
    ident = text(record['id'])
    registry = record['registry_id']
    if registry is not None:
        text(registry)

    raw_address = {name: record[name] for name in ('city', 'postal', 'street', 'building')}
    expected = {name: normalize(raw_address[name]) for name in raw_address}
    if not all(expected.values()):
        raise ValueError('normalization produced empty identity field')

    raw_unit = record['unit']
    if raw_unit is None:
        expected_unit = None
    else:
        expected_unit = normalize(raw_unit)
        if expected_unit == '':
            raise ValueError('empty normalized unit')

    actual = record['normalized']
    if not isinstance(actual, dict) or set(actual) != set(NORMALIZED_FIELDS):
        raise ValueError('normalized must contain exactly: ' + ' '.join(NORMALIZED_FIELDS))
    for name in ('city', 'postal', 'street', 'building'):
        if not isinstance(actual[name], str) or actual[name] != expected[name]:
            raise ValueError('normalized %s does not match its source value' % name)
    unit = actual['unit']
    if expected_unit is None:
        if unit is not None:
            raise ValueError('normalized unit does not match its source value')
    elif not isinstance(unit, str) or unit != expected_unit:
        raise ValueError('normalized unit does not match its source value')

    return ident, (registry, expected['city'], expected['postal'],
                   expected['street'], expected['building'], expected_unit)


def _projection(data):
    """Return ``{source_id: six-tuple}`` from a normal linkage report only.

    Diagnostic (``--audit-only``) reports do not carry ``normalized_records``
    and are rejected explicitly.  Other report members such as groups and
    evidence are deliberately ignored.
    """
    if not isinstance(data, dict):
        raise ValueError('report must be a JSON object containing normalized_records')
    if any(key in data for key in ('valid_count', 'problem_count', 'rule_counts', 'rows')):
        raise ValueError('audit report is not a normal linkage report')
    if 'normalized_records' not in data:
        raise ValueError('report is missing normalized_records')
    result = {}
    for record in array(data['normalized_records']):
        ident, six = _record_projection(record)
        if ident in result:
            raise ValueError('duplicate record id: ' + ident)
        result[ident] = six
    return result


def _canonical(projection):
    rows = [[ident, *projection[ident]] for ident in sorted(projection)]
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _connect(path, read_only=False):
    if read_only:
        uri = Path(path).resolve().as_uri() + '?mode=ro'
        return sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    return sqlite3.connect(str(path), isolation_level=None, timeout=30)


def _ensure_schema(connection):
    for statement in _SCHEMA:
        connection.execute(statement)


def _open_intervals(connection):
    rows = connection.execute(
        'SELECT id, registry_id, city, postal, street, building, unit '
        'FROM history_interval WHERE version_to IS NULL').fetchall()
    current = {}
    for row in rows:
        if row[0] in current:
            raise ValueError('database contains multiple open intervals for id: ' + row[0])
        current[row[0]] = tuple(row[1:])
    return current


def _apply_projection(connection, version, projection):
    """Close and open intervals for one snapshot; caller owns the transaction."""
    current = _open_intervals(connection)
    closes = []
    opens = []
    for ident in sorted(projection):
        old = current.get(ident)
        if old is None:
            opens.append((ident, version, *projection[ident]))
        elif old != projection[ident]:
            closes.append((version, ident))
            opens.append((ident, version, *projection[ident]))
    for ident in sorted(current):
        if ident not in projection:
            closes.append((version, ident))
    connection.executemany(
        'UPDATE history_interval SET version_to=? WHERE id=? AND version_to IS NULL',
        closes)
    connection.executemany(
        'INSERT INTO history_interval '
        '(id, version_from, registry_id, city, postal, street, building, unit) '
        'VALUES (?,?,?,?,?,?,?,?)', opens)
    return len(opens), len(closes)


def import_report(database, report_path, version):
    """Import one normal linkage report as ``version`` into ``database``.

    Validation happens before the database is touched.  For a new version,
    all interval changes and the version registration are committed in one
    transaction.  Re-importing an existing version is idempotent when its
    six-field projection is byte-for-byte the same after canonical sorting,
    and is rejected otherwise.  Record order inside the report is irrelevant.
    """
    version = _version(version)
    data = load(report_path)
    projection = _projection(data)
    canonical = _canonical(projection)

    database = Path(database)
    connection = _connect(database)
    try:
        connection.execute('BEGIN IMMEDIATE')
        try:
            _ensure_schema(connection)
            existing = connection.execute(
                'SELECT projection FROM history_version WHERE version=?',
                (version,)).fetchone()
            if existing is not None:
                if existing[0] != canonical:
                    raise ValueError(
                        'version %d already has a different six-field projection' % version)
                connection.execute('COMMIT')
                return {'version': version, 'record_count': len(projection),
                        'idempotent': True, 'opened': 0, 'closed': 0}
            latest = connection.execute(
                'SELECT MAX(version) FROM history_version').fetchone()[0]
            if latest is not None and version < latest:
                raise ValueError(
                    'new version must be greater than latest committed version %d' % latest)
            opened, closed = _apply_projection(connection, version, projection)
            connection.execute(
                'INSERT INTO history_version(version, projection) VALUES(?,?)',
                (version, canonical))
            connection.execute('COMMIT')
            return {'version': version, 'record_count': len(projection),
                    'idempotent': False, 'opened': opened, 'closed': closed}
        except BaseException:
            try:
                connection.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise
    finally:
        connection.close()


def as_of(database, version):
    """Export the snapshot committed as ``version``.

    Unknown versions raise ``ValueError``; no output file is involved here, so
    a CLI caller can preserve an existing report by not publishing on failure.
    Records are sorted by the original source ID and contain only ``id`` plus
    the six history fields.
    """
    version = _version(version)
    database = Path(database)
    if not database.is_file():
        raise ValueError('database does not exist')
    connection = _connect(database, read_only=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_version'"
        ).fetchone()
        if table is None:
            raise ValueError('unknown version: %d' % version)
        known = connection.execute(
            'SELECT 1 FROM history_version WHERE version=?', (version,)).fetchone()
        if known is None:
            raise ValueError('unknown version: %d' % version)
        rows = connection.execute(
            'SELECT id, registry_id, city, postal, street, building, unit '
            'FROM history_interval '
            'WHERE version_from <= ? AND (version_to IS NULL OR version_to > ?) '
            'ORDER BY id', (version, version)).fetchall()
    finally:
        connection.close()

    if len({row[0] for row in rows}) != len(rows):
        raise ValueError('database contains overlapping history intervals')
    records = [dict(zip(('id',) + SIX_FIELDS, row)) for row in rows]
    records.sort(key=lambda record: record['id'])
    return {'version': version, 'record_count': len(records), 'records': records}
