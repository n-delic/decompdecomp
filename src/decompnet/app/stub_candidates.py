"""Remember which saved candidates the judge found to be stubs, so a claim and a summary can say so without a compile.

The diff of a compile says how many of the target's instructions the candidate emitted (diff_summary.stub). The
fact is kept per function with the digest of the source it was measured on, and only used for that same source.
"""
import hashlib
import json

from ..diff_summary import stub, stub_message

KEY = 'stub-candidate:'


def record(db, function_id, source, rows):
    """Called with every judged attempt; keeps the verdict for this source, drops an older one for another."""
    found = stub(rows) if rows else None
    if found:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (KEY + str(function_id), json.dumps(
            {'source': hashlib.sha256((source or '').encode()).hexdigest(), 'found': found})))
    else:
        db.execute('DELETE FROM settings WHERE key=?', (KEY + str(function_id),))


def hint(net, fn, source):
    """The stub message when the judge measured this very source as a stub, else ''."""
    if fn['status'] != 'open' or not source:
        return ''
    row = net.project_store(fn['project_id']).one('SELECT value FROM settings WHERE key=?', KEY + str(fn['id']))
    if not row:
        return ''
    saved = json.loads(row['value'])
    if saved.get('source') != hashlib.sha256(source.encode()).hexdigest():
        return ''
    return stub_message(tuple(saved['found']))
