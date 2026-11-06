"""Local evidence for turning successful manual repairs into future deterministic rules.

A judge verdict is evidence of a match; an agent's explanation is a hypothesis, never a new rule.
The shared recorder handles every architecture and both ROM and imported-project modes.
"""
from __future__ import annotations

import contextvars
import difflib
import hashlib
import json
import logging
import time
from contextlib import contextmanager

from .network import NetworkError
from .project import compiler_for
from .repair_comparison import differences as settings_differences

CATEGORIES = ('unclassified', 'types', 'casts', 'constants', 'addresses', 'signatures', 'layout',
              'temporaries', 'expression_order', 'control_flow', 'compiler_settings', 'other')
STATES = ('new', 'reviewed', 'implemented', 'dismissed')
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS repair_attempt_settings(
        attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE, settings TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS repair_attempt_provenance(
        attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE, provenance TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS repair_examples(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
        attempt_id INTEGER REFERENCES attempts(id) ON DELETE CASCADE,
        before_attempt_id INTEGER REFERENCES attempts(id) ON DELETE SET NULL, agent_id INTEGER, created_at REAL NOT NULL, evidence TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'unclassified', explanation TEXT NOT NULL DEFAULT '',
        automation TEXT NOT NULL DEFAULT '', review_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new')''',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_project ON repair_examples(project_id,id)',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_attempt ON repair_examples(attempt_id)',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_function ON repair_examples(function_id,id)',
)


_TRIAL = contextvars.ContextVar('repair_trial', default=None)


@contextmanager
def trial(provenance):
    token = _TRIAL.set(provenance)
    try:
        yield
    finally:
        _TRIAL.reset(token)


def current_trial():
    return _TRIAL.get()


def validated_provenance(provenance, source_hash):
    """Cascaded matches must not inherit the triggering candidate's rewrite attribution."""
    if provenance and provenance.get('source_sha256') and provenance['source_sha256'] != source_hash:
        return {'kind':'inherited repair context','origin':provenance,
                'note':'The recorded rewrite source hash differs from this candidate; do not mine it as a direct rewrite.'}
    return provenance


def _settings(proj, fn):
    cfg = json.loads(proj['config'])
    if cfg.get('kind') == 'fork':
        # The build system owns per-file flags/includes; a global compiler name cannot stand in for them.
        return {'mode': 'project', 'platform': cfg.get('platform'), 'declared_compiler': cfg.get('compiler'),
                'version': cfg.get('version'), 'unit': fn['unit'], 'per_file_flags_recorded': False}
    return {'mode': 'rom', 'platform': cfg.get('platform'), 'compiler': compiler_for(cfg, fn['vram'], fn) if cfg.get('compiler') else None,
            'settings_source': 'project configuration at recording; temporary compile overrides may differ',
            'gp': cfg.get('gp'), 'big_endian': bool(proj['big_endian']),
            'rom': {key: cfg.get('rom', {}).get(key) for key in ('sha1', 'game_code', 'region', 'byte_order')}}



def _same_recheck(old, new):
    """Compare frozen judging inputs and repair attribution, ignoring fresh record IDs."""
    from .repair_review import identity
    def failure(evidence, key):
        row = evidence.get(key) or {}
        return tuple(row.get(field) for field in ('source_sha256', 'score', 'settings'))
    return (identity({'evidence': old}) == identity({'evidence': new})
            and old.get('via') == new.get('via')
            and old.get('provenance') == new.get('provenance')
            and failure(old, 'before') == failure(new, 'before')
            and failure(old, 'previous') == failure(new, 'previous'))


class RepairFeedback:
    def __init__(self, net):
        self.net, self.store = net, net.store
        with self.store.tx() as db:
            for statement in SCHEMA:
                db.execute(statement)
            from .repair_corpus import SCHEMA as corpus_schema
            db.execute(corpus_schema)
            from .payload_storage import install as install_payloads
            install_payloads(db,'repair_examples')
            from .large_text_storage import install as install_large
            install_large(db, ('repair_attempt_settings','repair_attempt_provenance'))
            self.store._track_reads('repair_examples', 'repair_profiles', 'evidence,project_id')
            from .repair_profile_index import install
            install(db)
            # Older automatic captures predate their implemented default. Preserve deliberate
            # reviews and agent annotations; only move untouched, provenance-backed captures.
            db.execute("UPDATE repair_examples SET status='implemented',review_note=? WHERE status='new' "
                       "AND explanation='' AND automation='' AND review_note='' "
                       "AND json_extract(evidence,'$.provenance.kind')='deterministic draft repair' "
                       "AND (json_extract(evidence,'$.provenance.source_sha256') IS NULL OR "
                       "json_extract(evidence,'$.provenance.source_sha256')=json_extract(evidence,'$.after.source_sha256'))",
                       ('Already produced by an automatic repair; retained for regression review.',))

    def attempt(self, db, fn, attempt_id, context_version, effective_settings=None):
        proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
        settings = _settings(proj, fn)
        if effective_settings and settings['mode']=='rom':
            settings.update(effective_settings)
        if _TRIAL.get() is not None:
            source = db.execute('SELECT source FROM attempts WHERE id=?',(attempt_id,)).fetchone()['source'] or ''
            provenance = validated_provenance(_TRIAL.get(),hashlib.sha256(source.encode()).hexdigest())
            db.execute('INSERT INTO repair_attempt_provenance VALUES(?,?)',(attempt_id,json.dumps(provenance)))
        header = db.execute('SELECT unit,header FROM context_versions WHERE project_id=? AND version=? '
                            "AND unit IN (?, '') ORDER BY (unit=?) DESC LIMIT 1",
                            (fn['project_id'], context_version, fn['unit'] or '', fn['unit'] or '')).fetchone()
        settings['context_unit'] = header['unit'] if header and settings['mode'] == 'rom' else None
        from .app.baseline_status import revision as scoring_revision
        settings['scoring_revision'] = scoring_revision()
        settings['context_sha256'] = hashlib.sha256(header['header'].encode()).hexdigest() if header and settings['mode'] == 'rom' else None
        db.execute('INSERT INTO repair_attempt_settings VALUES(?,?)', (attempt_id, json.dumps(settings)))

    def capture(self, db, fn, source, agent_id, via, context_version, attempt_id=None):
        if fn['status'] == 'matched' or not source:
            return  # polishing an existing match is not a new matching repair
        proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
        before = db.execute('SELECT * FROM attempts WHERE function_id=? AND compiled=1 AND score>0 AND (source_hash IS NOT NULL OR source IS NOT NULL) '
                            'AND (? IS NULL OR id<?) ORDER BY score,id DESC LIMIT 1',
                            (fn['id'], attempt_id, attempt_id)).fetchone()
        old_settings = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',
                                  (before['id'],)).fetchone() if before else None
        recorded = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',
                              (attempt_id,)).fetchone() if attempt_id else None
        current = json.loads(recorded['settings']) if recorded else _settings(proj, fn)
        comparable = db.execute('SELECT a.*,s.settings AS recorded_settings FROM attempts a JOIN repair_attempt_settings s ON s.attempt_id=a.id '
                                'WHERE a.function_id=? AND a.compiled=1 AND a.score>0 AND (? IS NULL OR a.id<?) ORDER BY a.score,a.id DESC LIMIT 100',
                                (fn['id'],attempt_id,attempt_id)).fetchall()
        comparable = next((r for r in comparable if not settings_differences(json.loads(r['recorded_settings']),current)),None)
        if comparable:
            before = comparable
            old_settings = {'settings':comparable['recorded_settings']}
        previous = db.execute('SELECT * FROM attempts WHERE function_id=? AND agent_id=? AND (score IS NULL OR score>0) AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 1',
                              (fn['id'],agent_id,attempt_id,attempt_id)).fetchone()
        def snapshot(row):
            if not row:
                return None
            settings = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',(row['id'],)).fetchone()
            return {'attempt_id':row['id'],'source_sha256':hashlib.sha256((row['source'] or '').encode()).hexdigest(),
                    'score':row['score'],'context_version':row['context_version'],'agent_id':row['agent_id'],
                    'settings':json.loads(settings['settings']) if settings else None}
        provenance = db.execute('SELECT provenance FROM repair_attempt_provenance WHERE attempt_id=?',(attempt_id,)).fetchone()
        meta = json.loads(fn['meta'] or '{}')
        agent = db.execute('SELECT name FROM agents WHERE id=?', (agent_id,)).fetchone() if agent_id else None
        session = None
        if agent and db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
            session = db.execute('SELECT id,helper_id,model,kind FROM helper_sessions WHERE agent=? AND project=? '
                                 'AND ended_at IS NULL ORDER BY id DESC LIMIT 1', (agent['name'], proj['slug'])).fetchone()
        old_source = before['source'] if before else None
        evidence = {
            'function': fn['name'], 'function_id': fn['id'], 'unit': fn['unit'],
            'symbol': meta.get('symbol', fn['name']), 'source_path': meta.get('source'),
            'demangled': meta.get('demangled'),
            'agent': {'name': agent['name'] if agent else None, 'session': dict(session) if session else None},
            'size': fn['size'], 'instructions': fn['n_insns'], 'vram': fn['vram'],
            'target_sha256': hashlib.sha256(fn['target']).hexdigest() if fn['target'] else None,
            'fingerprint': fn['fingerprint'], 'via': via, 'target_asm': fn['asm'],
            'judge': 'objdiff 100%' if current['mode'] == 'project' else 'linked byte-identical',
            'after': {'attempt_id': attempt_id, 'source': None if attempt_id else source,
                      'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                      'context_version': context_version, 'settings': current},
            'before': {'attempt_id': before['id'], 'source_sha256': hashlib.sha256(old_source.encode()).hexdigest(),
                       'score': before['score'],
                       'agent_id': before['agent_id'], 'context_version': before['context_version'],
                       'settings': json.loads(old_settings['settings']) if old_settings else None} if before else None,
            'previous':snapshot(previous),
            'provenance':validated_provenance(json.loads(provenance['provenance']) if provenance else _TRIAL.get(),
                                             hashlib.sha256(source.encode()).hexdigest()),
            'comparable_settings':bool(comparable),
            'comparison': 'closest comparable-settings failed attempt when available, otherwise closest historical failure; previous retains last own failed trial',
        }
        # Progress rechecks can reopen and rematch the same frozen input many times. Keep
        # the original review and avoid scheduling identical analysis again. Imported builds
        # do not yet capture complete per-file inputs, so never suppress their evidence here.
        if current.get('mode') == 'rom' and all(current.get(key) for key in
                ('compiler', 'context_sha256', 'scoring_revision')) and evidence['target_sha256']:
            last = db.execute('SELECT evidence FROM main.repair_examples WHERE function_id=? AND agent_id IS ? '
                              'ORDER BY id DESC LIMIT 1', (fn['id'], agent_id)).fetchone()
            if last and _same_recheck(json.loads(last['evidence']), evidence):
                return
        from .repair_corpus import AUTOMATIC, retain
        if not retain(db,evidence,old_source,source,time.time()):
            return
        # Retain deterministic successes for regression analysis without proposing the installed
        # rule as new work. Agent explanations still cannot mark their own hypotheses implemented.
        attribution=evidence.get('provenance') or {}
        automated = attribution.get('kind') in AUTOMATIC and bool(attribution.get('family'))
        captured = db.execute('INSERT INTO repair_examples(project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence,status,review_note) '
                   'VALUES(?,?,?,?,?,?,?,?,?)', (fn['project_id'], fn['id'], attempt_id, before['id'] if before else None,
                                             agent_id, time.time(), json.dumps(evidence), 'implemented' if automated else 'new',
                                             'Installed deterministic repair, verified by the normal judge.' if automated else ''))

        from .app.repair_outcomes import schedule
        try:schedule(self,captured.lastrowid,evidence['after']['source_sha256'])
        except Exception as error:
            logging.getLogger('decompdecomp').warning('Repair analysis scheduling failed: %s',error)

    def report(self, agent, slug, function, change, category='other', automation='', case_id=0):
        fn = self.net._function(function, slug)
        if category not in CATEGORIES or not change.strip():
            raise NetworkError('Give a short change explanation and a valid repair category: ' + ', '.join(CATEGORIES))
        with self.store.tx() as db:
            row = db.execute('SELECT id FROM repair_examples WHERE project_id=? AND function_id=? AND agent_id=? '
                             'AND (?=0 OR id=?) ORDER BY id DESC LIMIT 1',
                             (fn['project_id'], fn['id'], agent.id, case_id, case_id)).fetchone()
            if not row:
                raise NetworkError('No captured matching repair by you exists for this function. Match it first.')
            db.execute("UPDATE repair_examples SET explanation=?,category=?,automation=?,status='new' WHERE id=?",
                       (change.strip()[:3000], category, automation.strip()[:3000], row['id']))
        return f"Repair example #{row['id']} annotated for review. This does not add or enable an automatic rule."

    def _where(self, slug='', category='', status='', comparable=False):
        clauses, args = ['1=1'], []
        if slug:
            clauses.append('e.project_id=?'); args.append(self.net._project(slug)['id'])
        if category:
            if category not in CATEGORIES:
                raise NetworkError('Unknown repair category.')
            clauses.append('e.category=?'); args.append(category)
        if status:
            if status not in STATES:
                raise NetworkError('Unknown review state.')
            clauses.append('e.status=?'); args.append(status)
        if comparable:
            clauses.append("e.before_attempt_id IS NOT NULL AND json_extract(e.evidence,'$.comparable_settings')=1 AND json_extract(e.evidence,'$.before.source_sha256') != json_extract(e.evidence,'$.after.source_sha256')")
        return ' AND '.join(clauses), args

    def listing(self, slug='', category='', status='', before=0, comparable=False):
        where, args = self._where(slug, category, status, comparable)
        partition = "project_id,category,status,CASE WHEN json_extract(evidence,'$.via')='unit diff' THEN 'build:' || COALESCE(json_extract(evidence,'$.unit'),'') || ':' || CAST(created_at/60 AS INTEGER) ELSE 'function:' || function_id END,CASE WHEN json_extract(evidence,'$.via')!='unit diff' OR json_extract(evidence,'$.via') IS NULL THEN json_extract(evidence,'$.target_sha256') END,CASE WHEN json_extract(evidence,'$.via')!='unit diff' OR json_extract(evidence,'$.via') IS NULL THEN json_extract(evidence,'$.after.source_sha256') END,json_extract(evidence,'$.after.settings'),json_extract(evidence,'$.after.context_version')"
        prefix = ('WITH grouped AS (SELECT e.*,ROW_NUMBER() OVER (PARTITION BY ' + partition +
                  ' ORDER BY id DESC) AS position,COUNT(*) OVER (PARTITION BY ' + partition +
                  ') AS repetitions FROM main.repair_examples e WHERE ' + where + ') ')
        rows = self.store.all(prefix + 'SELECT e.id,e.created_at,e.category,e.explanation,e.automation,e.status,e.repetitions,'
                              "CASE WHEN json_extract(e.evidence,'$.via')='unit diff' THEN 'build report' WHEN e.before_attempt_id IS NULL THEN 'no failed baseline' WHEN json_extract(e.evidence,'$.before.source_sha256')=json_extract(e.evidence,'$.after.source_sha256') THEN 'same source / settings' WHEN json_extract(e.evidence,'$.comparable_settings')=1 THEN 'source repair' ELSE 'settings also changed' END AS evidence_kind,"
                              "(json_extract(e.evidence,'$.provenance.kind') IN ('deterministic draft repair','automatic agent local pass') AND "
                              "(json_extract(e.evidence,'$.provenance.source_sha256') IS NULL OR "
                              "json_extract(e.evidence,'$.provenance.source_sha256')=json_extract(e.evidence,'$.after.source_sha256'))) AS automation_known,"
                              'p.slug,p.name AS project,f.name AS function,a.name AS agent FROM grouped e '
                              'JOIN projects p ON p.id=e.project_id JOIN functions f ON f.id=e.function_id '
                              'LEFT JOIN agents a ON a.id=e.agent_id WHERE e.position=1 '
                              ' AND (?=0 OR e.id<?) ORDER BY e.id DESC LIMIT 26', *args, before, before)
        total = self.store.one(prefix + 'SELECT COUNT(*) AS n FROM grouped e JOIN projects p ON p.id=e.project_id JOIN functions f ON f.id=e.function_id WHERE e.position=1', *args)['n']
        groups = self.store.all('SELECT e.category,e.status,COUNT(*) AS count FROM main.repair_examples e WHERE ' + where +
                                ' GROUP BY e.category,e.status', *args)
        from .app.adaptive_repairs import coverage
        return {'examples': [dict(r) | {'review_group': ('build' if r['evidence_kind']=='build report' else 'first' if r['evidence_kind']=='no failed baseline' else 'context' if r['evidence_kind'] in ('same source / settings','settings also changed') else 'source')} for r in rows[:25]], 'more': len(rows) > 25,
                'total': total, 'groups': [dict(r) for r in groups], 'categories': CATEGORIES, 'states': STATES,
                'metrics': coverage(self.net)}

    def detail(self, slug, case_id, include_source=True):
        where, args = self._where(slug)
        row = self.store.one('SELECT e.*,p.slug,p.name AS project,a.name AS agent FROM repair_examples e '
                             'JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id '
                             'WHERE ' + where + ' AND e.id=?', *args, case_id)
        if row is None:
            raise NetworkError('No repair example exists in this project.')
        result = dict(row); evidence = json.loads(result.pop('evidence'))
        if include_source and row['evidence_archive']:
            evidence=json.loads(self.store.conn.repair_archive.read(row['evidence_archive'],snapshot=True))['evidence']
        evidence['provenance'] = validated_provenance(evidence.get('provenance'),evidence['after'].get('source_sha256'))
        for stage in ('before', 'after', 'previous'):
            value = evidence.get(stage)
            if not value:
                continue
            if include_source:
                aid = row['before_attempt_id'] if stage == 'before' else value.get('attempt_id')
                attempt = self.store.one('SELECT source,function_id FROM attempts WHERE id=?', aid) if aid else None
                valid = attempt and attempt['function_id'] == row['function_id'] and hashlib.sha256(
                    (attempt['source'] or '').encode()).hexdigest() == value.get('source_sha256')
                value['source'] = attempt['source'] if valid else value.get('source')
                unit = (value.get('settings') or {}).get('context_unit')
                if unit is not None:
                    header = self.store.one('SELECT header FROM context_versions WHERE project_id=? AND version=? AND unit=?',
                                            row['project_id'], value['context_version'], unit)
                elif evidence['after']['settings']['mode'] == 'rom':
                    # Context versions are unique within a project, including older attempts without settings snapshots.
                    header = self.store.one('SELECT header FROM context_versions WHERE project_id=? AND version=?',
                                            row['project_id'], value['context_version'])
                else:
                    header = None
                expected = (value.get('settings') or {}).get('context_sha256')
                if header and expected and hashlib.sha256(header['header'].encode()).hexdigest() != expected:
                    header = None
                value['header'] = header['header'] if header else value.get('header')
            else:
                value.pop('source', None)
        worker = evidence.get('worker_baseline')
        if worker:
            if include_source:
                evidence['worker_diff'] = ''.join(difflib.unified_diff((worker.get('source') or '').splitlines(True),
                    (evidence['after'].get('source') or '').splitlines(True),fromfile='worker-before.c',tofile='matched.c'))
            else:
                worker.pop('source', None)
                worker.pop('header', None)
        if not include_source:
            evidence.pop('target_asm', None)
        if include_source:
            from . import repair_changes
            evidence['source_change'] = repair_changes.kind((evidence.get('before') or {}).get('source'),evidence['after'].get('source') or '')
            evidence['source_change_label'] = repair_changes.LABELS[evidence['source_change']]
            old = (evidence.get('before') or {}).get('source') or ''
            new = evidence['after'].get('source') or ''
            if evidence.get('previous') and evidence['previous'].get('source') is not None:
                evidence['previous_diff'] = ''.join(difflib.unified_diff(evidence['previous']['source'].splitlines(True),new.splitlines(True),fromfile='previous.c',tofile='matched.c'))
            evidence['diff'] = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                          fromfile='before.c', tofile='matched.c'))
            from .repair_views import focused
            evidence.update(focused(old, new, evidence.get('symbol') or evidence['function']))
            from .repair_review import classify
            evidence['review_suggestion'] = classify({'evidence': evidence})
        from .app.repair_outcomes import result as outcome
        analysis = outcome(self,case_id,include_source=include_source)
        return result | {'evidence': evidence, 'automatic_analysis':analysis}

    def review(self, slug, case_id, status, category='', note=None):
        if status not in STATES:
            raise NetworkError('Choose a valid review state.')
        self.detail(slug, case_id, include_source=False)
        if category and category not in CATEGORIES:
            raise NetworkError('Choose a valid repair category.')
        with self.store.tx() as db:
            db.execute("UPDATE repair_examples SET status=?, category=CASE WHEN ?='' THEN category ELSE ? END, "
                       'review_note=COALESCE(?,review_note) WHERE id=?',
                       (status, category, category, str(note)[:6000] if note is not None else None, case_id))
        return {'message': 'Review state saved.'}

    def classify_new(self, slug, apply=False):
        """Triage at most 500 untouched examples; existing human reviews and annotations stay intact."""
        from . import repair_review
        where, args = self._where(slug, 'unclassified', 'new')
        rows = self.store.all('SELECT e.id FROM repair_examples e WHERE ' + where +
                              " AND e.review_note='' AND e.explanation='' AND e.automation='' ORDER BY e.id LIMIT 500", *args)
        seen, plans, counts = {}, [], {}
        scope, scope_args = self._where(slug)
        prior = self.store.all("SELECT e.id,e.project_id,json_extract(evidence,'$.target_sha256') AS target,"
                               "json_extract(evidence,'$.after') AS after FROM main.repair_examples e WHERE " + scope +
                               " AND e.status IN ('reviewed','implemented') ORDER BY e.id", *scope_args)
        for row in prior:
            old = {'project_id':row['project_id'], 'evidence':{'target_sha256':row['target'],
                   'after':json.loads(row['after'] or '{}')}}
            key = repair_review.identity(old)
            if key:
                seen.setdefault(key, row['id'])
        for row in rows:
            detail = self.detail(slug, row['id'])
            proposal = repair_review.classify(detail)
            key = repair_review.identity(detail)
            if key in seen:
                proposal.update(status='dismissed', note=f'Duplicate captured evidence of example #{seen[key]}; original retained.')
            elif key:
                seen[key] = row['id']
            proposal['id'] = row['id']
            counts[proposal['category']] = counts.get(proposal['category'], 0) + 1
            plans.append(proposal)
        if apply:
            with self.store.tx() as db:
                for proposal in plans:
                    # A review arriving during the analysis must win over this automatic triage.
                    db.execute("UPDATE repair_examples SET status=?,category=?,review_note=? WHERE id=? AND "
                               "status='new' AND category='unclassified' AND review_note='' AND explanation='' AND automation=''",
                               (proposal['status'], proposal['category'], proposal['note'] + ' Evidence group: ' + proposal['group'] + '.', proposal['id']))
        return {'examples': plans, 'counts': counts, 'applied': bool(apply), 'total': len(plans)}

    def export(self, slug='', category='', status='', include_source=False, comparable=False):
        where, args = self._where(slug, category, status, comparable)
        maximum = self.store.one('SELECT MAX(e.id) AS id FROM repair_examples e WHERE ' + where, *args)['id'] or 0
        yield '{"schema_version":1,"examples":['
        after, first = 0, True
        while True:
            rows = self.store.all('SELECT e.id FROM repair_examples e JOIN projects p ON p.id=e.project_id WHERE ' +
                                  where + ' AND e.id>? AND e.id<=? ORDER BY e.id LIMIT 50', *args, after, maximum)
            if not rows:
                break
            for row in rows:
                yield ('' if first else ',') + json.dumps(self.detail(slug, row['id'], include_source))
                first, after = False, row['id']
        yield ']}'
