"""Portable match attribution and a bounded excerpt of verified repair evidence."""
import difflib
import hashlib
import json
import re
import shlex

from .. import toolchains
from ..repair_comparison import differences


def origin(net, attempt_id, credited_name, *, restored=False, project_id=None):
    store = net.project_store(project_id) if project_id is not None else net.store
    row = store.one('SELECT via,agent_id,created_at FROM attempts WHERE id=?', attempt_id)
    agent = net.store.one('SELECT name FROM agents WHERE id=?', row['agent_id']) if row and not credited_name else None
    name = credited_name or (agent['name'] if agent else '') or 'unknown'
    return {'agent': actor(net, store, name, project_id, row['created_at'] if row else None),
            'method': 'saved progress recheck' if restored else (row['via'] if row else '') or 'recorded match'}


def actor(net, store, name, project_id, at):
    if re.fullmatch(r'permuter-\d+(?:-lane-\d+)?', name):
        return 'deep-permuter'
    if at is not None and store.one("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'"):
        session = store.one(
            'SELECT h.model FROM helper_sessions h JOIN projects p ON p.slug=h.project '
            'WHERE p.id=? AND h.agent=? AND h.started_at<=? '
            'ORDER BY h.started_at DESC,h.id DESC LIMIT 1', project_id, name, at)
        if session and session['model']:
            return session['model']
    if net.store.one('SELECT 1 FROM settings WHERE key=?', 'helper-name:'+name):
        return re.sub(r'-\d+$', '', name)
    return name


def short(value, limit=500):
    return ' '.join(str(value or '').split())[:limit]


def flag_text(flags, limit=1000):
    text = shlex.join(flags).replace('\r', r'\r').replace('\n', r'\n') if flags else '(none)'
    return text if len(text) <= limit else text[:limit] + '…'


def build_lines(details):
    compiler = details.get('compiler')
    if not isinstance(compiler, dict) or not compiler:
        return []
    tool = compiler.get('toolchain')
    pack = None
    if tool:
        try:
            pack = toolchains.get(tool)
        except toolchains.ToolchainError:
            pass
    profile = tool or 'custom command'
    if pack and pack.name != tool:
        profile += f' ({pack.name})'
    lines = ['Compiler profile: ' + short(profile)]
    # The registry's default flags are expanded only by the normal toolchain command.
    if not compiler.get('cmd'):
        flags = compiler.get('flags', pack.flags if pack else None)
        if isinstance(flags, list):
            lines.append('Compiler flags: ' + flag_text(flags))
    else:
        lines.append('Compiler flags: supplied by custom command')
    if language := (details.get('settings') or {}).get('language'):
        if language != 'unknown':
            lines.append('Compiler language: ' + short(language, 30))
    return lines


COMPARISON_LABELS = {
    'context_sha256': 'shared header', 'context_unit': 'header scope',
    'effective_command': 'compiler command', 'settings_source': 'settings provenance',
    'scoring_revision': 'scoring revision', 'big_endian': 'processor byte order',
    'rom': 'ROM identity or container metadata', 'gp': 'global pointer',
}
EXPLANATIONS = {
    'explicit_loop_backedge': 'Made the backward jump to the start of the loop explicit.',
}


def comparison(before, after, comparable):
    if comparable:
        return 'same recorded build settings'
    old, new = before.get('settings'), after.get('settings')
    if not isinstance(old, dict) or not isinstance(new, dict):
        missing = []
        if not isinstance(old, dict):
            missing.append('failed baseline')
        if not isinstance(new, dict):
            missing.append('match')
        return 'build settings missing for the ' + ' and '.join(missing)
    changed = differences(old, new)
    if 'compiler' in changed and isinstance(old.get('compiler'), dict) and isinstance(new.get('compiler'), dict):
        changed.remove('compiler')
        fields = differences(old['compiler'], new['compiler'])
        changed.update({'compiler ' + {'toolchain': 'version/toolchain', 'cmd': 'command template'}.get(key, key)
                        for key in fields})
    labels = [COMPARISON_LABELS.get(key, key.replace('_', ' ')) for key in changed]
    if 'compiler flags' in changed:
        old_flags, new_flags = old['compiler'].get('flags'), new['compiler'].get('flags')
        if isinstance(old_flags, list) and isinstance(new_flags, list):
            labels.remove('compiler flags')
            labels.append(f'compiler flags ({flag_text(old_flags, 150)} -> {flag_text(new_flags, 150)})')
    return 'recorded inputs differ in ' + short(', '.join(sorted(labels))) if labels else 'build settings not directly comparable'


def source_excerpt(store, job, captured, before, provenance):
    """Prefer the rewrite's recorded parent. Keep a historical baseline clearly labelled."""
    parent = provenance.get('base_source_sha256')
    row = None
    if parent:
        row = store.one('SELECT source FROM attempts WHERE function_id=? AND id<? AND source_digest=? '
                        'ORDER BY id DESC LIMIT 1', job['function_id'], job['attempt_id'], parent)
    baseline = before.get('source_sha256')
    base = row['source'] if row else None
    exact_parent = bool(base and hashlib.sha256(base.encode()).hexdigest() == parent)
    if not exact_parent:
        row = store.one('SELECT source FROM attempts WHERE function_id=? AND id=?',
                        job['function_id'], before['attempt_id']) if before.get('attempt_id') else None
        base = (row['source'] if row else None) or before.get('source')
        if not base or hashlib.sha256(base.encode()).hexdigest() != baseline:
            return []
    source = captured.get('source')
    if not base or not source or base == source or max(len(base), len(source)) > 65536:
        return []
    old, new = base.splitlines(), source.splitlines()
    # Avoid expensive matching on large functions and avoid dumping whole candidate bodies.
    if max(len(old), len(new)) > 1000:
        return []
    diff = list(difflib.unified_diff(old, new, n=0))[2:]
    if not diff:
        return []
    heading = 'Final rewrite' if exact_parent else 'Change from failed baseline'
    excerpt = [line.rstrip('\r\n') for line in diff[:10]]
    truncated = len(diff) > len(excerpt) or any(len(line) > 200 for line in excerpt)
    lines = [heading+' (C/C++ diff):', *(line[:200] for line in excerpt)]
    if truncated:
        lines.append('… (excerpt truncated)')
    return lines


def message(net, job, note):
    project_id = job['project_id']
    store = net.project_store(project_id)
    captured = json.loads(job['snapshot'])
    info = captured.get('origin')
    if info is None:
        info = origin(net, job['attempt_id'], captured['info'].get('matched_by'),
                      restored=job['notes_after'] == 0, project_id=project_id)
    elif not captured.get('actor_frozen'):
        # Pending jobs from older versions froze the instance name.
        info = {**info, 'agent': actor(net, store, info['agent'], project_id, job['created_at'])}
    name = info['agent']
    text = note['text'].strip() if note is not None and note['text'].strip() else 'Match ' + captured['name']
    label = 'Matched by'
    if captured.get('kind') == 'polished':
        text = 'Polish ' + captured['name']
        label = 'Polished by'
    lines = [label + ': ' + short(name), 'Match method: ' + short(info['method'])]
    lines.extend(build_lines(captured['details']))
    # Full source, assembly and archived payloads stay local.
    examples = store.all('SELECT category,explanation,evidence FROM repair_examples WHERE attempt_id=? AND function_id=? '
                         'ORDER BY id LIMIT 5', job['attempt_id'], job['function_id'])
    for example in examples:
        evidence = json.loads(example['evidence'])
        after = evidence.get('after') or {}
        if after.get('source_sha256') != captured['details']['source_sha256']:
            continue
        before = evidence.get('before') or {}
        category = example['category']
        summary = [short(category.replace('_', ' '), 80)] if category != 'unclassified' else []
        if before.get('score') is not None:
            summary.append(f"failed baseline score {before['score']} -> 0")
        if summary:
            lines.append('Repair evidence: ' + ' · '.join(summary))
        if before:
            lines.append('Repair comparison: ' + comparison(before, after, evidence.get('comparable_settings')))
        provenance = evidence.get('provenance') or {}
        explanation = example['explanation']
        if provenance.get('kind') == 'inherited repair context':
            lines.append('Repair attribution: inherited context; direct rewrite not established')
            provenance = {}
        elif provenance.get('source_sha256') and provenance['source_sha256'] != after.get('source_sha256'):
            provenance = {}
        strategy = ' / '.join(short(provenance.get(key), 150) for key in ('kind', 'family') if provenance.get(key))
        if strategy:
            lines.append('Repair strategy: ' + strategy)
        explanation = explanation or EXPLANATIONS.get(provenance.get('family'))
        if explanation:
            lines.append('Repair explanation: ' + short(explanation))
        lines.extend(source_excerpt(store, job, captured, before, provenance))
    return text + '\n\n' + '\n'.join(lines) + '\n\nDecompdecomp-Match: ' + job['id']
