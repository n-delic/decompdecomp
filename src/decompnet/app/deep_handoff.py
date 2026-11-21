"""Persist a measured residual for the next agent working on the current draft."""
import json

from ..arch import get
from . import experiment_evidence, region_repairs, trial_bookkeeping

PREFIX = 'deep-handoff:'


def smallest_block(rows, arch):
    rows = region_repairs.clean(rows)
    if not rows:
        return []
    targets = [target for _, target, _ in rows if target]
    reader = get(arch).instruction_regions
    graph = reader(targets) if reader and targets else None
    if not graph:
        first = next((i for i, (mark, _, _) in enumerate(rows) if mark != ' '), None)
        return rows[max(0, first-2):first+6] if first is not None else []
    blocks = {}
    index = 0
    for row in rows:
        _, target, _ = row
        position = index if target else max(0, index-1)
        blocks.setdefault(graph['block_for'][position], []).append(row)
        index += bool(target)
    unresolved = [block for block in blocks.values() if any(mark != ' ' for mark, _, _ in block)]
    if not unresolved:
        return []
    block = min(unresolved, key=len)
    first = next(i for i, (mark, _, _) in enumerate(block) if mark != ' ')
    return block[max(0, first-2):first+6]


def save(net, proj, fn, source, rows):
    if fn['status'] != 'open' or not source or source != fn['best_source']:
        return False
    if not (block := smallest_block(rows, proj['arch'])):
        return False
    store = experiment_evidence.prepare(net, fn['project_id'])
    trial_bookkeeping.flush(store)
    scope = experiment_evidence.scope(net, proj, fn, source)
    families = store.all(
        'SELECT family,attempted,same_output,worse,failed FROM repair_input_effects '
        'WHERE function_id=? AND scope=? AND improved=0 AND attempted>0 '
        'ORDER BY attempted DESC,family LIMIT 6', fn['id'], scope)
    if sum(row['attempted'] for row in families) < 3:
        return False
    record = dict(scope=scope, block=[[item[:120] for item in row] for row in block],
                  families=[dict(row) for row in families])
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   (PREFIX+str(fn['id']), json.dumps(record)))
    return True


def brief(net, proj, fn, source):
    if fn['status'] != 'open' or not source or source != fn['best_source']:
        return ''
    store = net.project_store(fn['project_id'])
    row = store.one('SELECT value FROM settings WHERE key=?', PREFIX+str(fn['id']))
    if not row:
        return ''
    record = json.loads(row['value'])
    if record['scope'] != experiment_evidence.scope(net, proj, fn, source):
        return ''
    tried = '; '.join(f"{r['family']} ({r['attempted']} tried, {r['same_output']} same output, "
                      f"{r['worse']} worse, {r['failed']} failed)" for r in record['families'])
    diff = '\n'.join(f'{mark} {target} => {candidate}' for mark, target, candidate in record['block'])
    return (f'Deep Permuter handoff for the saved C draft. Trials without a score improvement: {tried}.\n'
            f'Focused residual (target => draft):\n{diff}\n'
            'Use the saved draft as your baseline. Propose one C change that explains this residual and test it '
            'with the ordinary judge. Submit a verified improvement so Deep Permuter can resume from it.')
