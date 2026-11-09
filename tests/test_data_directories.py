import json
from types import SimpleNamespace

import pytest

from decompnet.app import data_directories as dirs
from decompnet.app import edition, updates
from decompnet.network import NetworkError
from decompnet.writer_gate import WriterGate


@pytest.fixture
def locations(tmp_path,monkeypatch):
    monkeypatch.setenv('DECOMP_DIRECTORIES_FILE',str(tmp_path/'machine/locations.json'))
    current,target=tmp_path/'current',tmp_path/'second directory'
    (current/'.decomp').mkdir(parents=True);target.mkdir()
    return current,target


def test_history_deduplicates_and_explicit_home_overrides_preference(locations,monkeypatch):
    current,target=locations
    dirs.remember(target,current=current)
    assert dirs.preferred() is None
    dirs.remember(target,selected=True,current=current)
    dirs.remember(target,current=current)
    assert dirs.read()['directories']==[str(current),str(target)]
    monkeypatch.delenv('DECOMP_HOME',raising=False)
    assert edition.home_dir()==target
    monkeypatch.setenv('DECOMP_HOME',str(current))
    assert edition.home_dir()==current
    assert 'secret' not in dirs.registry_path().read_text()


def test_registry_is_separate_for_release_and_development(monkeypatch,tmp_path):
    monkeypatch.delenv('DECOMP_DIRECTORIES_FILE',raising=False)
    monkeypatch.setattr(dirs.toolchains,'data_dir',lambda:tmp_path)
    monkeypatch.setattr(edition,'DEV',True);development=dirs.registry_path()
    monkeypatch.setattr(edition,'DEV',False)
    assert dirs.registry_path()!=development
    assert 'RetroReversing' in str(development)


@pytest.mark.parametrize('bad',['relative','/missing/directory',''])
def test_unavailable_directories_are_not_created_or_remembered(locations,bad):
    with pytest.raises(NetworkError):dirs.validate(bad)
    assert not dirs.registry_path().exists()


def test_internal_or_corrupt_database_is_rejected(locations):
    current,target=locations
    with pytest.raises(NetworkError,match='internal'):dirs.validate(str(current/'.decomp'))
    (target/'.decomp').mkdir();database=target/'.decomp/decomp.db'
    database.write_bytes(b'not a database')
    with pytest.raises(NetworkError,match='could not be read'):dirs.validate(str(target))
    assert database.read_bytes()==b'not a database'


def controller(current):
    return dirs.Controller(SimpleNamespace(home=current/'.decomp',port=1234,secret='old-secret',headless=False,
        jobs=SimpleNamespace(source_update_pending=False),updater=SimpleNamespace(job={'state':'idle'},before_restart=lambda:None)))


def test_remember_does_not_stop_work_or_move_files(locations):
    current,target=locations
    (current/'project').write_text('saved work')
    runtime=controller(current)
    info=runtime.choose(str(target))
    assert info['current']==str(current) and info['job']['state']=='idle'
    assert (current/'project').read_text()=='saved work' and not (target/'project').exists()
    assert not runtime.app.jobs.source_update_pending


def test_handoff_checks_target_lock_before_shutdown_and_preserves_preference(locations):
    current,target=locations
    runtime=controller(current);stops=[]
    runtime.app.updater.before_restart=lambda:stops.append(True)
    (target/'.decomp').mkdir()
    with WriterGate(target/'.decomp/server-instance').hold():runtime.run(target)
    assert runtime.job['state']=='error' and 'already open' in runtime.job['message']
    assert not stops and dirs.preferred() is None


def test_handoff_preserves_files_rotates_identity_and_removes_only_own_marker(locations,monkeypatch):
    current,target=locations
    runtime=controller(current);calls=[]
    state=current/'.decomp/server.json';state.write_text(json.dumps({'secret':'old-secret'}))
    (current/'saved.c').write_text('work')
    monkeypatch.setattr(dirs.window,'is_open',lambda:True)
    monkeypatch.setattr(dirs.window,'close_app',lambda:calls.append('close'))
    runtime.app.updater.before_restart=lambda:calls.append('save-stop')
    monkeypatch.setattr(dirs.updates,'restart',lambda port,secret,opened,**kwargs:calls.append((port,secret,opened,kwargs)))
    runtime.run(target)
    assert calls[:2]==['save-stop','close']
    assert calls[2][0]==1234 and calls[2][1]!='old-secret' and calls[2][2]
    assert calls[2][3]=={'home':target}
    assert not state.exists() and (current/'saved.c').read_text()=='work'
    assert dirs.preferred() is None  # The new runtime selects it only after successful startup.


def test_restart_sets_target_without_mutating_current_environment(locations,monkeypatch):
    current,target=locations
    monkeypatch.setenv('DECOMP_HOME',str(current))
    calls=[]
    monkeypatch.setattr(updates.os,'execve',lambda *args:calls.append(args))
    updates.restart(1234,'new-secret',True,home=target)
    environment=calls[0][2]
    assert environment['DECOMP_HOME']==str(target)
    assert json.loads(environment['DECOMP_RESTART'])['switch_home']
    assert dirs.os.environ['DECOMP_HOME']==str(current)


def test_queued_switch_deduplicates_and_reserves_update_admission(locations,monkeypatch):
    import threading
    current,target=locations
    runtime=controller(current)
    runtime.app.updater._lock=threading.Lock()
    starts=[]
    class Thread:
        def __init__(self,**kwargs):self.target=kwargs
        def start(self):starts.append(self.target)
    monkeypatch.setattr(dirs.threading,'Thread',Thread)
    first=runtime.choose(str(target),True)
    assert first['job']['state']=='queued' and runtime.app.jobs.source_update_pending
    assert runtime.app.updater.job['state']=='switching-directory'
    runtime.choose(str(target),True)
    assert len(starts)==1
    with pytest.raises(NetworkError,match='already in progress'):runtime.choose(str(current),True)


def test_missing_saved_drive_is_not_created_on_launch(locations,monkeypatch):
    from decompnet.app import cli
    _,target=locations
    dirs.remember(target,selected=True)
    target.rmdir()
    monkeypatch.delenv('DECOMP_HOME',raising=False)
    monkeypatch.setattr(cli,'use_own_wine_prefix',lambda:None)
    messages=[]
    monkeypatch.setattr(cli.window,'tell_desktop',lambda *args:messages.append(args))
    assert cli.run(SimpleNamespace())==1
    assert messages and not target.exists()
