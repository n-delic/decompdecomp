"""Resolved commands and incremental staging never reuse changed compiler inputs."""
import os
from types import SimpleNamespace

import pytest

from decompnet import compile_configuration, compile_sessions, compiler
from tests.test_compile_sessions import builds  # noqa: F401


@pytest.fixture
def configurations(tmp_path,monkeypatch):
    tool=tmp_path/'tool';tool.mkdir()
    manifest=tool/'install.json';manifest.write_text('one')
    executable=tool/'cc';executable.write_text('one')
    tc=SimpleNamespace(local=False,via_vm=False,via_wsl=False,wine=False,requires=(),headers=())
    monkeypatch.setattr(compile_configuration.toolchains,'_registered',lambda tid:tc if tid=='test' else None)
    monkeypatch.setattr(compile_configuration.toolchains,'get',lambda _:tc)
    monkeypatch.setattr(compile_configuration.toolchains,'path',lambda _:tool)
    monkeypatch.setattr(compile_configuration.toolchains,'MANIFEST','install.json')
    monkeypatch.setattr(compile_configuration.toolchains,'runtime_error',lambda _:None)
    calls=[]
    def build(root,cfg):
        calls.append((root,cfg))
        return compiler.CompilerConfig([str(executable),*cfg['compiler'].get('flags',[])],ro_paths=(str(tool),))
    return build,calls,manifest,executable,tc


def test_command_reuse_is_private_and_invalidates_inputs(tmp_path,monkeypatch,configurations):
    build,calls,manifest,executable,_=configurations
    cfg={'compiler':{'toolchain':'test','flags':['-O2']}}
    def resolve(config=cfg):return compile_configuration.resolve(tmp_path,config,build)
    with compile_sessions.session():
        first=resolve();first.cmd.append('caller edit')
        assert 'caller edit' not in resolve().cmd and len(calls)==1
        resolve({'compiler':{'toolchain':'test','flags':['-O1']}});assert len(calls)==2
        monkeypatch.setenv('SYNTHETIC_COMPILER_ENV','changed');resolve();assert len(calls)==3
        stamp=manifest.stat();manifest.write_text('two')
        os.utime(manifest,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        resolve();assert len(calls)==4
        executable.write_text('changed');resolve();assert len(calls)==5
        manifest.unlink();resolve();resolve();assert len(calls)==7
    resolve();resolve();assert len(calls)==9


@pytest.mark.parametrize('mode',['local','via_vm','via_wsl','wine','custom','unknown'])
def test_dynamic_runtimes_and_custom_commands_are_not_cached(tmp_path,configurations,mode):
    build,calls,_,_,tc=configurations
    c={'toolchain':'test'}
    if mode=='custom':c['cmd']=['custom']
    elif mode=='unknown':c['toolchain']='user-kit'
    else:setattr(tc,mode,True)
    with compile_sessions.session():
        for _ in range(2):compile_configuration.resolve(tmp_path,{'compiler':c},build)
    assert len(calls)==2


def test_configuration_cache_is_bounded(tmp_path,configurations):
    build,_,_,_,_=configurations
    with compile_sessions.session() as session:
        for index in range(20):
            compile_configuration.resolve(tmp_path,{'compiler':{'toolchain':'test','flags':[str(index)]}},build)
        assert len(session.configurations)==16


def test_context_updates_do_not_rewrite_untouched_headers(builds):  # noqa: F811
    cfg,calls,_=builds
    with compile_sessions.session():
        compiler.compile_source(cfg,'int one;','int entry(){return 1;}')
        header=calls[0][0]/'include/types.h';stamp=header.stat().st_mtime_ns
        compiler.compile_source(cfg,'int two;','int entry(){return 2;}')
        assert header.stat().st_mtime_ns==stamp
        assert (header.parent/'context.h').read_text()=='int two;'


def test_same_size_timestamp_preserving_edits_and_stage_corruption_are_seen(builds):  # noqa: F811
    cfg,calls,include=builds
    with compile_sessions.session() as session:
        compiler.compile_source(cfg,'','int entry(){return 1;}')
        path=include/'types.h';stamp=path.stat()
        path.write_bytes(b'typedef int Q;\r\n')
        os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        compiler.compile_source(cfg,'','int entry(){return 1;}')
        assert len(calls)==2
        staged=calls[0][0]/'include/types.h';stamp=staged.stat()
        staged.write_bytes(b'typedef int R;\n')
        os.utime(staged,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        session.prepare(cfg,'')
        assert staged.read_bytes()==b'typedef int Q;\n'


def test_incremental_staging_handles_file_directory_swaps_and_symlinks(builds,tmp_path):  # noqa: F811
    cfg,_,include=builds
    path=include/'nested';path.write_text('file')
    with compile_sessions.session() as session:
        work=session.prepare(cfg,'')
        path.unlink();path.mkdir();(path/'value.h').write_text('int value;')
        session.prepare(cfg,'')
        assert (work/'include/nested/value.h').read_text()=='int value;'
        other=tmp_path/'outside';other.mkdir();(other/'value.h').write_text('outside')
        import shutil
        shutil.rmtree(work/'include/nested')
        try:(work/'include/nested').symlink_to(other,target_is_directory=True)
        except (OSError,NotImplementedError):pytest.skip('directory symlinks unavailable')
        session.prepare(cfg,'')
        assert not (work/'include/nested').is_symlink()
        assert (other/'value.h').read_text()=='outside'
        (path/'value.h').unlink();path.rmdir();path.write_text('back to file')
        session.prepare(cfg,'')
        assert (work/'include/nested').read_text()=='back to file'


def test_dependency_manifest_changes_invalidate_resolved_commands(tmp_path,monkeypatch,configurations):
    build,calls,_,_,tc=configurations
    tc.requires=('dependency',)
    dependency=tmp_path/'dependency';dependency.mkdir()
    (dependency/'install.json').write_text('one')
    metadata=SimpleNamespace(local=False,via_vm=False,via_wsl=False,wine=False,requires=(),headers=())
    monkeypatch.setattr(compile_configuration.toolchains,'get',lambda tid:tc if tid=='test' else metadata)
    monkeypatch.setattr(compile_configuration.toolchains,'path',lambda tid:tmp_path/'tool' if tid=='test' else dependency)
    cfg={'compiler':{'toolchain':'test'}}
    with compile_sessions.session():
        for _ in range(2):compile_configuration.resolve(tmp_path,cfg,build)
        assert len(calls)==1
        (dependency/'install.json').write_text('two')
        compile_configuration.resolve(tmp_path,cfg,build)
        assert len(calls)==2


def test_project_include_symlink_retarget_invalidates_resolved_paths(tmp_path,configurations):
    build,calls,_,_,_=configurations
    first=tmp_path/'first';first.mkdir()
    second=tmp_path/'second';second.mkdir()
    link=tmp_path/'include'
    try:link.symlink_to(first,target_is_directory=True)
    except (OSError,NotImplementedError):pytest.skip('directory symlinks unavailable')
    cfg={'compiler':{'toolchain':'test','include_dirs':['include']}}
    with compile_sessions.session():
        for _ in range(2):compile_configuration.resolve(tmp_path,cfg,build)
        assert len(calls)==1
        link.unlink();link.symlink_to(second,target_is_directory=True)
        compile_configuration.resolve(tmp_path,cfg,build)
        assert len(calls)==2
