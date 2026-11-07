"""Renew bounded permutation allowances when external inputs change, never for the permuter's own steps."""
import hashlib
from pathlib import Path

from ..project import compiler_for


def build_state(proj):
    """Imported builds own these files; inspect their content without executing their commands."""
    result = []
    for name in ('objdiff.json', 'build.ninja', 'Makefile', 'compile_commands.json', 'config.json'):
        path = Path(proj['root']) / name
        if path.is_file():
            result.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return result


def refresh(net, proj, rows):
    """Adopt existing counters on first observation; later context/target/build changes reopen only idle work."""
    rows = list(rows)
    if any(fn['project_id'] != proj['id'] for fn in rows):
        raise ValueError('Permutation inputs must belong to the selected project')
    store = net.project_store(proj['id'])
    cfg = net.cfg(proj)
    fork = net.fork_slug(proj['slug'])
    symbols = [tuple(r) for r in store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    build = build_state(proj) if fork else []
    changed = []
    for fn in rows:
        header = net.forks.permuter_header(fork, fn['name']) if fork else net._context(proj['id'], fn['unit'])['header']
        own = cfg.get('compiler') if fork else compiler_for(cfg, fn['vram'], fn)
        fields = (header, own, symbols, build, fn['unit'], fn['vram'], fn['target'], fn['relocs'], fn['name'])
        digest = hashlib.sha256(repr(fields).encode()).hexdigest()
        key = f'permuter-inputs-v1:{fn["id"]}'
        with store.tx() as db:
            old = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            if old and old['value'] != digest:
                if db.execute('SELECT 1 FROM claims WHERE function_id=?', (fn['id'],)).fetchone():
                    continue  # retry after the current turn, without consuming its future allowance
                db.execute("UPDATE functions SET permuted=0 WHERE id=? AND status='open'", (fn['id'],))
                changed.append(fn['id'])
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, digest))
    return changed


def external_seed(db, current, source, agent_name):
    """Called only for an improving saved candidate. Identical text and our own hill climbing keep their cap."""
    if agent_name != 'permuter' and source != current['best_source']:
        db.execute("UPDATE functions SET permuted=0 WHERE id=? AND status='open'", (current['id'],))
