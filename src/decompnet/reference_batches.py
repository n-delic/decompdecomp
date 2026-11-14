"""Bounded owner-approved recipes, not correspondence discovery or another judge.

A batch pins code, origin and actual target/build context. Every admitted native
trial still goes through ReferenceTrials; changed context needs a new review.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from . import consoles
from .network import NetworkError
from .reference_metadata import ReferenceMetadata
from .reference_trials import ReferenceTrials

MAX_TRIALS = 20
MAX_RECIPES = 200
MAX_BATCHES = 50
MAX_MISSES = 200
MAX_RESPONSE = 256 * 1024
MAX_DISPLAYED_CODE = 192 * 1024
MAX_PLAN = 32 * 1024
TOKEN_TTL = 600
OWNER = secrets.token_hex(16)
HANDLE = re.compile(r'(trial|metadata):([1-9][0-9]{0,18})\Z')
ACTIVE = {'queued', 'running', 'stopping'}
TERMINAL_ITEMS = {'matched', 'nonmatch', 'failed', 'stale', 'cached', 'skipped', 'error', 'interrupted'}
PROOF_KEYS = ('project_id', 'function_id', 'reference_hash', 'source_hash', 'prelude_hash',
              'context_hash', 'db_context_hash', 'project_hash', 'target_hash',
              'unit_source', 'unit_source_hash', 'unit_signature')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _stored_json(value, label, expected):
    try:
        if not isinstance(value, str) or len(value.encode()) > 512 * 1024:
            raise ValueError()
        result = json.loads(value)
        if not isinstance(result, expected):
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError):
        raise NetworkError(f'Private {label} data is invalid; review the history before starting more work.') from None


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}.')
    return value


class ReferenceBatches:
    """Keep private batch/replay state without reserving a queue of functions."""

    def __init__(self, net, trials=None):
        self.net = net
        self.trials = trials if trials is not None else ReferenceTrials(net)
        self._key = secrets.token_bytes(32)
        self._lock = threading.RLock()

    def _project(self, slug):
        # Raw rows: engine getters can sync progress or initialize project state.
        if not isinstance(slug, str) or not slug or len(slug) > 256:
            raise ValueError('Choose a valid local project.')
        project = self.net.store.one('SELECT * FROM projects WHERE slug=?', slug)
        if project is None:
            raise NetworkError('Project no longer exists; choose a current local project.')
        if slug in getattr(self.net, 'rooms', {}) or slug in getattr(self.net, 'links', {}):
            raise NetworkError('Reference batches are unavailable for room hosts and guests.')
        return project

    def _metadata_current(self, project, row):
        if row['status'] != 'applied' or row['target_kind'] != 'function':
            raise NetworkError('Apply and review the explicit function mapping first.')
        try:
            target_id = int(row['target_id'])
            config = json.loads(project['config'])
            target = self.net.store.one('SELECT * FROM functions WHERE id=? AND project_id=?', target_id, project['id'])
            meta = json.loads(target['meta'] or '{}') if target is not None else {}
        except (ValueError, TypeError):
            raise NetworkError('The applied mapping is invalid; review it again.') from None
        if (target is None or not isinstance(meta, dict)
                or row['target_build'] != ReferenceMetadata._project_snapshot(project['config'])
                or row['target_import_build'] != row['import_build']
                or target['unit'] != row['target_unit'] or target['vram'] != row['target_address']
                or target['size'] != row['target_size']):
            raise NetworkError('The applied mapping is stale; review it again.')
        if consoles.of(config).renames_in_report:
            original = meta.get('symbol') or meta.get('real') or ''
            current = target['name'] == row['proposed_name'] and original in {row['target_original'], row['proposed_name']}
        else:
            current = (target['name'] == row['target_name'] and meta.get('symbol') == row['target_original']
                       and meta.get('real') == row['proposed_name'])
        if not current:
            raise NetworkError('The mapped target name changed; review the mapping again.')
        return target_id

    def _recipe(self, project, handle):
        if not isinstance(handle, str) or not (match := HANDLE.fullmatch(handle)):
            raise ValueError('Choose a listed reviewed candidate ID.')
        kind, number = match.group(1), int(match.group(2))
        _int(number, 'candidate ID', 1, 2**63 - 1)
        if kind == 'trial':
            row = self.net.store.one('SELECT * FROM reference_trials WHERE id=? AND project_id=?', number, project['id'])
            if row is None or row['status'] not in {'nonmatch', 'failed', 'stale', 'error'}:
                raise NetworkError('Choose a completed, previously reviewed trial for an open target.')
            try:
                provenance = json.loads(row['provenance'])
                origin, reference_hash = provenance['origin'], provenance['reference_hash']
            except (ValueError, TypeError, KeyError):
                raise NetworkError('The reviewed trial provenance is incomplete; prepare it manually again.') from None
            source, prelude = row['submitted_source'], row['submitted_prelude']
            if (not isinstance(source, str) or not isinstance(prelude, str)
                    or _hash(source) != row['source_hash'] or _hash(prelude) != row['prelude_hash']):
                raise NetworkError('The reviewed code is corrupt; prepare it manually again.')
            _, current = self.trials.preparer._origin_text(project['id'], origin)
            if current['source_id'] != row['source_id'] or current['content_hash'] != reference_hash:
                raise NetworkError('The reviewed reference changed; prepare and review it manually again.')
            options = {'target_id': row['function_id'], 'origin': origin, 'source_override': source, 'prelude': prelude}
            evidence = f'Previously reviewed trial #{number}; correspondence is not proof of identity.'
            identity = {'id': handle, 'target_id': row['function_id'], 'origin': origin,
                        'reference_hash': reference_hash, 'source_hash': row['source_hash'], 'prelude_hash': row['prelude_hash']}
        else:
            row = self.net.store.one('SELECT r.*,i.build import_build FROM metadata_records r '
                'JOIN metadata_imports i ON i.id=r.import_id JOIN reference_sources s ON s.id=i.source_id '
                'WHERE r.record_id=? AND s.project_id=?', number, project['id'])
            if row is None or row['kind'] != 'function' or not row['pseudocode']:
                raise NetworkError('Choose an applied function mapping with full pseudocode.')
            target_id = self._metadata_current(project, row)
            if 'M2C_' in row['pseudocode']:
                raise NetworkError('Review placeholder declarations manually in a single candidate first.')
            options = {'target_id': target_id, 'origin': {'kind': 'metadata', 'record_id': number},
                       'reference_name': row['name'], 'prelude': ''}
            evidence = f'Applied metadata mapping #{number}; correspondence is not proof of identity.'
            identity = {'id': handle, 'target_id': target_id, 'record_hash': row['record_hash'],
                        'mapping': {key: row[key] for key in ('target_kind', 'target_id', 'target_name', 'target_unit',
                            'target_address', 'target_size', 'target_original', 'target_build', 'target_import_build',
                            'old_name', 'proposed_name', 'status')}}
        return options, evidence, _hash(_json(identity))

    def _prepared(self, slug, project, handle):
        options, evidence, recipe_hash = self._recipe(project, handle)
        prepared = self.trials.prepare(slug, **options)
        if not prepared['ready']:
            raise NetworkError('Candidate needs manual source, name or declaration review before batching.')
        if 'source_override' in options and (prepared['source'] != options['source_override']
                                             or prepared['prelude'] != options['prelude']):
            raise NetworkError('The reviewed code needs manual preparation; it was not silently normalized.')
        payload = self.trials.preparer.verify(slug, prepared['plan'], prepared['source'], prepared['prelude'],
                                              check_current=False)
        proof = {key: payload[key] for key in PROOF_KEYS}
        # Row IDs/evidence lineage are NOT cache identity: a batch creates a newer
        # M3 trial for the same exact code, which must still hit the previous miss.
        cache_key = _hash(_json({'proof': proof, 'origin': options['origin']}))
        public = {key: prepared[key] for key in ('target', 'source', 'prelude', 'source_hash', 'prelude_hash',
                                                'provenance', 'context', 'warnings')}
        public.update(id=handle, evidence=evidence)
        signed = {'id': handle, 'recipe_hash': recipe_hash, 'proof': proof, 'target':
                  {key: prepared['target'][key] for key in ('id', 'name', 'unit')}}
        return prepared, signed, public, payload, cache_key

    def preview(self, slug, max_trials=10, max_seconds=900):
        """Read snapshots and current context only; signing does not reserve work."""
        _int(max_trials, 'max_trials', 1, MAX_TRIALS)
        _int(max_seconds, 'max_seconds', 60, 3600)
        project = self._project(slug)
        trials = self.net.store.all('SELECT id,function_id FROM reference_trials WHERE project_id=? '
            "AND status IN ('nonmatch','failed','stale','error') ORDER BY id DESC LIMIT ?", project['id'], MAX_RECIPES)
        metadata = self.net.store.all('SELECT r.record_id,r.target_id,r.status FROM metadata_records r '
            'JOIN metadata_imports i ON i.id=r.import_id JOIN reference_sources s ON s.id=i.source_id '
            "WHERE s.project_id=? AND r.kind='function' AND r.target_kind='function' ORDER BY r.record_id LIMIT ?",
            project['id'], MAX_RECIPES)
        # Repeated attempts on one function must not consume the entire discovery
        # budget and starve other functions. Older recipes need manual selection.
        latest = {}
        for row in trials:
            latest.setdefault(row['function_id'], row['id'])
        handles = [f'trial:{trial_id}' for trial_id in latest.values()]
        handles.extend(f'metadata:{row["record_id"]}' for row in metadata)
        candidates, entries, excluded, targets = [], [], [], set()
        displayed = attempts = 0
        for handle in handles:
            if attempts >= MAX_TRIALS or len(candidates) >= max_trials:
                break
            attempts += 1
            try:
                prepared, signed, public, _, cache_key = self._prepared(slug, project, handle)
                target = signed['proof']['function_id']
                if target in targets:
                    continue
                if self.net.store.one('SELECT 1 FROM reference_batch_misses WHERE project_id=? AND cache_key=?',
                                      project['id'], cache_key):
                    raise NetworkError('This exact code already missed with the same target and context; review changed inputs.')
                size = len(prepared['source'].encode()) + len(prepared['prelude'].encode())
                if displayed + size > MAX_DISPLAYED_CODE:
                    raise NetworkError('Displayed code budget reached; use a smaller batch.')
                result = {'candidates': candidates + [public], 'excluded': excluded}
                if len(_json(result).encode()) > MAX_RESPONSE - MAX_PLAN - 4096:
                    raise NetworkError('Review response budget reached; use a smaller batch.')
                displayed += size
                targets.add(target)
                candidates.append(public)
                entries.append(signed)
            except (NetworkError, ValueError, OSError, RuntimeError) as error:
                if len(excluded) < 50:
                    excluded.append({'id': handle, 'reason': str(error)[:512]})
        now = int(time.time())
        manifest = {'version': 1, 'nonce': secrets.token_hex(16), 'project_id': project['id'], 'issued': now,
                    'expires': now + TOKEN_TTL, 'limits': {'max_trials': max_trials, 'max_seconds': max_seconds}, 'items': entries}
        result = {'plan': self._sign(manifest), 'candidates': candidates, 'excluded': excluded,
                  'limits': manifest['limits'], 'expires': manifest['expires'], 'warnings': [
                      'Reviewed correspondence is not identity; only the existing judge can accept code.',
                      'Context changes, including an earlier accepted source edit, require another review.',
                      'The time budget stops new admissions, not an already admitted native judge.',
                      'Only prior reviewed trials and applied metadata mappings are supported; no name guesses or object importer.']}
        if len(_json(result).encode()) > MAX_RESPONSE:
            raise NetworkError('Batch review exceeds its response budget; use fewer candidates.')
        return result

    def _sign(self, manifest):
        raw = _json(manifest).encode()
        token = base64.urlsafe_b64encode(raw).decode().rstrip('=') + '.' + hmac.new(self._key, raw, hashlib.sha256).hexdigest()
        if len(token.encode()) > MAX_PLAN:
            raise NetworkError('Batch manifest exceeds its bound; use fewer candidates.')
        return token

    def _decode(self, token):
        if not isinstance(token, str) or not 0 < len(token.encode()) <= MAX_PLAN:
            raise ValueError('Use a bounded, freshly reviewed batch plan.')
        try:
            encoded, signature = token.split('.')
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            if not hmac.compare_digest(signature, hmac.new(self._key, raw, hashlib.sha256).hexdigest()):
                raise ValueError()
            value = json.loads(raw)
            if (_json(value).encode() != raw or value['version'] != 1 or len(value['items']) > MAX_TRIALS
                    or not re.fullmatch('[0-9a-f]{32}', value['nonce'])):
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError, AttributeError):
            raise NetworkError('Batch plan is invalid or belongs to an earlier app session; review again.') from None

    def admit(self, slug, plan, candidate_ids, accept_if_verified, allow_progress_copy):
        """Persist exactly one approved subset; a replay never launches another job."""
        if accept_if_verified is not True or allow_progress_copy is not True:
            raise NetworkError('Both explicit batch consent flags must be true.')
        project, manifest = self._project(slug), self._decode(plan)
        if manifest['project_id'] != project['id']:
            raise NetworkError('Batch plan belongs to another project; review this project again.')
        if (not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= manifest['limits']['max_trials']
                or any(not isinstance(value, str) for value in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids)):
            raise ValueError('Select a bounded distinct subset of the reviewed candidate IDs.')
        requested = set(candidate_ids)
        items = [dict(entry, state='queued', trial_id=None, trial_nonce=None, outcome=None, score=None, percent=None,
                      text='', warnings=[]) for entry in manifest['items'] if entry['id'] in requested]
        if len(items) != len(candidate_ids):
            raise NetworkError('Only the reviewed candidate subset can be run.')
        request_hash = _hash(_json({'ids': sorted(candidate_ids), 'limits': manifest['limits']}))
        with self._lock, self.net.store.tx() as db:
            old = db.execute('SELECT * FROM reference_batches WHERE project_id=? AND nonce=?',
                             (project['id'], manifest['nonce'])).fetchone()
            if old:
                if old['request_hash'] != request_hash:
                    raise NetworkError('This batch plan was already used for another subset; review again.')
                return {'created': False, 'batch': self._summary(old), 'job': old['job_id']}
            if manifest['expires'] < time.time():
                raise NetworkError('Batch review expired; review the current candidates again.')
            self._recover(db, project['id'])
            if db.execute("SELECT 1 FROM reference_batches WHERE project_id=? AND state IN ('queued','running','stopping')",
                          (project['id'],)).fetchone():
                raise NetworkError('A reference batch is already active for this project; stop it after its current trial.')
            if db.execute('SELECT COUNT(*) FROM reference_batches WHERE project_id=?', (project['id'],)).fetchone()[0] >= MAX_BATCHES:
                raise NetworkError('Private batch history reached its 50-record limit; no work was admitted.')
            now = time.time()
            batch_id = db.execute('INSERT INTO reference_batches(project_id,nonce,request_hash,owner,state,stop_requested,'
                'max_trials,max_seconds,created_at,updated_at,items,warnings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (project['id'], manifest['nonce'], request_hash, OWNER, 'queued', 0, manifest['limits']['max_trials'],
                 manifest['limits']['max_seconds'], now, now, _json(items), '[]')).lastrowid
            row = db.execute('SELECT * FROM reference_batches WHERE id=?', (batch_id,)).fetchone()
        return {'created': True, 'batch': self._summary(row), 'job': None}

    def attach_job(self, slug, batch_id, job_id):
        """Attach progress identity, never use a missing job as permission to rerun."""
        row = self._row(slug, batch_id)
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 128:
            raise ValueError('Use a valid progress job ID.')
        with self.net.store.tx() as db:
            db.execute('UPDATE reference_batches SET job_id=?,updated_at=? WHERE id=?', (job_id, time.time(), row['id']))
        return self.detail(slug, batch_id)

    def _row(self, slug, batch_id):
        project = self._project(slug)
        _int(batch_id, 'batch ID', 1, 2**63 - 1)
        row = self.net.store.one('SELECT * FROM reference_batches WHERE id=? AND project_id=?', batch_id, project['id'])
        if row is None:
            raise NetworkError('Reference batch no longer exists in this project.')
        return row

    def _save_item(self, batch_id, index, item):
        with self.net.store.tx() as db:
            row = db.execute('SELECT items FROM reference_batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                raise NetworkError('Project or batch was removed; no further trial will be admitted.')
            items = _stored_json(row['items'], 'batch items', list)
            items[index] = item
            db.execute('UPDATE reference_batches SET items=?,updated_at=? WHERE id=?', (_json(items), time.time(), batch_id))

    def execute(self, slug, batch_id, log):
        """Admit one fresh M3 trial at a time; stop and deadlines are checkpoints."""
        project, row = self._project(slug), self._row(slug, batch_id)
        with self._lock, self.net.store.tx() as db:
            current = db.execute('SELECT * FROM reference_batches WHERE id=?', (batch_id,)).fetchone()
            if current['state'] != 'queued' or current['owner'] != OWNER:
                return self.detail(slug, batch_id)
            changed = db.execute("UPDATE reference_batches SET state='running',updated_at=? "
                                 "WHERE id=? AND state='queued' AND owner=?",
                                 (time.time(), batch_id, OWNER)).rowcount
            if changed != 1:
                return self.detail(slug, batch_id)
        items = _stored_json(row['items'], 'batch items', list)
        deadline = row['created_at'] + row['max_seconds']
        end_state = 'done'
        try:
            for index, item in enumerate(items):
                current = self._row(slug, batch_id)
                if current['stop_requested'] or time.time() >= deadline:
                    end_state = 'stopped' if current['stop_requested'] else 'budget_exhausted'
                    for remainder in range(index, len(items)):
                        items[remainder].update(state='skipped', text='Not admitted: stop or time budget reached.')
                        self._save_item(batch_id, remainder, items[remainder])
                    break
                log(f'Reviewed reference trial {index + 1}/{len(items)}: checking current context.')
                try:
                    prepared, signed, _, payload, cache_key = self._prepared(slug, self._project(slug), item['id'])
                    if signed['recipe_hash'] != item['recipe_hash'] or signed['proof'] != item['proof']:
                        raise NetworkError('Approved source, target or context changed; review the remaining candidates again.')
                    if self.net.store.one('SELECT 1 FROM reference_batch_misses WHERE project_id=? AND cache_key=?',
                                          project['id'], cache_key):
                        item.update(state='cached', text='The exact code already missed with this target/context.')
                        self._save_item(batch_id, index, item)
                        continue
                    # This is the admission checkpoint. Once running, stop waits for
                    # M3's ordinary judge/rollback/recording and never interrupts it.
                    current = self._row(slug, batch_id)
                    if current['stop_requested'] or time.time() >= deadline:
                        end_state = 'stopped' if current['stop_requested'] else 'budget_exhausted'
                        for remainder in range(index, len(items)):
                            items[remainder].update(state='skipped', text='Not admitted: stop or time budget reached.')
                            self._save_item(batch_id, remainder, items[remainder])
                        break
                    item.update(state='running', trial_nonce=payload['nonce'],
                                combined_hash=_hash(prepared['prelude'] + '\n' + prepared['source']))
                    self._save_item(batch_id, index, item)
                    outcome = self.trials.execute(slug, prepared['plan'], prepared['source'], prepared['prelude'], True, True)
                    item.update(state=outcome['outcome'], outcome=outcome['outcome'],
                                trial_id=(outcome.get('trial') or {}).get('id'), score=outcome.get('score'),
                                percent=outcome.get('percent'), text=str(outcome.get('text') or '')[:4000],
                                warnings=[str(w)[:512] for w in outcome.get('warnings', [])[:10]])
                    self._save_item(batch_id, index, item)
                    if item['state'] == 'nonmatch' and item['score'] is not None:
                        with self.net.store.tx() as db:
                            db.execute('INSERT OR REPLACE INTO reference_batch_misses(project_id,cache_key,created_at) VALUES(?,?,?)',
                                       (project['id'], cache_key, time.time()))
                            db.execute('DELETE FROM reference_batch_misses WHERE project_id=? AND cache_key NOT IN '
                                '(SELECT cache_key FROM reference_batch_misses WHERE project_id=? ORDER BY created_at DESC LIMIT ?)',
                                (project['id'], project['id'], MAX_MISSES))
                except (NetworkError, ValueError, OSError, RuntimeError) as error:
                    item = self._reconcile_item(project['id'], item)
                    if item['state'] not in {'matched', 'nonmatch', 'failed'}:
                        item.update(state='stale', text=str(error)[:4000])
                    self._save_item(batch_id, index, item)
                log(f'Reviewed reference trial {index + 1}/{len(items)}: {item["state"]}.')
            if self._row(slug, batch_id)['stop_requested']:
                end_state = 'stopped'
        except Exception:  # finish durable state even if unexpected bookkeeping/logging fails after a commit
            self._finish_interrupted(batch_id, project['id'], 'error')
            raise
        with self.net.store.tx() as db:
            db.execute('UPDATE reference_batches SET state=?,updated_at=? WHERE id=?', (end_state, time.time(), batch_id))
        return self.detail(slug, batch_id)

    def stop(self, slug, batch_id):
        """Request a durable checkpoint stop, not cancellation of native work."""
        self._row(slug, batch_id)
        with self.net.store.tx() as db:
            row = db.execute('SELECT * FROM reference_batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                raise NetworkError('Project or batch was removed; no work will be admitted.')
            if row['state'] in ACTIVE:
                # Re-read under the writer lock: execution may have started since
                # the first lookup. Never label an admitted native trial stopped.
                state = 'stopped' if row['state'] == 'queued' else 'stopping'
                items = _stored_json(row['items'], 'batch items', list)
                if state == 'stopped':
                    for item in items:
                        if item['state'] == 'queued':
                            item.update(state='skipped', text='Stopped before admission.')
                db.execute('UPDATE reference_batches SET stop_requested=1,state=?,items=?,updated_at=? WHERE id=?',
                           (state, _json(items), time.time(), batch_id))
        return self.detail(slug, batch_id)

    def history(self, slug, limit=10):
        """Return private bounded summaries, not raw submitted reference code."""
        project = self._project(slug)
        _int(limit, 'limit', 1, MAX_BATCHES)
        rows = self.net.store.all('SELECT * FROM reference_batches WHERE project_id=? ORDER BY id DESC LIMIT ?',
                                  project['id'], limit)
        return {'batches': [self._summary(row) for row in rows]}

    def detail(self, slug, batch_id):
        """Keep committed outcomes visible even if their job or source disappeared."""
        row = self._row(slug, batch_id)
        items = [self._reconcile_item(row['project_id'], item)
                 for item in _stored_json(row['items'], 'batch items', list)]
        summary = self._summary(row, items)
        if row['owner'] != OWNER and row['state'] in ACTIVE:
            summary['state'] = 'interrupted'
        public = [{key: item.get(key) for key in ('id', 'target', 'state', 'trial_id', 'outcome', 'score', 'percent', 'text', 'warnings')}
                  for item in items]
        return {'batch': summary, 'items': public, 'limits': {'max_trials': row['max_trials'], 'max_seconds': row['max_seconds']},
                'warnings': _stored_json(row['warnings'], 'batch warnings', list)}

    def _summary(self, row, items=None):
        items = _stored_json(row['items'], 'batch items', list) if items is None else items
        state = 'interrupted' if row['owner'] != OWNER and row['state'] in ACTIVE else row['state']
        return {'id': row['id'], 'state': state, 'job': row['job_id'], 'stop_requested': bool(row['stop_requested']),
                'created_at': row['created_at'], 'updated_at': row['updated_at'], 'total': len(items),
                'completed': sum(item['state'] in TERMINAL_ITEMS for item in items),
                'matched': sum(item['state'] == 'matched' for item in items)}

    def _reconcile_item(self, project_id, original):
        item = dict(original)
        if not item.get('trial_nonce'):
            return item
        trial = self.net.store.one('SELECT * FROM reference_trials WHERE project_id=? AND nonce=?',
                                   project_id, item['trial_nonce'])
        if trial is not None:
            if trial['status'] != 'running':
                result = _stored_json(trial['result'], 'trial result', dict)
                item.update(state=trial['status'], outcome=trial['status'], trial_id=trial['id'],
                            score=result.get('score'), percent=result.get('percent'), text=str(result.get('text') or '')[:4000],
                            warnings=[str(w)[:512] for w in result.get('warnings', [])[:10]])
                return item
        # Process death/source removal can follow the authoritative transaction but
        # precede batch/M3 bookkeeping. Report committed code even without private
        # history; do not infer acceptance from similarity or score alone.
        fn = self.net.store.one('SELECT f.status,f.matched_source,a.name FROM functions f LEFT JOIN agents a '
            'ON a.id=f.matched_agent_id WHERE f.id=? AND f.project_id=?', item['proof']['function_id'], project_id)
        if (fn and fn['status'] == 'matched' and fn['name'] == 'reference-trial'
                and _hash(fn['matched_source'] or '') in {item['proof']['source_hash'], item.get('combined_hash')}):
            item.update(state='matched', outcome='matched', trial_id=trial['id'] if trial is not None else item.get('trial_id'),
                        score=0, text='The target has a committed match; private bookkeeping was interrupted or removed.',
                        warnings=['Review the interrupted trial details before attributing this match.'])
        return item

    def _recover(self, db, project_id):
        # One app instance owns a home. A new process never resumes old recipes or
        # reuses old M3 nonces; same-process service objects share OWNER.
        rows = db.execute("SELECT * FROM reference_batches WHERE project_id=? AND owner<>? AND state IN ('queued','running','stopping')",
                          (project_id, OWNER)).fetchall()
        for row in rows:
            items = [self._reconcile_item(project_id, item)
                     for item in _stored_json(row['items'], 'batch items', list)]
            for item in items:
                if item['state'] not in TERMINAL_ITEMS:
                    item.update(state='interrupted', text='App session ended; this candidate was not resumed.')
            db.execute("UPDATE reference_batches SET state='interrupted',items=?,updated_at=? WHERE id=?",
                       (_json(items), time.time(), row['id']))

    def _finish_interrupted(self, batch_id, project_id, state):
        with self.net.store.tx() as db:
            row = db.execute('SELECT items FROM reference_batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                return
            items = [self._reconcile_item(project_id, item)
                     for item in _stored_json(row['items'], 'batch items', list)]
            for item in items:
                if item['state'] not in TERMINAL_ITEMS:
                    item.update(state='interrupted', text='Batch ended unexpectedly; this candidate was not resumed.')
            db.execute('UPDATE reference_batches SET state=?,items=?,updated_at=? WHERE id=?',
                       (state, _json(items), time.time(), batch_id))
