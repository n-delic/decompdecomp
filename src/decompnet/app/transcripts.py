"""Local, paged records of what the app sends to helpers, separate from the short activity feed.

CLI internals (provider system prompts, private reasoning and context compaction) are outside the app.
No environment, command-line authentication or HTTP headers are recorded here.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from ..network import NetworkError


class Transcripts:
    def __init__(self, store, *, catalog=None, sessions=None):
        self.store = store
        self.catalog = store if catalog is None else catalog
        self.sessions = store if sessions is None else sessions
        with store.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS helper_messages('
                       'id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, created_at REAL NOT NULL, '
                       'kind TEXT NOT NULL, text TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_helper_messages_session ON helper_messages(session_id,id)')
            from ..large_text_storage import install
            install(db, ('helper_messages',))
            db.execute('CREATE INDEX IF NOT EXISTS idx_helper_messages_kind ON helper_messages(session_id,kind,id)')
            db.execute('CREATE TABLE IF NOT EXISTS helper_score_events(session_id INTEGER,attempt_id INTEGER, PRIMARY KEY(session_id,attempt_id))')

    def clear_helper(self, helper, project, agent):
        """Forget recorded exchanges while retaining session cost/attribution summaries."""
        exists = self.catalog.one('SELECT 1 FROM helpers WHERE id=?', helper)
        sessions = self.sessions.all('SELECT id FROM helper_sessions WHERE helper_id=? AND project=? AND agent=?',
                                     helper,project,agent)
        if not exists and not sessions:return
        ids = json.dumps([row['id'] for row in sessions])
        with self.store.tx() as db:
            # Removed helper IDs are never recycled. A durable marker also rejects late worker callbacks.
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       ('helper-transcripts-removed:'+str(helper),'1'))
            db.execute('DELETE FROM settings WHERE key=?', ('helper-conversation:'+str(helper),))
            last=db.execute('SELECT COALESCE(MAX(id),0) FROM helper_messages').fetchone()[0]
            # SQLite may reuse a deleted maximum rowid. Keep paged readers/exports and automation cursors monotonic.
            db.execute("INSERT INTO settings VALUES('helper-message-id-floor',?) ON CONFLICT(key) DO UPDATE SET "
                       "value=CAST(MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER)) AS TEXT)",(str(last),))
            db.execute('DELETE FROM helper_messages WHERE session_id IN (SELECT value FROM json_each(?))', (ids,))
            db.execute('DELETE FROM helper_score_events WHERE session_id IN (SELECT value FROM json_each(?))', (ids,))
        if self.sessions is not self.store:
            with self.sessions.tx() as db:
                db.execute('DELETE FROM settings WHERE key=?', ('helper-conversation:'+str(helper),))


    @staticmethod
    def _insert(db, session, created, kind, text):
        db.execute('INSERT INTO helper_messages(id,session_id,created_at,kind,text) '
                   "VALUES((SELECT MAX(COALESCE((SELECT MAX(id) FROM helper_messages),0),"
                   "COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key='helper-message-id-floor'),0))+1),?,?,?,?)",
                   (session,created,kind,text))

    def append(self, session, kind, value, secrets=(), *, helper=None):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[redacted]')
        # Tool payloads can echo credentials too; do not persist common explicit credential fields.
        text = re.sub(r'(?i)("(?:api_key|access_token|refresh_token|authorization|password)"\s*:\s*)"(?:\\.|[^"\\])*"',
                      r'\1"[redacted]"', text)
        text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', text)
        owner = self.sessions.one('SELECT * FROM helper_sessions WHERE id=?', session) if self.sessions.one(
            "SELECT 1 FROM sqlite_master WHERE name='helper_sessions'") else None
        if helper is None and owner:helper = owner['helper_id']
        actor = self.catalog.one('SELECT id FROM agents WHERE name=?', owner['agent']) if owner and kind == 'tool_response' else None
        seen = json.dumps([r['attempt_id'] for r in self.store.all(
            'SELECT attempt_id FROM helper_score_events WHERE session_id=?',session)]) if actor else '[]'
        changes = self.sessions.all(
            'SELECT t.id,t.score_before,t.score,t.created_at,f.name FROM main.attempts t '
            'JOIN main.functions f ON f.id=t.function_id JOIN projects p ON p.id=f.project_id '
            'WHERE p.slug=? AND t.agent_id=? AND t.created_at>=? AND t.score_improved=1 '
            'AND t.id NOT IN (SELECT value FROM json_each(?)) ORDER BY t.id',
            owner['project'],actor['id'],owner['started_at'],seen) if actor else []
        with self.store.tx() as db:
            if db.execute('SELECT 1 FROM settings WHERE key=?',('helper-transcript-session-cleared:'+str(session),)).fetchone():return
            if helper is not None and db.execute('SELECT 1 FROM settings WHERE key=?',
                                                 ('helper-transcripts-removed:'+str(helper),)).fetchone():return
            self._insert(db,session,time.time(),kind,text)
            for change in changes:
                if db.execute('INSERT OR IGNORE INTO helper_score_events VALUES(?,?)', (session,change['id'])).rowcount:
                    self._insert(db,session,change['created_at'],'score_improvement',json.dumps(dict(change)))


    def read(self, slug, helper, session=0, after=0, before=0, whole=False):
        sessions = self.sessions.all('SELECT * FROM helper_sessions WHERE project=? AND helper_id=? AND '
                                  '(?=0 OR id<?) ORDER BY id DESC LIMIT 30', slug, helper, before, before)
        if not session:
            session = sessions[0]['id'] if sessions else 0
        row = self.sessions.one('SELECT * FROM helper_sessions WHERE id=? AND project=? AND helper_id=?',
                             session, slug, helper)
        if session and row is None:
            raise NetworkError('This session does not belong to this project and helper.')
        events = self.store.all('SELECT * FROM helper_messages WHERE session_id=? AND id>? ORDER BY id' + ('' if whole else ' LIMIT 26'),
                               session, max(0, after)) if row else []
        events = [dict(event) for event in events]
        first_prompt = self.store.one("SELECT MIN(id) AS id FROM helper_messages WHERE session_id=? AND kind='prompt'", session)
        # Older CLI sessions logged the app's probe before recording the initial
        # prompt. Retitle only that known ordering; never infer model billing.
        for event in events:
            if not first_prompt or not first_prompt['id'] or event['id'] >= first_prompt['id']:
                continue
            if event['kind'] == 'tool_definitions':
                event['kind'] = 'connection_check_tools'
            elif event['kind'] in {'tool_request','tool_response'}:
                try:
                    if json.loads(event['text']).get('name') == 'project_info':
                        event['kind'] = 'connection_check_' + event['kind']
                except (ValueError, AttributeError):
                    pass
        return {'sessions': [dict(r) for r in sessions], 'session': dict(row) if row else None,
                'events': [dict(r) for r in (events if whole else events[:25])], 'more': not whole and len(events) > 25}

    def export(self, slug, helper):
        """Save every recorded session without the dialog's session/event page limits."""
        sessions = self.sessions.all('SELECT * FROM helper_sessions WHERE project=? AND helper_id=? ORDER BY id',
                                  slug, helper)
        directory = Path.home() / 'Downloads'
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', slug)[:80] or 'project'
        path = directory / f'{name}-agent-{helper}-transcripts-{uuid.uuid4().hex[:8]}.json'
        # Freeze the event boundary so a running agent cannot extend the export indefinitely.
        boundary = self.store.one('SELECT MAX(id) AS last FROM helper_messages')['last'] or 0
        count = 0
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as output:
                output.write(json.dumps({'schema_version': 1, 'project': slug, 'helper_id': helper,
                                         'exported_at': time.time(),
                                         'coverage': 'Recorded app exchanges only; hidden reasoning and CLI internals are not captured.'})[:-1])
                output.write(', "sessions": [')
                for index, session in enumerate(sessions):
                    if index:
                        output.write(',')
                    output.write(json.dumps(dict(session), ensure_ascii=False)[:-1] + ', "events": [')
                    after = 0
                    first = True
                    while True:
                        events = self.store.all('SELECT * FROM helper_messages WHERE session_id=? AND id>? AND id<=? '
                                                'ORDER BY id LIMIT 250', session['id'], after, boundary)
                        if not events:
                            break
                        for event in events:
                            if not first:
                                output.write(',')
                            output.write(json.dumps(dict(event), ensure_ascii=False))
                            first = False
                            count += 1
                        after = events[-1]['id']
                    output.write(']}')
                output.write(']}')
        except OSError as error:
            path.unlink(missing_ok=True)
            raise NetworkError(f'Could not save transcripts in {directory}: {error}. Check that Downloads is writable.') from error
        return {'path': str(path), 'sessions': len(sessions), 'events': count}


class ProjectTranscripts:
    """Resolve a transcript's explicit project before taking its writer lock."""
    def __init__(self, net):
        self.net = net
        self.lock = threading.RLock()
        self.stores = {net.store:Transcripts(net.store)}

    def for_project(self, project):
        from .session_storage import prepare
        store = prepare(self.net, project)
        with self.lock:
            if store not in self.stores:
                from .transcript_storage import TranscriptStore
                path = str(store.path)+'.transcripts.sqlite3'
                migrated = getattr(self.net,'_project_files',None) is not None
                pid = store.one('SELECT id FROM projects')['id'] if migrated else None
                self.stores[store] = Transcripts(TranscriptStore(path,project_namespace=pid,repair_archive=self.net.store.conn.repair_archive,require_existing=migrated), catalog=self.net.store, sessions=store)
            return self.stores[store]

    def append(self, session, kind, value, secrets=(), *, helper=None, project=None):
        if project is not None:
            target = self.for_project(project)
        else:
            # Compatibility for old ID-only callbacks. Session IDs must remain
            # globally unique; ambiguous evidence is rejected rather than guessed.
            targets = []
            for store, _ in self.net.project_stores():
                if store.one("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'") and store.one(
                        'SELECT 1 FROM helper_sessions WHERE id=?', session):
                    targets.append(store)
            if len(targets) > 1:raise NetworkError('This session ID exists in several project databases. Restore a consistent backup.')
            if not targets:return
            with self.lock:
                store = targets[0]
                if store not in self.stores:
                    pid = next(ids[0] for candidate, ids in self.net.project_stores() if candidate is store and ids)
                    self.for_project(pid)
                target = self.stores[store]
        return target.append(session, kind, value, secrets, helper=helper)

    def read(self, slug, *args, **kwargs):return self.for_project(slug).read(slug, *args, **kwargs)
    def export(self, slug, helper):return self.for_project(slug).export(slug, helper)
    def clear_helper(self, helper, project, agent):return self.for_project(project).clear_helper(helper, project, agent)


def service(net):
    lock = net.__dict__.setdefault('_transcript_service_lock', threading.RLock())
    with lock:
        if '_transcript_service' not in net.__dict__:net._transcript_service = ProjectTranscripts(net)
        return net._transcript_service
