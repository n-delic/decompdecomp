"""Counter upserts merged within a history batch total exactly what one write per trial would."""
from decompnet.app import trial_bookkeeping
from decompnet.store import Store

SQL = ("INSERT INTO c VALUES(?,?,?,?,?,?) ON CONFLICT(k) DO UPDATE SET n=n+excluded.n, ms=ms+excluded.ms, "
       "first=MIN(first,excluded.first), last=MAX(last,excluded.last), "
       "note=CASE WHEN excluded.last>=last THEN excluded.note ELSE note END")


def merge(old, new):
    return trial_bookkeeping.sums(old, new, 1, first={3}, last={4}, newest=4)


def rows(store):
    return [tuple(r) for r in store.all('SELECT * FROM c ORDER BY k')]


def write(store, batched):
    trials = [('a', 1, 5, 10.0, 10.0, 'x'), ('b', 1, 7, 11.0, 11.0, 'y'), ('a', 1, 3, 12.0, 12.0, 'z'),
              ('a', 1, 2, 9.0, 9.0, 'w')]
    if batched:
        with trial_bookkeeping.scope():
            for args in trials:trial_bookkeeping.count(store, SQL, args, 1, merge)
            assert len(trial_bookkeeping.current().pending) == 2  # one per key until the flush
    else:
        for args in trials:trial_bookkeeping.count(store, SQL, args, 1, merge)


def test_merged_counters_equal_one_write_per_trial(tmp_path):
    results = []
    for batched in (False, True):
        store = Store(tmp_path / f'{batched}.db')
        with store.tx() as db:
            db.execute('CREATE TABLE c(k TEXT PRIMARY KEY, n INTEGER, ms INTEGER, first REAL, last REAL, note TEXT)')
        write(store, batched)
        results.append(rows(store))
    # last/note follow the newest trial by `last` either way
    assert results[0] == results[1] == [('a', 3, 10, 9.0, 12.0, 'z'), ('b', 1, 7, 11.0, 11.0, 'y')]
