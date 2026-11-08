"""Shared writer cursors must not deadlock Python SQL callbacks."""
import os
import subprocess
import sys


def test_cursor_fetch_waits_outside_sqlite_mutex():
    code = '''
import sqlite3, threading, time
from decompnet.payload_storage import Connection
conn = sqlite3.connect(':memory:', check_same_thread=False, factory=Connection)
conn.execute('CREATE TABLE data(value TEXT)')
conn.executemany('INSERT INTO data VALUES(?)', [('first',), ('second',), ('third',)])
reader = conn.execute('SELECT value FROM data')
entered = threading.Event()
release = threading.Event()
def callback():
    entered.set()
    assert release.wait(3)
    return 'callback'
conn.create_function('callback', 0, callback)
results = []
a = threading.Thread(target=lambda: results.append(conn.execute('SELECT callback()').fetchone()))
a.start()
assert entered.wait(3)
b = threading.Thread(target=lambda: results.extend(reader.fetchall()))
b.start()
time.sleep(.1)
release.set()
a.join(3); b.join(3)
assert not a.is_alive() and not b.is_alive()
assert sorted(results) == [('callback',), ('first',), ('second',), ('third',)]
assert list(conn.execute('SELECT value FROM data')) == [('first',), ('second',), ('third',)]
conn.close()
'''
    subprocess.run([sys.executable, '-c', code], env=os.environ.copy(), check=True, timeout=10)
