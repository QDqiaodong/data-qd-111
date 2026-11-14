"""SQLite delivery event facts attributed to an address-history dimension.

An ingest consumes a JSONL stream of settlement events and an existing
``address-history`` database.  Each event is ``event_id``, ``source_id``,
``version`` and a signed 64-bit ``cents`` amount.  The six history fields
(``registry_id``, ``city``, ``postal``, ``street``, ``building``, ``unit``) that
were effective for ``source_id`` at the event's ``version`` are frozen onto the
fact row.  The history database is only ever read: it is opened read-only and a
single read transaction yields one consistent snapshot for the whole batch.

``event_id`` is globally unique.  Re-ingesting identical content is idempotent;
the same ID with different content is a conflict that rejects and rolls back
the whole batch, whether the duplicate is inside the batch or was committed by
an earlier one.  Every fact of one batch is written in a single SQLite
transaction, so a failure leaves neither a partial batch nor a changed fact
behind.

Attribution never scans every history interval for every event: the distinct
source IDs of a batch are fetched once through the ``(id, version_from)``
primary key and each event is resolved with a binary search over that ID's
intervals.  Amounts are summed with Python integers, so summaries are exact
even when the total exceeds 64 bits.
"""
import sqlite3
from bisect import bisect_right
from pathlib import Path

from .history import VERSION_MAX, SIX_FIELDS
from .io import fields, loads, text

EVENT_FIELDS = 'event_id source_id version cents'
MAX_EVENTS = 100000
CENTS_MIN = -(2 ** 63)
CENTS_MAX = 2 ** 63 - 1
_CHUNK = 500

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS delivery_fact (
           event_id TEXT PRIMARY KEY,
           source_id TEXT NOT NULL,
           version INTEGER NOT NULL CHECK (version > 0),
           cents INTEGER NOT NULL,
           registry_id TEXT,
           city TEXT NOT NULL,
           postal TEXT NOT NULL,
           street TEXT NOT NULL,
           building TEXT NOT NULL,
           unit TEXT
       )''',
)

_FACT_COLUMNS = 'event_id, source_id, version, cents, ' + ', '.join(SIX_FIELDS)


def _version(value):
    """Validate a positive integer version without accepting bools."""
    if type(value) is not int or not 1 <= value <= VERSION_MAX:
        raise ValueError('version must be a positive integer')
    return value


def _cents(value):
    """Validate a signed 64-bit integer amount without accepting bools."""
    if type(value) is not int or not CENTS_MIN <= value <= CENTS_MAX:
        raise ValueError('cents must be a signed 64-bit integer')
    return value


def _read_events(path):
    """Parse one JSONL event file with the shared JSON contract.

    Blank lines (including a trailing newline) are ignored; every other line
    must be exactly one event object.  Errors carry the 1-based line number.
    """
    raw = Path(path).read_bytes()
    events = []
    for number, line in enumerate(raw.split(b'\n'), 1):
        if not line.strip():
            continue
        if len(events) >= MAX_EVENTS:
            raise ValueError('events exceed %d entries' % MAX_EVENTS)
        try:
            value = loads(line)
        except ValueError as e:
            raise ValueError('line %d: %s' % (number, e)) from None
        try:
            fields(value, EVENT_FIELDS)
            event = {'event_id': text(value['event_id']),
                     'source_id': text(value['source_id']),
                     'version': _version(value['version']),
                     'cents': _cents(value['cents'])}
        except ValueError as e:
            raise ValueError('line %d: %s' % (number, e)) from None
        events.append(event)
    return events


def _deduplicate(events):
    """Collapse repeated IDs; differing content for one ID is a conflict."""
    unique = {}
    for event in events:
        ident = event['event_id']
        content = (event['source_id'], event['version'], event['cents'])
        previous = unique.get(ident)
        if previous is None:
            unique[ident] = event
        elif (previous['source_id'], previous['version'], previous['cents']) != content:
            raise ValueError(
                'event_id %s appears multiple times with different content' % ident)
    return unique


def _read_history(database, source_ids):
    """Read one consistent history snapshot for the batch.

    Returns the registered version set and, for each requested source ID, a
    ``(starts, ends, tuples)`` triple suitable for binary-search attribution.
    The connection is read-only; a single read transaction makes every query
    of this batch observe the same committed snapshot.
    """
    path = Path(database)
    if not path.is_file():
        raise ValueError('history database does not exist')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro',
                                 uri=True, isolation_level=None, timeout=30)
    try:
        connection.execute('BEGIN')
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ('history_version', 'history_interval'):
            if name not in tables:
                raise ValueError('history database is missing table ' + name)
        versions = {row[0] for row in connection.execute(
            'SELECT version FROM history_version')}

        rows = []
        for start in range(0, len(source_ids), _CHUNK):
            chunk = source_ids[start:start + _CHUNK]
            marks = ','.join('?' * len(chunk))
            rows.extend(connection.execute(
                'SELECT id, version_from, version_to, ' + ', '.join(SIX_FIELDS) +
                ' FROM history_interval WHERE id IN (%s)' % marks, chunk))
        connection.execute('COMMIT')
    finally:
        connection.close()

    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row[1:])
    spans = {}
    for ident in grouped:
        ordered = sorted(grouped[ident], key=lambda item: item[0])
        previous_end = None
        for index, item in enumerate(ordered):
            if index and (previous_end is None or item[0] < previous_end):
                raise ValueError('history database contains overlapping intervals '
                                 'for id: ' + ident)
            previous_end = item[1]
        spans[ident] = ([item[0] for item in ordered],
                        [item[1] for item in ordered],
                        [tuple(item[2:]) for item in ordered])
    return versions, spans


def _effective(spans, version):
    """Six fields effective at ``version`` for one ID, or ``None``."""
    if spans is None:
        return None
    starts, ends, values = spans
    index = bisect_right(starts, version) - 1
    if index < 0:
        return None
    end = ends[index]
    if end is not None and end <= version:
        return None
    return values[index]


def _attribute(events, history_database):
    """Freeze six history fields per event; reject unknown version or ID."""
    source_ids = sorted({event['source_id'] for event in events.values()})
    versions, spans = _read_history(history_database, source_ids)
    facts = {}
    for ident in sorted(events):
        event = events[ident]
        version = event['version']
        if version not in versions:
            raise ValueError('event %s references unknown history version %d'
                             % (ident, version))
        six = _effective(spans.get(event['source_id']), version)
        if six is None:
            raise ValueError('source id %s does not exist at version %d (event %s)'
                             % (event['source_id'], version, ident))
        facts[ident] = (event['source_id'], version, event['cents']) + six
    return facts


def _connect(database):
    return sqlite3.connect(str(database), isolation_level=None, timeout=30)


def _ensure_schema(connection):
    for statement in _SCHEMA:
        connection.execute(statement)


def _existing(connection, identifiers):
    found = {}
    for start in range(0, len(identifiers), _CHUNK):
        chunk = identifiers[start:start + _CHUNK]
        marks = ','.join('?' * len(chunk))
        for row in connection.execute(
                'SELECT ' + _FACT_COLUMNS + ' FROM delivery_fact '
                'WHERE event_id IN (%s)' % marks, chunk):
            found[row[0]] = tuple(row[1:])
    return found


def ingest(database, events_path, history_database):
    """Ingest one JSONL batch of delivery events into ``database``.

    Event validation and history attribution happen before the fact database
    is opened, so a bad batch never creates or changes it.  Existing IDs with
    identical frozen content are skipped, differing content rejects the batch,
    and all remaining inserts share one transaction.
    """
    database = Path(database)
    events_path = Path(events_path)
    history_database = Path(history_database)
    if database.resolve() == history_database.resolve():
        raise ValueError('database and history must differ')
    if events_path.resolve() == database.resolve():
        raise ValueError('events and database must differ')
    if events_path.resolve() == history_database.resolve():
        raise ValueError('events and history must differ')

    events = _read_events(events_path)
    unique = _deduplicate(events)
    facts = _attribute(unique, history_database)

    identifiers = sorted(facts)
    connection = _connect(database)
    try:
        connection.execute('BEGIN IMMEDIATE')
        try:
            _ensure_schema(connection)
            found = _existing(connection, identifiers)
            inserts = []
            idempotent = 0
            for ident in identifiers:
                previous = found.get(ident)
                if previous is None:
                    inserts.append((ident,) + facts[ident])
                elif previous != facts[ident]:
                    raise ValueError(
                        'event_id %s is already recorded with different content' % ident)
                else:
                    idempotent += 1
            connection.executemany(
                'INSERT INTO delivery_fact(' + _FACT_COLUMNS + ') '
                'VALUES (' + ','.join('?' * (4 + len(SIX_FIELDS))) + ')', inserts)
            connection.execute('COMMIT')
        except BaseException:
            try:
                connection.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise
    finally:
        connection.close()
    return {'event_count': len(identifiers), 'inserted': len(inserts),
            'idempotent': idempotent}


def summary(database):
    """Group committed facts by frozen city and postal, sorted.

    Amounts are summed as Python integers after reading the rows, so the result
    is exact regardless of whether it fits in a signed 64-bit SQLite integer.
    """
    path = Path(database)
    if not path.is_file():
        raise ValueError('database does not exist')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro',
                                 uri=True, isolation_level=None, timeout=30)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'delivery_fact' not in tables:
            raise ValueError('delivery fact table does not exist')
        rows = connection.execute(
            'SELECT city, postal, cents FROM delivery_fact').fetchall()
    finally:
        connection.close()

    groups = {}
    total = 0
    for city, postal, cents in rows:
        counts = groups.get((city, postal))
        if counts is None:
            groups[(city, postal)] = [1, cents]
        else:
            counts[0] += 1
            counts[1] += cents
        total += cents
    records = [{'city': city, 'postal': postal,
                'event_count': counts[0], 'cents': counts[1]}
               for (city, postal), counts in sorted(groups.items())]
    return {'event_count': len(rows), 'cents': total,
            'group_count': len(records), 'groups': records}
