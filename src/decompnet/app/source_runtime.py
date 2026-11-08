"""Prepare source updates separately; use bounded checkpoints before replacing this process."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..hostenv import NO_WINDOW, find_uv
from ..network import NetworkError
from . import edition, window


def command(argv, cwd):
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0')
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE','UV_PROJECT_ENVIRONMENT','UV_PROJECT'):
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


def checkpoint(net, state):
    """Park a continuous service only after its submitted work has joined."""
    while getattr(net, 'source_update_pending', False) and not state.stop.is_set():
        state.state = 'update_wait'
        state.stop.wait(.2)
    if not state.stop.is_set():
        state.state = 'running'


def idle_wait(net, state, seconds):
    """Idle services must notice a queued update without waiting minutes to poll."""
    until = time.monotonic() + seconds
    while not state.stop.is_set() and time.monotonic() < until:
        if getattr(net, 'source_update_pending', False):
            checkpoint(net, state)
            return
        state.stop.wait(min(.2, max(0, until - time.monotonic())))


ACTIVE = ('preparing', 'waiting', 'deferred', 'updating', 'restarting')
RETRY_SECONDS = 60


def worker_only_changes(paths):
    """Only child-owned code can change under a live server; docs/tests are inert."""
    worker = {"src/decompnet/app/permute.py", "src/decompnet/app/permuter_initialization.py",
              "src/decompnet/app/permuter_local.py", "src/decompnet/app/permuter_macro_effects.py",
              "src/decompnet/permuter_gen.py", "src/decompnet/permuter_capture.py", "src/decompnet/permuter_formatting.py"}
    def child_owned(path):
        return path in worker
    return bool(paths) and any(child_owned(path) for path in paths) and all(
        child_owned(path) or path in ("README.md", "ROADMAP.md") or
        path.startswith(("docs/", "tests/")) for path in paths)


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
                'uv_available':bool(find_uv()),
                'automatic':bool(self.app.settings.data.get('source_runtime_auto'))}

    def busy(self):
        app=self.app
        if any(s.proc is not None and s.proc.poll() is None or s.state in ('starting','working')
               for s in app.manager.sessions.values()):
            return True
        if any(j['state']=='running' for j in app.jobs.jobs.values()):
            return True
        net=app.net
        if getattr(net, '_repair_analysis_pending', None):
            return True
        for owner in (net.drafter,net.permuter,net.twins):
            if any(s.state=='running' for s in owner.jobs.values()):
                return True
        return any(s.get('state')=='running' for s in net.autopilots.values()) or any(not value.get('paused') for value in list(net.progress.activity.values()))

    def hold(self, value):
        with self.app.manager._lock, self.app.jobs._lock:
            self.app.net.source_update_pending = value
            self.app.manager.source_update_pending = value
            self.app.jobs.source_update_pending = value

    def configure(self, automatic):
        if not isinstance(automatic, bool):
            raise ValueError('Automatic source updates must be true or false.')
        self.app.settings.data['source_runtime_auto'] = automatic
        if automatic:
            self.app.settings.data['check_updates'] = True
        self.app.settings.save()
        return self.status()

    def check(self):
        """The existing update watcher calls this; never install merely by choosing a path."""
        if not self.app.settings.data.get('source_runtime_auto') or self.job['state'] in ACTIVE:
            return False
        if not edition.DEV:
            return False  # First source launch is always explicit, with its data choice.
        from .updates import REPO, installed
        repo = installed().get('repo') or REPO
        git = shutil.which('git')
        if not git:
            return False
        remote = command([git, 'ls-remote', f'https://github.com/{repo}.git', 'refs/heads/main'], edition.CHECKOUT_ROOT)
        head = command([git, 'rev-parse', 'HEAD'], edition.CHECKOUT_ROOT)
        if remote.split() and remote.split()[0] != head:
            self.start(str(edition.CHECKOUT_ROOT), 'current')
            return True
        return False

    def prepare(self, folder, git, uv, repo):
        # Neither the active checkout nor its environment may change under lazy imports/compiles.
        root = self.app.home / 'source-runtimes'
        root.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix='main-', dir=root))
        steps = [([git, 'clone', '--depth', '1', '--single-branch', '--branch', 'main', '--',
                   f'https://github.com/{repo}.git', str(staged)], folder),
                 ([uv, 'sync', '--locked', '--python', '3.12'], staged),
                 ([uv, 'run', '--no-sync', 'python', '-c', 'import decompnet.app.cli'], staged)]
        for argv, cwd in steps:
            if self.cancel.is_set():
                break
            command(argv, cwd)
        return staged

    def update_worker_source(self, folder, staged, git, repo):
        """Fast-forward child-only code while the server keeps its loaded modules."""
        worker = getattr(self.app.net, "permuter", None)
        if folder.resolve() != edition.CHECKOUT_ROOT.resolve() or not hasattr(worker, "code_update"):
            return False
        validate(str(folder), repo)
        desired = command([git, "rev-parse", "HEAD"], staged)
        command([git, "fetch", "--quiet", "origin", "main"], folder)
        current = command([git, "rev-parse", "HEAD"], folder)
        if current == desired:
            self.job.update(state="done", message="Checkout already current. The app continues running.")
            return True
        if command([git, "merge-base", current, desired], folder) != current:
            return False
        changed = command([git, "diff", "--name-only", current, desired], folder).splitlines()
        if not worker_only_changes(changed):
            return False
        with worker.code_update():
            validate(str(folder), repo)  # work written while preparing cannot be overwritten
            command([git, "merge", "--ff-only", desired], folder)
        self.app.build_id = desired[:12]
        self.job.update(state="done", message="Permuter updated. Its worker restarted; the app and other jobs kept running.")
        return True

    def drain(self):
        """Let new work start while busy; reserve the handoff only when idle."""
        while not self.cancel.is_set():
            if not self.busy():
                self.hold(True)
                if not self.busy():
                    return True
                self.hold(False)
            self.job.update(state='deferred', message='Update ready; active work is continuing. Another restart check in 60 seconds.')
            if self.cancel.wait(RETRY_SECONDS):
                return False
        return False

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
        target=self.app.home.parent if data_mode=='current' else edition.default_home(dev=True)
        if target.resolve()!=self.app.home.parent.resolve():
            if edition.DEV and folder == edition.CHECKOUT_ROOT.resolve():
                raise ValueError('This app already runs from that checkout. Keep current data, or choose a different checkout for a separate development app.')
            from .cli import _state
            if _state(target/'.decomp/server.json'):
                raise ValueError('The development app is already running. Close it before switching this launcher.')
        with self.lock:
            if self.job['state'] in ACTIVE:
                raise ValueError('A source handoff is already queued.')
            if self.app.updater.job['state'] in ('installing','restarting'):
                raise ValueError('Wait for the packaged update to finish first.')
            self.cancel.clear()
            self.app.settings.data['source_runtime_path']=str(folder)
            self.app.settings.save()
            self.job={'state':'preparing','message':'Downloading and checking the update in a separate runtime. Agents and background jobs continue working.', 'path':str(folder)}
        threading.Thread(target=self.run,args=(folder,git,uv,target,repo),daemon=True,name='source-runtime').start()
        return self.status()

    def stop(self):
        if self.job['state'] in ('preparing','waiting','deferred'):
            self.cancel.set()
        else:
            raise ValueError('The restart has already started. Wait for the app to reconnect.')
        return self.status()

    def run(self,folder,git,uv,target,repo):
        try:
            if self.cancel.is_set():
                self.job.update(state='cancelled',message='Update cancelled. Work continues normally.')
                return
            source_folder = folder
            folder = self.prepare(folder, git, uv, repo)
            if self.cancel.is_set():
                self.job.update(state='cancelled',message='Update cancelled. Work continues normally.')
                return
            if target.resolve()!=self.app.home.parent.resolve():
                from .cli import _state
                if _state(target/'.decomp/server.json'):
                    raise ValueError('The development app started while this operation was waiting. Close it and retry.')
                env=dict(os.environ,DECOMP_HOME=str(target))
                for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE','DECOMP_RESTART','DECOMP_PORT','UV_PROJECT_ENVIRONMENT','UV_PROJECT'):
                    env.pop(key,None)
                subprocess.Popen([uv,'run','--project',str(folder),'--no-sync','python','-m','decompnet.app.cli'],
                                 cwd=folder,env=env,stdin=subprocess.DEVNULL,creationflags=NO_WINDOW)
                self.job.update(state='done',message=f'Development app launched with separate data at {target}. This app keeps its current data and continues running.')
                return
            if self.update_worker_source(source_folder, folder, git, repo):
                return
            if not self.drain():
                self.job.update(state='cancelled',message='Update cancelled. Work continues normally.')
                return
            self.app.jobs.source_update_preparing=True
            self.job.update(state='restarting',message='Source ready. Restarting into the checkout; agents resume with the selected app data.')
            self.app.updater.before_restart()
            env=dict(os.environ,DECOMP_HOME=str(target),DECOMP_RESTART=json.dumps(
                {'port':self.app.port,'secret':self.app.secret,'window':window.is_open()}))
            for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','DECOMP_PACKAGE','UV_PROJECT_ENVIRONMENT','UV_PROJECT'):
                env.pop(key,None)
            os.chdir(folder)
            os.execve(uv,[uv,'run','--project',str(folder),'--no-sync','python','-m','decompnet.app.cli','--port',str(self.app.port)],env)
        except (OSError,ValueError,NetworkError,subprocess.SubprocessError) as error:
            self.job.update(state='error',message=str(error) + (' Restart failed after shutdown; reopen decompdecomp from the checkout.' if self.job['state']=='restarting' else ' Current app has not been restarted.'))
        finally:
            self.hold(False)
            self.app.jobs.source_update_preparing=False
