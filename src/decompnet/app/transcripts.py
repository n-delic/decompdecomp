"""Local, paged records of what the app sends to helpers, separate from the short activity feed.

CLI internals (provider system prompts, private reasoning and context compaction) are outside the app.
No environment, command-line authentication or HTTP headers are recorded here.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from ..network import NetworkError


class Transcripts:
    def __init__(self, store):
        self.store = store
        with store.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS helper_messages('
                       'id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, created_at REAL NOT NULL, '
                       'kind TEXT NOT NULL, text TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_helper_messages_session ON helper_messages(session_id,id)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_helper_messages_kind ON helper_messages(session_id,kind,id)')
            db.execute('CREATE TABLE IF NOT EXISTS helper_score_events(session_id INTEGER,attempt_id INTEGER, PRIMARY KEY(session_id,attempt_id))')

    def append(self, session, kind, value, secrets=()):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[redacted]')
        # Tool payloads can echo credentials too; do not persist common explicit credential fields.
        text = re.sub(r'(?i)("(?:api_key|access_token|refresh_token|authorization|password)"\s*:\s*)"(?:\\.|[^"\\])*"',
                      r'\1"[redacted]"', text)
        text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', text)
        with self.store.tx() as db:
            db.execute('INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(?,?,?,?)',
                       (session, time.time(), kind, text))
            if kind == 'tool_response' and db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
                changes = db.execute('SELECT t.id,t.score_before,t.score,t.created_at,f.name FROM attempts t '
                                     'JOIN functions f ON f.id=t.function_id JOIN projects p ON p.id=f.project_id '
                                     'JOIN agents a ON a.id=t.agent_id JOIN helper_sessions s ON s.agent=a.name AND s.project=p.slug '
                                     'WHERE s.id=? AND t.created_at>=s.started_at AND t.score_improved=1 '
                                     'AND NOT EXISTS(SELECT 1 FROM helper_score_events e WHERE e.session_id=s.id AND e.attempt_id=t.id) '
                                     'ORDER BY t.id', (session,)).fetchall()
                for change in changes:
                    db.execute('INSERT INTO helper_score_events VALUES(?,?)', (session, change['id']))
                    db.execute('INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(?,?,?,?)',
                               (session, change['created_at'], 'score_improvement', json.dumps(dict(change))))


    def read(self, slug, helper, session=0, after=0, before=0, whole=False):
        sessions = self.store.all('SELECT * FROM helper_sessions WHERE project=? AND helper_id=? AND '
                                  '(?=0 OR id<?) ORDER BY id DESC LIMIT 30', slug, helper, before, before)
        if not session:
            session = sessions[0]['id'] if sessions else 0
        row = self.store.one('SELECT * FROM helper_sessions WHERE id=? AND project=? AND helper_id=?',
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
        sessions = self.store.all('SELECT * FROM helper_sessions WHERE project=? AND helper_id=? ORDER BY id',
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
