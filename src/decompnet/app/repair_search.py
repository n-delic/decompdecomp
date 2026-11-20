"""Read-only corpus lookup for matching agents, plus advisory votes."""
import json
import time
from itertools import islice

from ..network import NetworkError
from ..repair_comparison import mismatch_signature, opcode
from ..repair_feedback import CATEGORIES, _settings

SEARCH_LIMIT = 12
SHORTLIST_LIMIT = 256
DETAIL_LIMIT = 6
MAX_ID = 2**63 - 1


def _context(net, store, agent, proj, function):
    if function:
        fn = net._function(function, proj['slug'])
    else:
        held = store.all("SELECT f.id,f.name,f.vram,f.rom_offset,f.unit FROM claims c JOIN functions f ON f.id=c.function_id "
                         "WHERE f.project_id=? AND c.agent_id=? AND c.expires_at>? AND f.status='open' LIMIT 2",
                         proj['id'], agent.id, time.time())
        if len(held) != 1:
            return None, {}, set()
        fn = held[0]
    latest = store.one('SELECT a.score,m.signature FROM main.attempts a LEFT JOIN repair_attempt_mismatches m '
                       'ON m.attempt_id=a.id WHERE a.function_id=? AND a.agent_id=? ORDER BY a.id DESC LIMIT 1',
                       fn['id'], agent.id)
    signature = ()
    if latest and latest['score'] and latest['score'] > 0:
        kept = net._last_diffs.get((agent.id, fn['id']))
        signature = json.loads(latest['signature']) if latest['signature'] else mismatch_signature(kept[0]) if kept else ()
    return fn, _settings(proj, fn), {tuple(pair) for pair in signature}


def _profile(settings):
    compiler = settings.get('compiler') or settings.get('declared_compiler')
    if isinstance(compiler, dict):
        return compiler.get('toolchain'), compiler.get('flags')
    return (compiler if isinstance(compiler, str) else None), None


def _rank(row, settings, signature):
    recorded = json.loads(row['settings'] or '{}')
    expected_compiler, expected_flags = _profile(settings)
    compiler, flags = _profile(recorded)
    same_compiler = bool(expected_compiler and expected_compiler == compiler)
    same_flags = same_compiler and expected_flags is not None and flags == expected_flags
    past = {tuple(pair) for pair in json.loads(row['signature'] or '[]')}
    wanted = {left for _, left, _ in signature if left}
    present = {left for _, left, _ in past if left}
    if not past and wanted and row['target']:
        present = {opcode(line) for line in row['target'].splitlines()}
    same_unit = settings.get('mode') == 'project' and bool(settings.get('unit')) and recorded.get('unit') == settings['unit']
    # Votes are useful among similarly relevant evidence, but do not outrank a measured mismatch.
    return same_compiler, same_flags, len(signature & past), len(wanted & present), same_unit, row['votes'], row['id']


def _short(text, limit):
    single_line = ' '.join((text or '').split())
    return single_line if len(single_line) <= limit else single_line[:limit - 1] + '…'


def _examples(store, project_id, signature, category=None):
    return store.all(
        "SELECT e.id,f.name,substr(e.explanation,1,300) explanation,e.suggested_category,"
        "json_extract(e.evidence,'$.after.settings') settings,"
        "json_extract(e.evidence,'$.before.mismatch_signature') signature,"
        "json_extract(e.evidence,'$.before.score') score,"
        "CASE WHEN ? AND json_extract(e.evidence,'$.before.mismatch_signature') IS NULL "
        "THEN substr(f.asm,1,4096) END AS target,"
        "(SELECT COUNT(*) FROM repair_example_votes v WHERE v.example_id=e.id) votes "
        "FROM main.repair_examples e JOIN functions f ON f.id=e.function_id "
        "WHERE e.project_id=? AND (? IS NULL OR e.category=?) AND e.status!='dismissed' "
        "AND CASE WHEN json_valid(e.evidence) THEN json_type(e.evidence,'$.before')='object' ELSE 0 END ORDER BY e.id DESC LIMIT ?",
        bool(signature and category), project_id, category, category, SHORTLIST_LIMIT)



def retry_example(net, proj, fn, rows):
    signature = {tuple(pair) for pair in mismatch_signature(rows)}
    if not signature:
        return ''
    settings = _settings(proj, fn)
    store = net.project_store(proj['id'])
    ranked = []
    for row in _examples(store, proj['id'], signature):
        compiler, flags, exact, _, _, votes, number = _rank(row, settings, signature)
        if compiler and flags and exact and row['explanation']:
            ranked.append((exact, votes, number, _short(row['explanation'], 180)))
    if not ranked:
        return ''
    _, _, number, hint = max(ranked)
    return f'Past exact repair with this compiler, flags and a shared mismatch (#{number}): {hint} Read get_repair_examples for the evidence.'


def search(net, agent, slug, category, function=''):
    if category not in CATEGORIES:
        raise NetworkError('Choose a repair category: ' + ', '.join(CATEGORIES))
    proj = net._project(slug)
    store = net.project_store(proj['id'])
    fn, settings, signature = _context(net, store, agent, proj, function)
    rows = _examples(store, proj['id'], signature, category)
    if not rows:
        return f'No captured repairs in category {category} in this project.'
    ranked = sorted(rows, key=lambda row: _rank(row, settings, signature), reverse=True)[:SEARCH_LIMIT]
    title = f'Repair examples for {_short(fn["name"], 80)}' if fn is not None else 'Repair examples by category'
    lines = [title + '. Select IDs with get_repair_examples.']
    for row in ranked:
        label = row['suggested_category'] or category
        summary = row['explanation']
        if not summary:
            pairs = json.loads(row['signature'] or '[]')
            shapes = ', '.join(f'{candidate or "missing"} → {target or "removed"}' for _, target, candidate in pairs[:2])
            summary = f'Failed baseline score {row["score"]} → exact.' + (f' Instruction differences: {shapes}.' if shapes else '')
        votes = f"{row['votes']} vote" + ('' if row['votes'] == 1 else 's')
        lines.append(f'#{row["id"]} {_short(row["name"], 64)} [{_short(label, 64)}, {votes}] {_short(summary, 160)}')
    return '\n'.join(lines)


def _visible_example(store, project_id, case_id):
    return store.one("SELECT e.category,(SELECT COUNT(*) FROM repair_example_votes v WHERE v.example_id=e.id) votes "
                     "FROM main.repair_examples e WHERE e.id=? AND e.project_id=? AND e.status!='dismissed'",
                     case_id, project_id)


def _show_profile(stage):
    settings = stage.get('settings') or {}
    compiler, flags = _profile(settings)
    if not compiler:
        return 'compiler settings unavailable'
    if settings.get('per_file_flags_recorded') is False:
        return _short(compiler, 130) + ' (per-file flags unavailable)'
    return _short(compiler + (' ' + ' '.join(flags) if flags else ''), 180)


def _detail_text(service, slug, case_id, category, votes):
    data = service.detail(slug, case_id)
    evidence = data['evidence']
    before, after = evidence.get('before') or {}, evidence['after']
    result = [f'#{case_id} {_short(evidence.get("function"), 100)} [{category}, {votes} votes]',
              f'Before ({before.get("score", "no failed score")}): {_show_profile(before)}',
              f'Exact match ({evidence.get("judge", "verified")}): {_show_profile(after)}']
    if data['explanation']:
        result.append(_short(data['explanation'], 350))
    if data['suggested_category']:
        result.append('Suggested category: ' + _short(data['suggested_category'], 80))
    if before.get('source') and after.get('source'):
        diff = evidence.get('function_diff') or evidence['diff']
        lines = [line if len(line) <= 140 else line[:139] + '…' for line in islice(diff.splitlines(), 17)]
        result.extend(lines[:16])
        if len(lines) > 16:
            result.append('… diff excerpt')
        if not lines:
            result.append('The recorded source is unchanged.')
    else:
        result.append('Before-and-after source is unavailable for this example.')
    return '\n'.join(result)


def details(net, agent, slug, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= DETAIL_LIMIT or any(type(i) is not int or not 1 <= i <= MAX_ID for i in ids):
        raise NetworkError(f'Pass 1–{DETAIL_LIMIT} positive repair example IDs.')
    service = net.repair_feedback.for_project(slug)
    proj = net._project(slug)
    result = []
    for case_id in dict.fromkeys(ids):
        row = _visible_example(service.store, proj['id'], case_id)
        if row:
            result.append(_detail_text(service, slug, case_id, row['category'], row['votes']))
        else:
            result.append(f'#{case_id} is unavailable in this project.')
    return '\n\n'.join(result)


def upvote(net, agent, slug, id):
    if type(id) is not int or not 1 <= id <= MAX_ID:
        raise NetworkError('Pass a positive repair example ID.')
    proj = net._project(slug)
    store = net.project_store(proj['id'])
    with store.tx() as db:
        if not db.execute("SELECT 1 FROM main.repair_examples WHERE id=? AND project_id=? AND status!='dismissed'",
                          (id, proj['id'])).fetchone():
            raise NetworkError('No available repair example with that ID in this project.')
        added = db.execute('INSERT OR IGNORE INTO repair_example_votes(example_id,agent_id) VALUES(?,?)',
                           (id, agent.id)).rowcount
    return f'Upvoted repair example #{id}.' if added else f'You already upvoted repair example #{id}.'
