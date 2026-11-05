import json
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from decompnet.app import source_runtime as runtime


def test_only_child_owned_python_can_update_without_a_server_restart():
    assert runtime.worker_only_changes([
        'src/decompnet/app/permute.py', 'src/decompnet/app/permuter_initialization.py', 'tests/test_permute.py'])
    assert not runtime.worker_only_changes(['src/decompnet/app/permuter_worker.py'])
    assert not runtime.worker_only_changes(['src/decompnet/app/permuter_focus.py'])
    assert not runtime.worker_only_changes(['src/decompnet/app/permute.py', 'src/decompnet/app/engine/core.py'])


def test_permuter_only_pull_replaces_the_worker_and_keeps_the_server(tmp_path, monkeypatch):
    def git(*args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    remote = tmp_path / 'remote.git'
    source = tmp_path / 'source'
    staged = tmp_path / 'staged'
    git('init', '--bare', str(remote))
    git('clone', str(remote), str(source))
    git('config', 'user.email', 'test@example.invalid', cwd=source)
    git('config', 'user.name', 'Test', cwd=source)
    git('switch', '-c', 'main', cwd=source)
    (source / 'src/decompnet/app').mkdir(parents=True)
    (source / 'src/decompnet/app/cli.py').write_text('')
    (source / 'src/decompnet/app/permute.py').write_text('old worker')
    (source / 'pyproject.toml').write_text('')
    git('add', '.', cwd=source)
    git('commit', '-m', 'old', cwd=source)
    git('push', '-u', 'origin', 'main', cwd=source)
    git('clone', '--branch', 'main', str(remote), str(staged))
    git('config', 'user.email', 'test@example.invalid', cwd=staged)
    git('config', 'user.name', 'Test', cwd=staged)
    (staged / 'src/decompnet/app/permute.py').write_text('new worker')
    git('add', '.', cwd=staged)
    git('commit', '-m', 'worker only', cwd=staged)
    git('push', 'origin', 'main', cwd=staged)

    app = make_app(tmp_path)
    app.build_id = 'old'
    steps = []

    @contextmanager
    def code_update():
        steps.append('paused')
        yield
        steps.append('resumed')

    app.net.permuter.code_update = code_update
    app.updater.before_restart = lambda: pytest.fail('server must stay running')
    monkeypatch.setattr(runtime.edition, 'CHECKOUT_ROOT', Path(source))
    monkeypatch.setattr(runtime, 'validate', lambda *args: (source, 'git'))
    manager = runtime.Runtime(app)
    assert manager.update_worker_source(source, staged, 'git', 'example/repo')
    assert steps == ['paused', 'resumed']
    assert (source / 'src/decompnet/app/permute.py').read_text() == 'new worker'
    assert app.build_id == git('rev-parse', '--short=12', 'HEAD', cwd=staged)


def test_worker_only_handoff_uses_the_selected_checkout_not_the_staged_clone(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    manager = runtime.Runtime(app)
    source = tmp_path / 'source'
    staged = tmp_path / 'staged'
    source.mkdir()
    staged.mkdir()
    monkeypatch.setattr(manager, 'prepare', lambda *args: staged)
    monkeypatch.setattr(manager, 'update_worker_source',
                        lambda folder, prepared, git, repo: (folder, prepared) == (source, staged))
    monkeypatch.setattr(manager, 'drain', lambda: pytest.fail('worker-only update must not drain the server'))
    manager.run(source, 'git', 'uv', tmp_path, 'example/repo')
    assert manager.job['state'] == 'idle'


def test_checkout_validation_preserves_local_work(tmp_path):
    import shutil
    if not shutil.which('git'):
        pytest.skip('needs git')
    def git(*args):
        return subprocess.run(['git',*args],cwd=tmp_path,check=True,capture_output=True)
    git('init','-b','main')
    git('config','user.email','test@example.invalid');git('config','user.name','Test')
    (tmp_path/'src/decompnet/app').mkdir(parents=True)
    (tmp_path/'src/decompnet/app/cli.py').write_text('')
    (tmp_path/'pyproject.toml').write_text('')
    git('add','.');git('commit','-m','test')
    git('remote','add','origin','https://github.com/example/decompdecomp.git')
    assert runtime.validate(str(tmp_path),'example/decompdecomp')[0]==tmp_path
    (tmp_path/'pyproject.toml').write_text('local work')
    with pytest.raises(ValueError,match='local changes'):
        runtime.validate(str(tmp_path),'example/decompdecomp')
    assert (tmp_path/'pyproject.toml').read_text()=='local work'
    with pytest.raises(ValueError,match='origin'):
        runtime.validate(str(tmp_path),'another/repo')


def make_app(tmp_path):
    return NS(home=tmp_path/'.decomp',port=12345,secret='test',
              manager=NS(sessions={},source_update_pending=False,_lock=threading.RLock()),jobs=NS(jobs={},source_update_pending=False,_lock=threading.RLock()),
              net=NS(drafter=NS(jobs={}),permuter=NS(jobs={}),twins=NS(jobs={}),autopilots={},progress=NS(activity={})),
              updater=NS(before_restart=lambda:None),settings=NS(data={}))


def test_wait_cancellation_never_changes_checkout_or_stops_work(tmp_path,monkeypatch):
    app=make_app(tmp_path);r=runtime.Runtime(app)
    monkeypatch.setattr(r,'busy',lambda:True)
    monkeypatch.setattr(runtime,'command',lambda *args:pytest.fail('must not update while busy'))
    r.cancel.set();r.run(tmp_path,'git','uv',tmp_path,'example/repo')
    assert r.job['state']=='cancelled'
    assert not app.manager.source_update_pending and not app.jobs.source_update_pending


def test_idle_handoff_checks_environment_before_shutdown_and_preserves_home(tmp_path,monkeypatch):
    app=make_app(tmp_path);r=runtime.Runtime(app);calls=[];envs=[]
    monkeypatch.setattr(r,'prepare',lambda *args: calls.append('prepared') or tmp_path)
    monkeypatch.setattr(runtime,'command',lambda argv,cwd:calls.append(argv))
    monkeypatch.setattr(runtime.os,'chdir',lambda path:None)
    monkeypatch.setattr(runtime.window,'is_open',lambda:True)
    app.updater.before_restart=lambda:calls.append('shutdown')
    def execute(executable,argv,env):
        envs.append((argv,env));raise OSError('test exec')
    monkeypatch.setattr(runtime.os,'execve',execute)
    r.run(tmp_path,'git','uv',tmp_path,'example/repo')
    assert calls[0]=='prepared'
    assert calls[-1]=='shutdown'
    argv,env=envs[0]
    assert env['DECOMP_HOME']==str(tmp_path) and '--no-sync' in argv
    assert json.loads(env['DECOMP_RESTART'])['port']==12345
    assert r.job['state']=='error' and 'test exec' in r.job['message']


def test_update_failure_leaves_current_process_running(tmp_path,monkeypatch):
    app=make_app(tmp_path);r=runtime.Runtime(app)
    monkeypatch.setattr(runtime,'validate',lambda *args:None)
    def fail(*args):raise ValueError('local changes')
    monkeypatch.setattr(runtime,'command',fail)
    app.updater.before_restart=lambda:pytest.fail('must not stop current app')
    r.run(tmp_path,'git','uv',tmp_path,'example/repo')
    assert r.job['state']=='error' and not app.manager.source_update_pending


def test_idle_checks_all_background_work(tmp_path):
    app=make_app(tmp_path);r=runtime.Runtime(app)
    assert not r.busy()
    app.net.permuter.jobs['game']=NS(state='running')
    assert r.busy()
    app.net.permuter.jobs.clear();app.net.progress.activity['game']={'phase':'checking'}
    assert r.busy()


def test_queue_requires_explicit_data_choice_and_keeps_work_running(tmp_path,monkeypatch):
    app=make_app(tmp_path);app.settings.save=lambda:None;app.updater.job={'state':'idle'}
    r=runtime.Runtime(app)
    monkeypatch.setattr(runtime,'validate',lambda *args:(tmp_path,'git'))
    monkeypatch.setattr(runtime,'find_uv',lambda:'uv')
    started=[]
    class Thread:
        def __init__(self,**kwargs):started.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(runtime.threading,'Thread',Thread)
    with pytest.raises(ValueError,match='Choose current'):
        r.start(str(tmp_path),'')
    result=r.start(str(tmp_path),'current')
    assert result['job']['state']=='preparing'
    assert started[0]['args'][3]==tmp_path
    assert not app.jobs.source_update_pending and not app.manager.source_update_pending
    with pytest.raises(ValueError,match='already queued'):
        r.start(str(tmp_path),'current')


def test_source_handoff_blocks_new_agents_before_model_detection():
    from decompnet.app.agents import Manager
    with pytest.raises(ValueError,match='source update'):
        Manager.start(NS(source_update_pending=True),'not-a-model','game')


def test_separate_data_launch_does_not_replace_current_app(tmp_path,monkeypatch):
    from decompnet.app import cli
    app=make_app(tmp_path);r=runtime.Runtime(app);launched=[]
    monkeypatch.setattr(r,'prepare',lambda *args:tmp_path)
    monkeypatch.setattr(cli,'_state',lambda *args:None)
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *args,**kwargs:launched.append((args,kwargs)))
    app.updater.before_restart=lambda:pytest.fail('separate data must not stop the current app')
    r.run(tmp_path,'git','uv',tmp_path/'separate','example/repo')
    assert r.job['state']=='done'
    env=launched[0][1]['env']
    assert env['DECOMP_HOME']==str(tmp_path/'separate') and 'DECOMP_RESTART' not in env


def test_usage_limit_wait_does_not_block_but_live_session_does(tmp_path):
    app = make_app(tmp_path)
    session = NS(state='waiting', proc=NS(poll=lambda: 0), until=9999999999)
    app.manager.sessions['helper'] = session
    r = runtime.Runtime(app)
    assert not r.busy()
    session.proc = NS(poll=lambda: None)
    assert r.busy()  # Never interrupt an active provider process.
    session.proc = None
    session.state = 'working'
    assert r.busy()  # API providers have no subprocess.


def test_continuous_service_parks_and_resumes_without_stopping(tmp_path):
    app = make_app(tmp_path)
    app.net.source_update_pending = True
    state = NS(state='running', stop=threading.Event())
    app.net.permuter.jobs['game'] = state
    thread = threading.Thread(target=runtime.idle_wait, args=(app.net, state, 60))
    thread.start()
    try:
        import time
        until = time.monotonic() + 2
        while state.state != 'update_wait' and time.monotonic() < until:
            threading.Event().wait(.01)
        assert state.state == 'update_wait'
        assert not runtime.Runtime(app).busy()
        assert not state.stop.is_set()
    finally:
        app.net.source_update_pending = False
        thread.join(2)
    assert not thread.is_alive()
    assert state.state == 'running'


def test_failed_update_releases_background_checkpoint(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    app.net.source_update_pending = True
    monkeypatch.setattr(runtime.Runtime, 'prepare', lambda *args: (_ for _ in ()).throw(ValueError('changed')))
    runtime.Runtime(app).run(tmp_path, 'git', 'uv', tmp_path, 'example/repo')
    assert not app.net.source_update_pending



def test_prepare_uses_separate_checkout_and_environment_without_holding_workers(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    r = runtime.Runtime(app)
    calls = []
    def command(argv, cwd):
        assert not app.manager.source_update_pending
        assert not app.jobs.source_update_pending
        calls.append((argv, cwd))
    monkeypatch.setattr(runtime, 'command', command)
    staged = r.prepare(tmp_path, 'git', 'uv', 'example/repo')
    assert staged != tmp_path and staged.parent == app.home / 'source-runtimes'
    assert calls[0][0][:2] == ['git', 'clone']
    assert calls[0][0][-1] == str(staged)
    assert all(cwd == staged for _, cwd in calls[1:])
    assert '--locked' in calls[1][0]
    assert 'import decompnet.app.cli' in calls[2][0]


def test_long_running_job_releases_every_hold_and_retries(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    r = runtime.Runtime(app)
    monkeypatch.setattr(r, 'busy', lambda: True)
    monkeypatch.setattr(runtime, 'DRAIN_SECONDS', 0)
    waits = []
    class Cancel:
        def is_set(self): return False
        def wait(self, seconds):
            waits.append(seconds)
            if seconds == runtime.RETRY_SECONDS:
                assert not app.manager.source_update_pending
                assert not app.jobs.source_update_pending
                assert not app.net.source_update_pending
                assert r.job['state'] == 'deferred'
                return True
            return False
    r.cancel = Cancel()
    assert not r.drain()
    assert waits == [.2, runtime.RETRY_SECONDS]


def test_automatic_source_check_never_holds_work_and_skips_same_commit(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    app.settings.data['source_runtime_auto'] = True
    r = runtime.Runtime(app)
    monkeypatch.setattr(runtime.edition, 'DEV', True)
    monkeypatch.setattr(runtime.edition, 'CHECKOUT_ROOT', tmp_path)
    monkeypatch.setattr(runtime.shutil, 'which', lambda _: 'git')
    monkeypatch.setattr(runtime, 'command', lambda argv, cwd: 'abc refs/heads/main' if 'ls-remote' in argv else 'abc')
    monkeypatch.setattr(r, 'start', lambda *args: pytest.fail('already current'))
    assert not r.check()
    monkeypatch.setattr(runtime, 'command', lambda argv, cwd: 'def refs/heads/main' if 'ls-remote' in argv else 'abc')
    started = []
    monkeypatch.setattr(r, 'start', lambda *args: started.append(args))
    assert r.check() and started == [(str(tmp_path), 'current')]
    assert not app.manager.source_update_pending
    r.job['state'] = 'preparing'
    assert not r.check()


def test_automatic_preference_is_explicit_and_persisted(tmp_path):
    app = make_app(tmp_path)
    saved = []
    app.settings.save = lambda: saved.append(dict(app.settings.data))
    r = runtime.Runtime(app)
    with pytest.raises(ValueError, match='true or false'):
        r.configure('yes')
    assert r.configure(True)['automatic']
    assert saved[-1]['source_runtime_auto'] is True
    assert saved[-1]['check_updates'] is True
    assert not r.configure(False)['automatic']



def test_deferred_update_can_reach_next_checkpoint(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    r = runtime.Runtime(app)
    monkeypatch.setattr(runtime, 'DRAIN_SECONDS', 0)
    busy = iter([True, False])
    monkeypatch.setattr(r, 'busy', lambda: next(busy))
    class Cancel:
        def is_set(self): return False
        def wait(self, seconds):
            if seconds == runtime.RETRY_SECONDS:
                assert not app.manager.source_update_pending
            return False
    r.cancel = Cancel()
    assert r.drain()
    assert app.manager.source_update_pending  # Held only for the imminent restart.
    r.hold(False)


def test_preparation_cancellation_skips_sync_and_launch(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    r = runtime.Runtime(app)
    commands = []
    def command(argv, cwd):
        commands.append(argv)
        r.cancel.set()
    monkeypatch.setattr(runtime, 'command', command)
    app.updater.before_restart = lambda: pytest.fail('cancelled update must not stop work')
    r.run(tmp_path, 'git', 'uv', tmp_path, 'example/repo')
    assert len(commands) == 1 and commands[0][1] == 'clone'
    assert r.job['state'] == 'cancelled'
    assert not app.manager.source_update_pending
