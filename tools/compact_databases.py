"""Remove unused shared text and compact every database of a decompdecomp home, with the app closed.

    uv run python tools/compact_databases.py [DECOMP_HOME] [--dry-run] [--no-collect]

The app's own Settings tasks do the same while it runs, but SQLite VACUUM holds each file's writer, so on a home
with multi-gigabyte project databases it is easier to run this once with the app closed. It refuses to start while
an app holds the home's server-instance lock. Each file is collected (payload_links.collect: the same checks as the
Settings task, nothing still referenced is removed) and then compacted, one file at a time, and only when the drive
has twice that file's size free. Stopping (Ctrl+C) keeps the files already finished; an interrupted VACUUM rolls back.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from decompnet.writer_gate import WriterGate


class Log:
    def __call__(self, line):
        print('   ', line)

    def check_cancel(self):
        pass

    def progress(self, data):
        pass


def size(path):
    return sum(p.stat().st_size for p in (path, Path(f'{path}-wal')) if p.is_file())


def files(internal):
    """Operational databases (collected and compacted) and the rest (compacted only)."""
    projects = sorted((internal / 'project-databases').glob('*.sqlite3'),
                      key=lambda p: (len(p.name), p.name))
    operational = [p for p in [internal / 'decomp.db', *projects]
                   if p.is_file() and not p.name.endswith('.transcripts.sqlite3')]
    others = [p for p in [*(internal / 'project-databases').glob('*.transcripts.sqlite3'),
                          internal / 'match-library.sqlite3', internal / 'repair-evidence.sqlite3'] if p.is_file()]
    return operational, others


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('home', nargs='?', default=os.environ.get('DECOMP_HOME'))
    parser.add_argument('--dry-run', action='store_true', help='list the files and their sizes only')
    parser.add_argument('--no-collect', action='store_true', help='compact without removing unused shared text')
    args = parser.parse_args()
    if not args.home:
        parser.error('give the data home (the folder holding .decomp), or set DECOMP_HOME')
    internal = Path(args.home).expanduser() / '.decomp'
    if not (internal / 'decomp.db').is_file():
        parser.error(f'{internal} has no decomp.db')
    with WriterGate(internal / 'server-instance').hold(blocking=False) as admitted:
        if not admitted:
            print('decompdecomp is running for this home. Quit it first, then run this again.', file=sys.stderr)
            return 1
        operational, others = files(internal)
        total = 0
        for path in [*operational, *others]:
            before = size(path)
            print(f'{path.name}: {before / 2**20:,.1f} MiB')
            if args.dry_run:
                continue
            if not args.no_collect and path in operational:
                from decompnet import payload_links
                from decompnet.store import Store
                store = Store(path)
                try:
                    print('    removed', payload_links.collect(store, Log()), 'unused shared payloads')
                finally:
                    store.conn.close()
            if shutil.disk_usage(path.parent).free < 2 * before:
                print('    skipped compaction: free at least twice this file\'s size on its drive')
                continue
            with sqlite3.connect(path, timeout=60) as db:
                db.execute('VACUUM')
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            after = size(path)
            total += max(0, before - after)
            print(f'    compacted to {after / 2**20:,.1f} MiB')
        print(f'Reclaimed {total / 2**30:,.2f} GiB.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
