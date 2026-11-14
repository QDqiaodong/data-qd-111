"""``delivery-facts`` CLI: ingest JSONL events, summarise the fact database."""
import argparse, json, sqlite3, sys
from pathlib import Path

from .delivery import ingest, summary
from .io import publish

DESCRIPTION = 'Event fact pipeline for delivery settlement amounts'

def _parser():
    p = argparse.ArgumentParser(description=DESCRIPTION)
    sub = p.add_subparsers(dest='command', required=True)

    load = sub.add_parser('ingest', help='commit one JSONL batch of events as facts')
    load.add_argument('--database', type=Path, required=True)
    load.add_argument('--events', type=Path, required=True,
                      help='JSONL file with event_id, source_id, version and cents')
    load.add_argument('--history', type=Path, required=True,
                      help='existing address-history SQLite database (read only)')

    total = sub.add_parser('summary', help='sum events and cents by city and postal')
    total.add_argument('--database', type=Path, required=True)
    total.add_argument('--output', type=Path, required=True)
    return p

def _emit(result):
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))

def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == 'ingest':
            result = ingest(args.database, args.events, args.history)
            _emit(result)
            return 0
        if args.output.resolve() == args.database.resolve():
            raise ValueError('output and database must differ')
        result = summary(args.database)
        publish(args.output, result)
        _emit(result)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError,
            RecursionError, sqlite3.Error) as e:
        print(str(e), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
