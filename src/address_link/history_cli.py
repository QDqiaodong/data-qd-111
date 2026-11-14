"""``address-history`` CLI: import snapshots, export an as-of snapshot."""
import argparse, json, sqlite3, sys
from pathlib import Path

from .history import as_of, import_report
from .io import publish

DESCRIPTION = 'SQLite history dimension for offline address snapshots'

def _parser():
    p = argparse.ArgumentParser(description=DESCRIPTION)
    sub = p.add_subparsers(dest='command', required=True)

    imp = sub.add_parser('import', help='commit one normal linkage report as a version')
    imp.add_argument('--database', type=Path, required=True)
    imp.add_argument('--report', type=Path, required=True,
                     help='normal linkage report containing normalized_records')
    imp.add_argument('--version', type=int, required=True)

    out = sub.add_parser('as-of', help='export the snapshot committed as a version')
    out.add_argument('--database', type=Path, required=True)
    out.add_argument('--version', type=int, required=True)
    out.add_argument('--output', type=Path, required=True)
    return p

def _emit(result):
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))

def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == 'import':
            if args.report.resolve() == args.database.resolve():
                raise ValueError('report and database must differ')
            result = import_report(args.database, args.report, args.version)
            _emit(result)
            return 0
        if args.output.resolve() == args.database.resolve():
            raise ValueError('output and database must differ')
        result = as_of(args.database, args.version)
        publish(args.output, result)
        _emit(result)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError,
            RecursionError, sqlite3.Error) as e:
        print(str(e), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
