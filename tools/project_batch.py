"""Shared connection and durable project queue for the command-line batch runners."""
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


class Reconnected(Exception):
    """The request may have reached the app; inspect the project before trying again."""


class App:
    def __init__(self, home, interval=5):
        self.home = Path(home).expanduser().resolve()
        self.interval = interval
        self.info = None

    def request(self, path, body=None):
        disconnected = False
        while True:
            try:
                info = json.loads((self.home / '.decomp' / 'server.json').read_text())
                port = info['port']
                changed = self.info is not None and info != self.info
                check = disconnected or changed
                request = urllib.request.Request(
                    f'http://127.0.0.1:{port}{"/api/projects?brief=1" if check else path}',
                    data=json.dumps(body).encode() if body is not None and not check else None,
                    headers={'User-Agent': f"decompdecomp-window/{info['secret']}", 'X-Decomp': '1',
                             'Content-Type': 'application/json'},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.load(response)
                self.info = info
                if check:
                    print('App reconnected; checking the current project.', flush=True)
                    raise Reconnected
                return result
            except urllib.error.HTTPError as error:
                try:
                    detail = json.loads(error.read()).get('error', str(error))
                except (ValueError, AttributeError):
                    detail = str(error)
                if error.code not in (401, 403, 502, 503, 504) and 'shutting down' not in str(detail).lower():
                    raise RuntimeError(detail) from error
            except (OSError, ValueError, KeyError):
                pass
            if not disconnected:
                print('App disconnected or restarting; waiting for it to return.', flush=True)
                disconnected = True
            time.sleep(self.interval)

    def read(self, path):
        while True:
            try:
                return self.request(path)
            except Reconnected:
                continue


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def queue(path, projects, selected):
    """Keep the initial order even if the app changes its recent-project ordering."""
    slugs = [p['slug'] for p in projects if not selected or p['slug'] in selected]
    missing = set(selected or ()) - {p['slug'] for p in projects}
    if missing:
        raise RuntimeError('Unknown projects: ' + ', '.join(sorted(missing)))
    selection = sorted(selected or [])
    if path.exists():
        state = json.loads(path.read_text())
        if state.get('remaining') and state.get('selection') != selection:
            raise RuntimeError(f'{path} contains a different project selection. Finish it or use another --progress-file.')
        if state.get('remaining'):
            return state
    state = {'selection': selection, 'remaining': slugs}
    save(path, state)
    return state


def finish_project(path, state):
    state['remaining'].pop(0)
    save(path, state)
