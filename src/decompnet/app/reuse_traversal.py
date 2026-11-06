"""Resume cheap candidate discovery fairly, even when cached work consumes the time budget."""
from contextlib import contextmanager


@contextmanager
def rotating(net, project_id, rows, ready=None):
    """Persist the last visited function once per pass, including cancellation and early returns."""
    key = f'free-candidate-cursor:{project_id}'
    saved = net.store.one('SELECT value FROM settings WHERE key=?', key)
    cursor = int(saved['value']) if saved else None
    rows = list(rows)
    position = next((i+1 for i, row in enumerate(rows) if row['id'] == cursor), 0)
    ordered = rows[position:] + rows[:position]
    last = None
    def visit():
        nonlocal last
        for row in ordered:
            if ready is not None and not ready():
                return
            last = row['id']
            yield row
    try:
        yield visit()
    finally:
        if last is not None:
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, str(last)))
