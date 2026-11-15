"""Resource pressure is not a source failure; compiler scratch follows explicit app storage."""
import errno
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import compiler, network
from decompnet.network import Network, NetworkError
from decompnet.storage_errors import message


def test_workspace_prefers_app_data_and_removes_only_its_private_child(tmp_path,monkeypatch):
    home=tmp_path/'data drive';monkeypatch.setenv('DECOMP_HOME',str(home));monkeypatch.delenv('DECOMP_COMPILER_TMPDIR',raising=False)
    parent=home/'.decomp'/'compiler-tmp';parent.mkdir(parents=True)
    marker=parent/'keep.txt';marker.write_text('unrelated data')
    with compiler._workspace('decompnet-') as name:
        work=Path(name)
        assert work.parent==parent
        assert work.is_dir()
    assert not work.exists() and marker.read_text()=='unrelated data'
    with pytest.raises(RuntimeError):
        with compiler._workspace('decompnet-assembly-') as name:
            work=Path(name)
            raise RuntimeError('fake compiler failed')
    assert not work.exists() and marker.exists()


def test_workspace_override_and_standalone_system_default(tmp_path,monkeypatch):
    monkeypatch.setenv('DECOMP_HOME',str(tmp_path/'app'))
    monkeypatch.setenv('DECOMP_COMPILER_TMPDIR',str(tmp_path/'custom'))
    with compiler._workspace('decompnet-') as work:assert Path(work).parent==tmp_path/'custom'
    monkeypatch.delenv('DECOMP_HOME');monkeypatch.delenv('DECOMP_COMPILER_TMPDIR')
    calls=[]
    original=compiler.tempfile.TemporaryDirectory
    def temporary(**kwargs):calls.append(kwargs);return original(**kwargs)
    monkeypatch.setattr(compiler.tempfile,'TemporaryDirectory',temporary)
    with compiler._workspace('decompnet-') as work:assert Path(work).is_dir()
    assert calls[0]['dir'] is None


def test_unavailable_explicit_parent_does_not_silently_fall_back(tmp_path,monkeypatch):
    file=tmp_path/'not-a-directory';file.write_text('retained')
    monkeypatch.setenv('DECOMP_COMPILER_TMPDIR',str(file))
    calls=[];monkeypatch.setattr(compiler.tempfile,'TemporaryDirectory',lambda **kwargs:calls.append(kwargs))
    with pytest.raises(FileExistsError):
        with compiler._workspace('decompnet-'):pass
    assert not calls and file.read_text()=='retained'


@pytest.mark.parametrize('kind',['c','assembly'])
def test_c_and_assembly_use_the_selected_workspace_without_running_a_compiler(tmp_path,monkeypatch,kind):
    monkeypatch.setenv('DECOMP_HOME',str(tmp_path/'data'));monkeypatch.delenv('DECOMP_COMPILER_TMPDIR',raising=False)
    parent=tmp_path/'data'/'.decomp'/'compiler-tmp';seen=[]
    cfg=compiler.CompilerConfig(['synthetic-cc','-c','{src}','-o','{obj}'],sandbox='none')
    if kind=='c':
        def compile_as(cfg,work,root,source,ext):
            seen.append(work)
            assert work.parent==parent and (work/'include'/'context.h').read_text()=='typedef int sample;'
            return compiler.CompileResult(True,b'object','')
        monkeypatch.setattr(compiler,'_compile_as',compile_as)
        result=compiler.compile_source(cfg,'typedef int sample;','int entry(void){return 1;}')
    else:
        def execute(cfg,work,args,timeout):
            seen.append(work);assert work.parent==parent
            if '-E' in args:(work/'func.s').write_text('.text\n')
            else:(work/'func.o').write_bytes(b'object')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        monkeypatch.setattr(compiler,'_exec',execute)
        result=compiler.compile_assembly(cfg,'','.text\n')
    assert result.ok and result.obj==b'object'
    assert seen and all(not work.exists() for work in seen)


@pytest.mark.parametrize('code,phrase',[(errno.ENOSPC,'disk space'),(errno.EMFILE,'open-file limit'),pytest.param(getattr(errno,'EDQUOT',-1),'quota',marks=pytest.mark.skipif(not hasattr(errno,'EDQUOT'),reason='no host quota errno'))])
def test_resource_failure_does_not_record_or_charge_a_candidate_attempt(tmp_path,monkeypatch,code,phrase):
    net=Network(tmp_path/'app.db');agent=net.authenticate(net.register_agent('solver'))
    cfg={'platform':'n64','compiler':{'cmd':['synthetic-cc','-c','{src}','-o','{obj}'],'sandbox':'none'}}
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,root,config) VALUES('demo',?,?)",(str(tmp_path),json.dumps(cfg))).lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name,n_insns,status,unit) VALUES(?,'entry',4,'open','')",(pid,)).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'','')",(pid,))
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fid,agent.id,time.time(),time.time()+300))
    def full(*args,**kwargs):raise OSError(code,'synthetic host failure','/volume/compiler-tmp/child')
    monkeypatch.setattr(network,'compile_source',full)
    with pytest.raises(NetworkError,match=phrase):net.attempt(agent,'entry','int entry(void){return 1;}','demo')
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==0
    assert net.store.one('SELECT attempts FROM functions WHERE id=?',fid)['attempts']==0
    assert net.store.one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==agent.id


def test_unrelated_os_errors_keep_their_existing_handling():
    assert message(FileNotFoundError(errno.ENOENT,'missing','/unavailable')) is None
    assert message(ValueError('not a storage failure')) is None
