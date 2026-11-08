"""Machine-local project directory history and explicit, isolated runtime handoffs."""
import json
import os
import secrets
import sqlite3
import tempfile
import threading
from contextlib import closing, nullcontext
from pathlib import Path

from .. import toolchains
from ..network import NetworkError
from ..writer_gate import WriterGate
from . import edition, updates, window


def registry_path():
    override = os.environ.get('DECOMP_DIRECTORIES_FILE')
    return Path(override).expanduser() if override else toolchains.data_dir() / ('RetroReversing-directories-dev.json' if edition.DEV else 'RetroReversing-directories.json')


def read():
    path = registry_path()
    if not path.exists():return {'directories':[], 'selected':''}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('directories'), list):raise ValueError()
        if any(not isinstance(p,str) or not Path(p).is_absolute() for p in value['directories']):raise ValueError()
        selected = value.get('selected','')
        if not isinstance(selected,str) or (selected and (not Path(selected).is_absolute() or selected not in value['directories'])):raise ValueError()
        return value
    except (ValueError, TypeError):
        raise NetworkError('The remembered project directories file is invalid: '+str(path)) from None


def preferred():
    try:return read().get('selected') or None
    except (NetworkError, OSError):return None  # A damaged history must not prevent access to the default home.


def remember(path, *, selected=False, current=None):
    file = registry_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    with WriterGate(file).hold():
        data = read()
        paths = [str(Path(p).resolve()) for p in (current, path) if p]
        data['directories'] = list(dict.fromkeys(paths+data['directories']))
        if selected:data['selected'] = str(Path(path).resolve())
        fd, temporary = tempfile.mkstemp(prefix=file.name+'.',dir=file.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(data,stream)
            os.replace(temporary,file)
        finally:Path(temporary).unlink(missing_ok=True)


def validate(raw):
    if not isinstance(raw,str) or not raw.strip() or any(c in raw for c in '\0\r\n'):
        raise NetworkError('Choose a project directory.')
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():raise NetworkError('Choose a full directory path.')
    path = path.resolve()
    if not path.is_dir():raise NetworkError('This directory is not available. Connect its drive or choose an existing folder.')
    if any(part.lower()=='.decomp' for part in path.parts):raise NetworkError('Choose the project directory containing .decomp, not its internal app-state folder.')
    internal = path/'.decomp'
    if internal.is_symlink():raise NetworkError('The .decomp app-state directory must not be a symbolic link.')
    # Check access before stopping any work. Never create a missing mount or copy projects.
    fd, probe = tempfile.mkstemp(prefix='.decomp-write-check-',dir=path)
    os.close(fd);Path(probe).unlink()
    database = internal/'decomp.db'
    if database.is_symlink():raise NetworkError('The app database must not be a symbolic link.')
    if database.exists():
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:db.execute('PRAGMA schema_version').fetchone()
        except sqlite3.DatabaseError:
            raise NetworkError('This directory’s app database could not be read. Repair it before switching directories.') from None
    return path


class Controller:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.job = {'state':'idle'}
        self.previous_update = None

    def status(self):
        current = str(self.app.home.parent.resolve())
        paths = list(dict.fromkeys([current,str(edition.default_home().resolve())]+read()['directories']))
        return {'current':current, 'directories':[{'path':p,'available':Path(p).is_dir()} for p in paths], 'job':dict(self.job)}

    def choose(self, raw, switch=False):
        target = validate(raw)
        current = self.app.home.parent.resolve()
        with self.lock:
            if self.job['state'] in ('queued','restarting'):
                if self.job.get('target') == str(target):return self.status()
                raise NetworkError('A directory switch is already in progress.')
            if switch and target != current:
                if self.app.headless:raise NetworkError('Switch project directories in the desktop app.')
                runtime = getattr(self.app,'source_runtime',None)
                if getattr(self.app.jobs,'source_update_pending',False) or self.app.updater.job.get('state') in ('installing','restarting','switching-directory') or (runtime and runtime.job.get('state') in ('preparing','waiting','deferred','updating','restarting')):
                    raise NetworkError('Wait for the app update or restart to finish before switching directories.')
            remember(target,current=current)
            if not switch:return {**self.status(),'chosen':str(target)}
            if target == current:
                remember(target,selected=True)
                return self.status()
            with runtime.lock if runtime else nullcontext(), self.app.updater._lock:
                if runtime and runtime.job.get('state') in ('preparing','waiting','deferred','updating','restarting'):
                    raise NetworkError('Wait for the source runtime update to finish before switching directories.')
                if self.app.updater.job.get('state') in ('installing','restarting','switching-directory'):
                    raise NetworkError('Wait for the current app update to finish before switching directories.')
                self.previous_update = dict(self.app.updater.job)
                self.app.updater.job = {'state':'switching-directory','log':['Switching project directory…'],'error':None}
            self.app.jobs.source_update_pending = True  # Block new jobs during the handoff.
            self.job = {'state':'queued','target':str(target),'message':'Preparing to save work and switch project directory…'}
            try:threading.Thread(target=self.run,args=(target,),daemon=True,name='data-directory-switch').start()
            except (RuntimeError,OSError):
                self.job = {'state':'idle'}
                self.app.jobs.source_update_pending = False
                with self.app.updater._lock:self.app.updater.job = self.previous_update
                raise NetworkError('Could not start the directory switch. Current work continues; retry shortly.') from None
        return self.status()

    def run(self, target):
        stopped = False
        try:
            # Let the initiating request return before closing its window.
            threading.Event().wait(.3)
            internal = target/'.decomp'
            internal.mkdir(exist_ok=True)
            with WriterGate(internal/'server-instance').hold(blocking=False) as admitted:
                if not admitted:raise NetworkError('This directory is already open or starting in another app. Switch to that window first.')
                self.job.update(state='restarting',message='Saving current work and stopping agents and room connections…')
                was_open = window.is_open()
                stopped = True
                self.app.updater.before_restart()
                state_file = self.app.home/'server.json'
                if state_file.is_file():
                    state = json.loads(state_file.read_text())
                    if state.get('secret') == self.app.secret:state_file.unlink()
                # New identity rejects stale window/browser requests aimed at the previous data.
                window.close_app()
                updates.restart(self.app.port,secrets.token_urlsafe(24),was_open,home=target)
        except (OSError,ValueError,NetworkError) as error:
            self.job.update(state='error',message=str(error)+(' Current work may have stopped; quit and reopen the app to resume this directory.' if stopped else ' Current work continues.'))
            self.app.jobs.source_update_pending = False
            if self.previous_update is not None:
                with self.app.updater._lock:self.app.updater.job = self.previous_update
