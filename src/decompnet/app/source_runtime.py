"""Explicit source-code handoff: drain work before changing files or replacing this process."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from ..hostenv import NO_WINDOW, find_uv
from . import edition, window


def command(argv, cwd):
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0')
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE'):
        env.pop(key,None)
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=600,
                            stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip()[-2000:] or 'Command failed.')
    return result.stdout.strip()


def validate(path, repo):
    path = Path(path).expanduser().resolve()
    if not (path/'.git').exists() or not (path/'src/decompnet/app/cli.py').is_file() or not (path/'pyproject.toml').is_file():
        raise ValueError('Choose a decompdecomp Git checkout containing pyproject.toml and src/decompnet.')
    git = shutil.which('git')
    if not git:
        raise ValueError('Install Git before running from source.')
    remote = command([git,'remote','get-url','origin'],path)
    if remote.removesuffix('.git').rstrip('/') not in (f'https://github.com/{repo}',f'git@github.com:{repo}'):
        raise ValueError('The checkout origin must match the update repository configured in Settings.')
    if command([git,'branch','--show-current'],path) != 'main':
        raise ValueError('Switch the checkout to main first. This action never switches branches or discards work.')
    if command([git,'status','--porcelain'],path):
        raise ValueError('The checkout has local changes. Commit or move them before updating from the app.')
    return path,git


class Runtime:
    def __init__(self, app):
        self.app=app
        self.job={'state':'idle'}
        self.cancel=threading.Event()
        self.lock=threading.Lock()

    def status(self):
        return {'job':dict(self.job), 'path':self.app.settings.data.get('source_runtime_path') or
                (str(edition.CHECKOUT_ROOT) if edition.DEV else ''),
                'home':str(self.app.home.parent), 'source_running':edition.DEV,
                'uv_available':bool(find_uv())}

    def busy(self):
        app=self.app
        if any(s.proc is not None and s.proc.poll() is None or s.state in ('starting','working')
               for s in app.manager.sessions.values()):
            return True
        if any(j['state']=='running' for j in app.jobs.jobs.values()):
            return True
        net=app.net
        for owner in (net.drafter,net.permuter,net.twins):
            if any(s.state=='running' for s in owner.jobs.values()):
                return True
        return any(s.get('state')=='running' for s in net.autopilots.values()) or any(not value.get('paused') for value in list(net.progress.activity.values()))

    def start(self,path,data_mode):
        if data_mode not in ('current','development'):
            raise ValueError('Choose current app data or separate development data.')
        if getattr(getattr(self.app.updater, 'checkout', None), 'job', {}).get('state') == 'cloning':
            raise ValueError('Wait for the source download to finish before launching it.')
        from .updates import REPO, installed
        repo=installed().get('repo') or REPO
        if not path.strip():
            raise ValueError('Choose the full path to a source checkout first.')
        folder,git=validate(path,repo)
        uv=find_uv()
        if not uv:
            raise ValueError('Install uv, then reopen this dialog to run the checkout.')
        target=self.app.home.parent if data_mode=='current' else Path.home()/'Decomp-dev'
        if target.resolve()!=self.app.home.parent.resolve():
            if edition.DEV and folder == edition.CHECKOUT_ROOT.resolve():
                raise ValueError('This app already runs from that checkout. Keep current data, or choose a different checkout for a separate development app.')
            from .cli import _state
            if _state(target/'.decomp/server.json'):
                raise ValueError('The development app is already running. Close it before switching this launcher.')
        with self.lock:
            if self.job['state'] in ('waiting','updating','restarting'):
                raise ValueError('A source handoff is already queued.')
            if self.app.updater.job['state'] in ('installing','restarting'):
                raise ValueError('Wait for the packaged update to finish first.')
            self.cancel.clear()
            self.app.settings.data['source_runtime_path']=str(folder)
            self.app.settings.save()
            self.job={'state':'waiting','message':'Waiting for current sessions and background jobs to finish. New agent sessions and jobs are held.', 'path':str(folder)}
            with self.app.manager._lock, self.app.jobs._lock:
                self.app.manager.source_update_pending=True
                self.app.jobs.source_update_pending=True
        threading.Thread(target=self.run,args=(folder,git,uv,target,repo),daemon=True,name='source-runtime').start()
        return self.status()

    def stop(self):
        if self.job['state']=='waiting':
            self.cancel.set()
        else:
            raise ValueError('Only a waiting handoff can be cancelled. File update or restart has already started.')
        return self.status()

    def run(self,folder,git,uv,target,repo):
        try:
            while self.busy():
                if self.cancel.wait(1):
                    self.job.update(state='cancelled',message='Source handoff cancelled. Normal work can start again.')
                    return
            if self.cancel.is_set():
                self.job.update(state='cancelled',message='Source handoff cancelled.')
                return
            self.app.jobs.source_update_preparing=True
            self.job.update(state='updating',message='Updating clean main checkout and preparing its Python environment…')
            validate(str(folder),repo)  # edits made while waiting must never be overwritten
            command([git,'pull','--ff-only','origin','main'],folder)
            command([uv,'sync','--locked','--python','3.12'],folder)
            # Verify the replacement interpreter/imports before shutting down the current app.
            command([uv,'run','--no-sync','python','-c','import decompnet.app.cli'],folder)
            if target.resolve()!=self.app.home.parent.resolve():
                from .cli import _state
                if _state(target/'.decomp/server.json'):
                    raise ValueError('The development app started while this operation was waiting. Close it and retry.')
                env=dict(os.environ,DECOMP_HOME=str(target))
                for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE','DECOMP_RESTART','DECOMP_PORT'):
                    env.pop(key,None)
                subprocess.Popen([uv,'run','--project',str(folder),'--no-sync','python','-m','decompnet.app.cli'],
                                 cwd=folder,env=env,stdin=subprocess.DEVNULL,creationflags=NO_WINDOW)
                self.job.update(state='done',message=f'Development app launched with separate data at {target}. This app keeps its current data and continues running.')
                return
            self.job.update(state='restarting',message='Source ready. Restarting into the checkout; agents resume with the selected app data.')
            self.app.updater.before_restart()
            env=dict(os.environ,DECOMP_HOME=str(target),DECOMP_RESTART=json.dumps(
                {'port':self.app.port,'secret':self.app.secret,'window':window.is_open()}))
            for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE'):
                env.pop(key,None)
            os.chdir(folder)
            os.execve(uv,[uv,'run','--project',str(folder),'--no-sync','python','-m','decompnet.app.cli','--port',str(self.app.port)],env)
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            self.job.update(state='error',message=str(error) + (' Restart failed after shutdown; reopen decompdecomp from the checkout.' if self.job['state']=='restarting' else ' Current app has not been restarted.'))
        finally:
            self.app.manager.source_update_pending=False
            self.app.jobs.source_update_pending=False
            self.app.jobs.source_update_preparing=False
