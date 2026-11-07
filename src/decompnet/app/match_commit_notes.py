"""Portable, bounded attribution for a match commit; never include raw repair targets or transcripts."""
import json


def origin(net, attempt_id, credited_name, *, restored=False, project_id=None):
    store = net.project_store(project_id) if project_id is not None else net.store
    row = store.one('SELECT via,agent_id FROM attempts WHERE id=?', attempt_id)
    agent = net.store.one('SELECT name FROM agents WHERE id=?', row['agent_id']) if row and not credited_name else None
    return {'agent': credited_name or (agent['name'] if agent else '') or 'unknown',
            'method': 'saved progress recheck' if restored else (row['via'] if row else '') or 'recorded match'}


def short(value, limit=500):
    return ' '.join(str(value or '').split())[:limit]


def message(net, job, note):
    project_id = job['project_id']
    store = net.project_store(project_id)
    captured = json.loads(job['snapshot'])
    info = captured.get('origin') or origin(net, job['attempt_id'], captured['info'].get('matched_by'), restored=job['notes_after'] == 0, project_id=project_id)
    text = note['text'].strip() if note is not None and note['text'].strip() else 'Match ' + captured['name']
    lines = ['Matched by: ' + short(info['agent']), 'Match method: ' + short(info['method'])]
    # Read only the compact evidence index. Full sources, assembly and archived payloads stay local.
    examples = store.all('SELECT id,category,status,explanation,evidence FROM repair_examples WHERE attempt_id=? AND function_id=? ORDER BY id LIMIT 5',
                             job['attempt_id'], job['function_id'])
    for example in examples:
        evidence = json.loads(example['evidence'])
        after = evidence.get('after') or {}
        if after.get('source_sha256') != captured['details']['source_sha256']:
            continue
        label = f"Repair example #{example['id']} (local record; {short(example['category'], 80)})"
        before = evidence.get('before') or {}
        if before.get('score') is not None:
            label += f"; score {before['score']} -> 0"
        lines.append(label)
        lines.append('Repair review: ' + short(example['status'], 80))
        if before:
            lines.append('Repair comparison: ' + ('same recorded build settings' if evidence.get('comparable_settings') else 'build settings not directly comparable'))
        if example['explanation']:
            lines.append('Repair explanation: ' + short(example['explanation']))
        provenance = evidence.get('provenance') or {}
        if provenance.get('kind') == 'inherited repair context':
            lines.append('Repair attribution: inherited context; direct rewrite not established')
        elif not provenance.get('source_sha256') or provenance['source_sha256'] == after.get('source_sha256'):
            strategy = ' / '.join(short(provenance.get(key), 150) for key in ('kind', 'family') if provenance.get(key))
            if strategy:lines.append('Repair strategy: ' + strategy)
    return text + '\n\n' + '\n'.join(lines) + '\n\nDecompdecomp-Match: ' + job['id']
