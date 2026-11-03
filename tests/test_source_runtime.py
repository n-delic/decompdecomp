import json
import subprocess
import threading
from types import SimpleNamespace as NS

import pytest

from decompnet.app import source_runtime as runtime


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
              manager=NS(sessions={},source_update_pending=True,_lock=threading.RLock()),jobs=NS(jobs={},source_update_pending=True,_lock=threading.RLock()),
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
    monkeypatch.setattr(runtime,'validate',lambda *args:calls.append('validate'))
    monkeypatch.setattr(runtime,'command',lambda argv,cwd:calls.append(argv))
    monkeypatch.setattr(runtime.os,'chdir',lambda path:None)
    monkeypatch.setattr(runtime.window,'is_open',lambda:True)
    app.updater.before_restart=lambda:calls.append('shutdown')
    def execute(executable,argv,env):
        envs.append((argv,env));raise OSError('test exec')
    monkeypatch.setattr(runtime.os,'execve',execute)
    r.run(tmp_path,'git','uv',tmp_path,'example/repo')
    assert calls[1]==['git','pull','--ff-only','origin','main']
    assert '--locked' in calls[2]
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


def test_queue_requires_explicit_data_choice_and_holds_new_work(tmp_path,monkeypatch):
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
    assert result['job']['state']=='waiting'
    assert started[0]['args'][3]==tmp_path
    assert app.jobs.source_update_pending and app.manager.source_update_pending
    with pytest.raises(ValueError,match='already queued'):
        r.start(str(tmp_path),'current')


def test_source_handoff_blocks_new_agents_before_model_detection():
    from decompnet.app.agents import Manager
    with pytest.raises(ValueError,match='source update'):
        Manager.start(NS(source_update_pending=True),'not-a-model','game')


def test_separate_data_launch_does_not_replace_current_app(tmp_path,monkeypatch):
    from decompnet.app import cli
    app=make_app(tmp_path);r=runtime.Runtime(app);launched=[]
    monkeypatch.setattr(runtime,'validate',lambda *args:None)
    monkeypatch.setattr(runtime,'command',lambda *args:None)
    monkeypatch.setattr(cli,'_state',lambda *args:None)
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *args,**kwargs:launched.append((args,kwargs)))
    app.updater.before_restart=lambda:pytest.fail('separate data must not stop the current app')
    r.run(tmp_path,'git','uv',tmp_path/'separate','example/repo')
    assert r.job['state']=='done'
    env=launched[0][1]['env']
    assert env['DECOMP_HOME']==str(tmp_path/'separate') and 'DECOMP_RESTART' not in env
