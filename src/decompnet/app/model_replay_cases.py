"""Freeze and reverify private paired model cases through the normal copied judges."""

import hashlib
import json
import os
import re
from pathlib import Path

from .. import agent_notes, scoring, toolchains
from ..match_bundle import compiler_identity
from ..network import NetworkError
from ..project import compiler_for
from . import model_replay


def copied_engine(home, slug):
    """Refuse running homes and project paths outside the nominated copy."""
    import sqlite3

    from .engine import RomNetwork

    home = home.resolve()
    internal = home / '.decomp'
    catalog = internal / 'decomp.db'
    if (internal / 'server.json').exists() or not catalog.is_file():
        raise ValueError('Use an offline copied app home with its SQLite backups and no server.json.')
    if not catalog.resolve().is_relative_to(home) or internal.resolve() != internal:
        raise ValueError('The copied database must live inside the offline home.')
    from contextlib import closing

    from ..header_storage import Codec

    def row(path, query, argument):
        if not path.resolve().is_relative_to(home):
            raise ValueError('Copy the project database inside the offline home.')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            Codec().register(db)
            return db.execute(query, (argument,)).fetchone()

    project = row(catalog, 'SELECT id,root FROM projects WHERE slug=?', slug)
    if project is None:
        raise ValueError('The copied catalog does not contain that project.')
    project_id, root = project
    project_db = internal / 'project-databases' / f'{project_id}.sqlite3'
    if project_db.exists():
        record = row(project_db, 'SELECT root FROM projects WHERE id=?', project_id)
        if record is None:
            raise ValueError('The copied project database is missing its project row.')
        root = record[0]
    root = Path(root).resolve()
    if root == home or not root.is_relative_to(home) or not root.is_dir():
        raise ValueError('Update both copied database roots to the project folder inside the offline home.')
    # Do not refresh an installed app's object cache, or give one comparison arm a cache
    # warmed by a different arm's candidate.
    os.environ['DECOMP_HOME'] = str(home)
    os.environ['DECOMP_OBJECT_CACHE_MB'] = '0'
    net = RomNetwork(catalog, home, project_databases=project_db.is_file())
    proj = net.project_row(slug, sync=False)
    return net, proj


def _declarations(header, source):
    wanted = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
    lines = []
    for line in header.splitlines():
        if line.strip().startswith(('extern ', 'typedef ', '#define ')) and wanted.intersection(re.findall(r'\b[A-Za-z_]\w*\b', line)):
            lines.append(line[:400])
    return '\n'.join(lines)[:5000]


def prepare(net, proj, entry):
    store = net.project_store(proj['id'])
    fn = store.one('SELECT * FROM functions WHERE project_id=? AND name=?', proj['id'], entry['name'])
    if fn is None:
        raise ValueError('A frozen function is absent from the copied project.')
    rom_mode = net.is_rom(proj)
    if rom_mode:
        config = net.cfg(proj)
        profile = compiler_for(config, fn['vram'], fn)
        if not profile or not profile.get('toolchain') or 'cmd' in profile or not toolchains.installed(profile['toolchain']):
            raise ValueError('Use an installed pinned compiler for the frozen ROM case.')
        header_row = store.one("SELECT header FROM context_versions WHERE project_id=? AND version=? "
                               "AND unit IN (?,'') ORDER BY (unit=?) DESC LIMIT 1",
                               proj['id'], entry['context_version'], fn['unit'], fn['unit'])
        if header_row is None:
            raise ValueError('The historical header is unavailable.')
        header = header_row['header']
        inputs = agent_notes.rom_inputs(store.one, proj, fn, entry['context_version'])
        compiler = compiler_identity(profile)
        rom = (Path(proj['root']) / config['rom']['file']).resolve()
        if not rom.is_relative_to(Path(proj['root']).resolve()):
            raise ValueError('Copy the ROM image inside the offline project folder.')
        image_key = (str(rom), rom.stat().st_size, rom.stat().st_mtime_ns)
        hashes = net.__dict__.setdefault('_model_replay_images', {})
        if image_key not in hashes:
            with rom.open('rb') as stream:
                hashes[image_key] = hashlib.file_digest(stream, 'sha256').hexdigest()
        image = hashes[image_key]

        def evaluate(source):
            from ..source_lines import mismatches, render
            from .forkengine import _CHEAT, _strip_comments

            if _CHEAT.search(_strip_comments(source)):
                return {'matched': False, 'score': None, 'error': 'Submit C or C++ without inline assembly.'}
            built = net._compile(proj, header, source, fn)
            if not built.ok:
                return {'matched': False, 'score': None, 'error': built.log}
            candidate, exact, problems = net._evaluate(fn, proj, built.obj)
            if candidate is None:
                return {'matched': False, 'score': None, 'error': '\n'.join(problems)}
            diff = scoring.diff(net._target_obj(fn, proj), candidate)
            exact = bool(exact if exact is not None else diff.bytes_match)
            lines = mismatches(built.obj, fn['name'], diff.rows, source,
                               filename='func.cpp' if built.language == 'c++' else 'func.c', arch=proj['arch'])
            return {'matched': exact, 'score': 0 if exact else max(1, diff.score), 'rows': diff.rows,
                    'line_note': render(lines, source, limit=4)}
    else:
        from ..candidate_scope import dependencies
        from ..forks.project import ForkProject

        header = ''
        image = None
        tree = ForkProject(Path(proj['root']), create_state=False)
        unit = tree.unit(fn['unit'])
        if not unit.source or not unit.target or not Path(unit.target).resolve().is_relative_to(tree.root.resolve()):
            raise ValueError('Copy the imported unit source and target object into the offline project folder.')
        if not (tree.root / unit.source).resolve().is_relative_to(tree.root.resolve()):
            raise ValueError('Copy the imported unit source into the offline project folder.')
        inputs = agent_notes.project_inputs(tree, unit, fn)
        profile = {'unit': fn['unit'],
                   'compiler': net.console(proj).file_compiler(tree, unit.source)}
        compiler = model_replay.digest(repr((profile, dependencies(tree.root, unit.source))))
        agent, _ = net.local_agent('model-replay-judge')

        def evaluate(source):
            try:
                with net.progress.view():
                    result = net.forks._attempt(agent, proj['slug'], fn['name'], source, keep=False,
                                                diagnostic=True, background=True)
            except NetworkError as err:
                return {'matched': False, 'score': None, 'error': str(err)}
            return {**result, 'score': 0 if result.get('matched') else result.get('score'),
                    'error': result.get('error') or (result.get('text', '') if result.get('score') is None else '')}
    if not inputs:
        raise ValueError('Could not freeze the judge inputs for this function.')
    if entry.get('recorded_inputs') and entry['recorded_inputs'] != inputs:
        raise ValueError('The recorded compiler, header or target changed.')
    scope = {'build_sha256': model_replay.digest(inputs), 'compiler_identity': compiler, 'image_sha256': image,
             'source_sha256': model_replay.digest(entry['source']),
             'control_sha256': model_replay.digest(entry['control']),
             'stages_sha256': model_replay.digest(json.dumps(entry.get('stages', [])))}
    if entry.get('scope', scope) != scope:
        raise ValueError('A frozen source, target or build input changed.')
    from ..candidate_scope import callees
    from .model_replay_evidence import matched_callers

    if rom_mode:
        net.xrefs(proj)
    matched_callees = callees(net, proj, fn, entry['source'], max_source_chars=8000)
    return {**entry, 'scope': scope, 'callees': matched_callees, 'callers': matched_callers(net, proj, fn), 'tracks_invocations': rom_mode, 'profile': profile, 'measure': evaluate, 'arch':proj['arch'],
            'assembly': fn['asm'] or '', 'header': header, 'declarations': _declarations(header, entry['source'])}


def read_cases(net, proj, path):
    document = json.loads(path.read_text())
    entries = document.get('cases', [])
    if (document.get('format') != 1 or not 1 <= len(entries) <= 32
            or len({e['name'] for e in entries}) != len(entries)):
        raise ValueError('Use a frozen manifest of 1–32 distinct functions.')
    if any('scope' not in entry for entry in entries):
        raise ValueError('Freeze all case scopes before replay.')
    return [prepare(net, proj, entry) for entry in entries]


def _claim_source(transcripts, store, project, name, attempt, agent_name):
    session = store.one("SELECT s.id,s.started_at FROM helper_sessions s JOIN projects p ON p.slug=s.project "
                        "WHERE p.id=? AND s.agent=? AND s.model=? AND s.started_at<=? "
                        "AND (s.ended_at IS NULL OR s.ended_at>=?) ORDER BY s.id DESC LIMIT 1",
                        project, agent_name, model_replay.LUNA, attempt['created_at'], attempt['created_at'])
    if not session:
        return None
    claim = store.one("SELECT created_at FROM events WHERE project_id=? AND kind='claim' AND detail=? AND "
                      "agent_id=? AND created_at BETWEEN ? AND ? ORDER BY created_at DESC LIMIT 1",
                      project, name, attempt['agent_id'], session['started_at'], attempt['created_at'])
    if not claim:
        return None
    for row in transcripts.all("SELECT text FROM helper_messages WHERE session_id=? AND kind='tool_response' "
                               "AND created_at BETWEEN ? AND ? ORDER BY id LIMIT 24",
                               session['id'], claim['created_at'], attempt['created_at']):
        try:
            reply = json.loads(row['text'])
        except (TypeError, ValueError):
            continue
        if not isinstance(reply, dict) or reply.get('name') != 'claim_function':
            continue
        content = reply.get('content')
        if isinstance(content, list):
            content = '\n'.join(part.get('text', '') for part in content
                                if isinstance(part, dict) and isinstance(part.get('text'), str))
        if not isinstance(content, str):
            continue
        source = claim_candidate(content, name)
        if source:
            return source
    return None


def claim_candidate(text, name):
    """Use only an unambiguous candidate block in Luna's recorded claim reply."""
    if not re.search(r'(?m)^#+ CLAIMED:.*\b' + re.escape(name) + r'(?:\s|$)', text):
        return None
    pattern = r'```(?:c|cpp|c\+\+)\s*\n([\s\S]*?)```'
    blocks = [source for source in re.findall(pattern, text) if re.search(r'\b'+re.escape(name)+r'\s*\(', source)]
    return blocks[0] if len(blocks) == 1 else None


def historical_cases(net, proj, limit, *, allow_submission_start=False):
    """Pick Luna's claim sources followed by an independently reproduced Sol win."""
    from .transcripts import service
    if not 1 <= limit <= 32:
        raise ValueError('Choose 1–32 distinct historical cases.')
    store = net.project_store(proj['id'])
    windows = store.all('SELECT agent,model,started_at,ended_at FROM helper_sessions WHERE project=? AND model IN (?,?)',
                        proj['slug'], model_replay.LUNA, model_replay.SOL)
    names = {r['name']: r['id'] for r in net.store.all('SELECT id,name FROM agents')}
    agent_names = {value: name for name, value in names.items()}
    transcripts = service(net).for_project(proj['slug']).store
    sessions = json.dumps([(names[r['agent']], r['model'], r['started_at'], r['ended_at'])
                           for r in windows if r['agent'] in names])
    wins = store.all('''
        SELECT DISTINCT a.id, a.function_id, a.context_version, a.handoff_inputs, a.agent_id, a.created_at, f.name
        FROM main.attempts a JOIN main.functions f ON f.id=a.function_id
        JOIN json_each(?) s ON json_extract(s.value,'$[0]')=a.agent_id
        WHERE a.via='agent' AND a.score=0 AND a.compiled AND f.project_id=?
          AND json_extract(s.value,'$[1]')=?
          AND a.created_at BETWEEN json_extract(s.value,'$[2]') AND coalesce(json_extract(s.value,'$[3]'),1e20)
          AND f.n_insns BETWEEN 24 AND 220
        ORDER BY (a.handoff_inputs IS NOT NULL) DESC, a.id DESC LIMIT 400
    ''', sessions, proj['id'], model_replay.SOL)
    selected = []
    seen = set()
    for win in wins:
        if win['name'] in seen:
            continue
        luna = store.one('''
            SELECT a.id,a.score,a.handoff_inputs,a.agent_id,a.created_at FROM main.attempts a
            WHERE a.function_id=? AND a.context_version=? AND a.id<? AND a.score>0 AND a.via='agent'
              AND EXISTS (SELECT 1 FROM json_each(?) s WHERE json_extract(s.value,'$[0]')=a.agent_id
                  AND json_extract(s.value,'$[1]')=?
                  AND a.created_at BETWEEN json_extract(s.value,'$[2]') AND coalesce(json_extract(s.value,'$[3]'),1e20))
            ORDER BY a.id LIMIT 1
        ''', win['function_id'], win['context_version'], win['id'], sessions, model_replay.LUNA)
        if luna is None:
            continue
        source = _claim_source(transcripts, store, proj['id'], win['name'], luna, agent_names[luna['agent_id']])
        origin = 'recorded_luna_claim'
        if source is None and allow_submission_start:
            source = store.one('SELECT source FROM attempts WHERE id=?', luna['id'])['source']
            origin = 'first_measured_luna_submission'
        control = store.one('SELECT source FROM attempts WHERE id=?', win['id'])['source']
        if not source or not control:
            continue
        rows = store.all('SELECT id FROM main.attempts WHERE function_id=? AND context_version=? AND agent_id=? '
                         "AND id>? AND id<=? AND via='agent' AND compiled AND score>=0 AND score<? ORDER BY id LIMIT 7",
                         win['function_id'], win['context_version'], win['agent_id'], luna['id'], win['id'], luna['score'])
        stages = [store.one('SELECT source FROM attempts WHERE id=?', row['id'])['source'] for row in rows]
        if luna['handoff_inputs'] and win['handoff_inputs'] and luna['handoff_inputs'] != win['handoff_inputs']:
            continue
        entry = {'id': f'case-{len(selected)+1:02d}', 'name': win['name'], 'source': source, 'control': control,
                 'context_version': win['context_version'], 'stages': list(dict.fromkeys([s for s in stages if s]+[control])),
                 'recorded_inputs': win['handoff_inputs'] or luna['handoff_inputs'],
                 'starting_source_origin': origin,
                 'luna_attempt_id': luna['id'], 'sol_attempt_id': win['id']}
        try:
            prepared = prepare(net, proj, entry)
            baseline, exact = prepared['measure'](source), prepared['measure'](control)
        except (NetworkError, ValueError):
            continue
        if (baseline.get('score') is None or baseline.get('matched') or not exact.get('matched')
                or (origin == 'first_measured_luna_submission' and baseline['score'] != luna['score'])):
            continue
        selected.append({**entry, 'scope': prepared['scope']})
        seen.add(win['name'])
        if len(selected) == limit:
            break
    if not selected:
        raise ValueError('No comparable historic Luna submission and Sol match reproduced under the copied judge.')
    return {'format': 1, 'cases': selected}
