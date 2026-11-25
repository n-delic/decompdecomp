"""Keep a small set of independently rejudged repair roots between searches."""
import json

from .. import candidate_scope

KEY = 'deep-resume-v1'
WIDTH = 3
MAX_SOURCE = 128_000
MAX_ROWS = 512


def _load(store, fid):
    row = store.one(
        'SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fid, KEY,
    )
    try:
        nodes = json.loads(row['feedback']) if row else []
    except (ValueError, TypeError):
        return []
    return nodes if isinstance(nodes, list) else []


def _scope(net, proj, fn, source):
    return candidate_scope.key(net, proj, dict(fn, best_source=source))


def roots(net, proj, fn):
    store = net.project_store(proj['id'])
    return [node for node in _load(store, fn['id'])
            if node['source'] != fn['best_source']
            and node['scope'] == _scope(net, proj, fn, node['source'])]


def keep(net, proj, fn, source, score, rows, output, path, recovered):
    if not output or not path or len(source) > MAX_SOURCE:
        return
    node = dict(source=source, score=score, rows=rows[:MAX_ROWS], output=output,
                path=path, recovered=recovered, scope=_scope(net, proj, fn, source))
    store = net.project_store(proj['id'])
    # Each node's scope is a hash of the function's build inputs (candidate_scope.key): computed once per source,
    # here, never inside the writer. It held the project's single writer for most of a second per call (322 calls
    # held it 205 s at 32 s of CPU) while a lane recomputed it for the saved nodes.
    scopes = {node['source']: node['scope']}
    for old in _load(store, fn['id']):
        scopes.setdefault(old['source'], _scope(net, proj, fn, old['source']))
    with store.tx() as db:
        def current(old):
            if old['source'] == source or old['output'] == output:
                return False
            if old['source'] not in scopes:  # saved by another lane since the read above: judged here, rarely
                scopes[old['source']] = _scope(net, proj, fn, old['source'])
            return old['scope'] == scopes[old['source']]
        nodes = [old for old in _load(store, fn['id']) if current(old)]
        nodes.append(node)
        # Keep the cheapest root and favor residuals that removed target sites.
        nodes.sort(key=lambda old: old['score'])
        nodes[1:] = sorted(nodes[1:], key=lambda old: (-old['recovered'], old['score']))
        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                   (fn['id'], KEY, 'frontier', 'clean measured repair roots', json.dumps(nodes[:WIDTH])))
