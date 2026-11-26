"""Repair rules learnt from the Deep Permuter's matches (decomprepair.rule_mining makes them, this keeps the count).

A search that matched left what it matched with and a hash of the C it started from. Where both are still saved, the
one change between them is generalised; once two different matches made the same change, it is written to the home's
rules folder, beside the rules agents write, and tried first on other functions (`variants`). A rule only proposes C:
the judge decides, as for every repair.

Only a project somebody is searching is read (`refresh`, from its own win records, once each), so the history of a
game counts the first time a search runs on it. A rule is written once: deleting its file keeps it deleted."""
import hashlib
import json
import os
import threading

from . import repair_rules

try:
    from decomprepair import rule_mining, rules
except ImportError:  # a library older than the one that added rule_mining
    rule_mining = rules = None

SUPPORT = 2          # matches (of different C) that must have made the same change
MAX_RULES = 40       # ever written: every rule is regex work on every function, and agents share the folder
MAX_VARIANTS = 48    # candidates one pass tries on a function
PAGE = 200
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS mined_rewrites(old TEXT NOT NULL, new TEXT NOT NULL, arch TEXT NOT NULL,
       source TEXT NOT NULL, PRIMARY KEY(old,new,arch,source))''',
    'CREATE TABLE IF NOT EXISTS mined_rules(id TEXT PRIMARY KEY, support INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS mined_cursors(project_id INTEGER PRIMARY KEY, win_id INTEGER NOT NULL)',
)
_LOCK = threading.Lock()


def _prepare(net):
    with net.store.tx() as db:
        for statement in SCHEMA:
            db.execute(statement)


def _saved_sources(store, function_id):
    """{sha256: C} of what is still saved for a function: its judged attempts and its verified match."""
    table = 'attempt_details' if store.one("SELECT 1 FROM sqlite_master WHERE name='attempt_details'") else 'attempts'
    rows = store.all(f'SELECT source FROM {table} WHERE function_id=?', function_id)
    if store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
        rows += store.all('SELECT source FROM verified_matches WHERE function_id=?', function_id)
    texts = (row['source'] for row in rows)
    return {hashlib.sha256(text.encode()).hexdigest(): text for text in texts if isinstance(text, str) and text}


def _learn(net, proj, check):
    """Read the project's winning matches not read before. True when anything new was recorded."""
    store = net.project_store(proj['id'])
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='deep_permutation_recipe_wins'"):
        return False
    row = net.store.one('SELECT win_id FROM mined_cursors WHERE project_id=?', proj['id'])
    cursor = row['win_id'] if row else 0
    learned = False
    while True:
        check()
        wins = store.all('''SELECT w.id, w.function_id, w.provenance, f.name FROM deep_permutation_recipe_wins w
            JOIN functions f ON f.id=w.function_id
            WHERE w.id>? AND w.new_score=0 AND f.project_id=? ORDER BY w.id LIMIT ?''', cursor, proj['id'], PAGE)
        if not wins:
            return learned
        saved, found = {}, []
        for win in wins:
            check()
            cursor = win['id']
            info = json.loads(win['provenance'])
            sources = saved.get(win['function_id'])
            if sources is None:
                sources = saved[win['function_id']] = _saved_sources(store, win['function_id'])
            before, after = sources.get(info.get('base_source_sha256')), sources.get(info.get('source_sha256'))
            if before and after and (pair := rule_mining.rewrite(before, after, win['name'])):
                found.append((*pair, proj['arch'], info['source_sha256']))
        with net.store.tx() as db:
            for values in found:
                db.execute('INSERT OR IGNORE INTO mined_rewrites VALUES(?,?,?,?)', values)
            db.execute('INSERT OR REPLACE INTO mined_cursors VALUES(?,?)', (proj['id'], cursor))
        learned = learned or bool(found)


def _publish(net, log):
    """Write a rule file for every change that has reached SUPPORT and has none yet, the most used first."""
    written = {row['id'] for row in net.store.all('SELECT id FROM mined_rules')}
    rows = net.store.all('''SELECT old, new, COUNT(DISTINCT source) support, GROUP_CONCAT(DISTINCT arch) archs
        FROM mined_rewrites GROUP BY old, new HAVING COUNT(DISTINCT source)>=? ORDER BY support DESC, old, new''', SUPPORT)
    folder = repair_rules.folder(net.workspace)
    for row in rows:
        key = rule_mining.identifier(row['old'], row['new'])
        path = folder / f'{key}.json'
        archs = sorted(row['archs'].split(','))
        if key in written:
            with net.store.tx() as db:
                db.execute('UPDATE mined_rules SET support=? WHERE id=?', (row['support'], key))
            if not path.is_file():
                continue          # somebody deleted it: it stays deleted
            if json.loads(path.read_text(encoding='utf-8')).get('archs', []) == [a for a in archs if a in rules.ARCHS]:
                continue
        elif len(written) >= MAX_RULES or len(list(folder.glob('*.json'))) >= repair_rules.MAX_FILES:
            continue
        rule = rule_mining.rule(row['old'], row['new'], archs, f'Learnt from matches: {row["old"]} => {row["new"]}')
        if rules.problems(rule):
            continue
        folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(rule, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO mined_rules VALUES(?,?)', (key, row['support']))
        if key not in written:
            written.add(key)
            log(f'Learned a repair rule from {row["support"]} matches: {row["old"]}  =>  {row["new"]}')
    repair_rules.configure(net.workspace)


def refresh(net, proj, log, check=lambda: None):
    """Count the changes behind the project's new matches and write the rules that have earned it."""
    if rule_mining is None or getattr(net, 'workspace', None) is None:
        return
    with _LOCK:
        _prepare(net)
        if _learn(net, proj, check):
            _publish(net, log)


def variants(net, arch, source, name):
    """[(family, C)] of the learnt rules on `source`, the most used rule first, at most MAX_VARIANTS."""
    if rule_mining is None or not source or not net.store.one("SELECT 1 FROM sqlite_master WHERE name='mined_rules'"):
        return []
    loaded = rules.loaded()
    out, seen = [], {source}
    for row in net.store.all('SELECT id FROM mined_rules ORDER BY support DESC, id'):
        rule = loaded.get(row['id'])
        if rule is None or (rule.archs and arch not in rule.archs):
            continue
        for family, text in rules.apply(rule, source, name):
            if text not in seen:
                seen.add(text)
                out.append((family, text))
        if len(out) >= MAX_VARIANTS:
            break
    return out[:MAX_VARIANTS]
