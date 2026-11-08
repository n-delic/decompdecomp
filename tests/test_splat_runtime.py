"""An outdated development environment must fail before invoking incompatible Splat arguments."""
from types import SimpleNamespace

import pytest

from decompnet.rom import RomError, split


@pytest.mark.parametrize('version', ['0.36.3','0.50.0','unknown'])
def test_old_splat_explains_dependency_update_without_starting_process(monkeypatch,tmp_path,version):
    monkeypatch.setattr(split.metadata,'version',lambda name:version)
    monkeypatch.setattr(split.subprocess,'Popen',lambda *a,**k:pytest.fail('incompatible Splat must not run'))
    with pytest.raises(RomError,match='Close the app.*uv sync'):
        split._splat(['create_config','--objcopy','/tools/objcopy','game.elf'],tmp_path)


def test_missing_splat_explains_dependency_update(monkeypatch):
    def missing(name):raise split.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(split.metadata,'version',missing)
    with pytest.raises(RomError,match='not installed'):split._check_splat_runtime()


@pytest.mark.parametrize('version',['0.50.1','0.51.0','1.0.0'])
def test_supported_splat_preserves_explicit_objcopy(monkeypatch,tmp_path,version):
    monkeypatch.setattr(split.metadata,'version',lambda name:version)
    commands=[]
    def start(command,**kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0,communicate=lambda **kwargs:('ready',''))
    monkeypatch.setattr(split.subprocess,'Popen',start)
    args=['create_config','--objcopy','/tools/objcopy','game.elf']
    assert split._splat(args,tmp_path)=='ready'
    assert commands[0][-len(args):]==args


def test_real_ps2_elf_import_with_explicit_room_compiler(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from decompnet import toolchains
    from decompnet.app.engine import RomNetwork
    if any(toolchains.installed(name) is None for name in ('ee-gcc-2.96','binutils-mips')):
        pytest.skip('needs PS2 demo compiler and binutils')
    demo=Path(__file__).resolve().parents[1]/'projects/demo-ps2'
    game=demo/'build/SLUS_999.99'
    if not game.exists():subprocess.run([sys.executable,str(demo/'build.py')],check=True)
    before=game.read_bytes()
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    slug=net.import_rom(game,confirmed=True,toolchain='ee-gcc-2.96',flags=['-O2','-G8'],log=lambda *_:None)
    project=net.project_row(slug)
    assert net.cfg(project)['platform']=='ps2'
    assert net.cfg(project)['compiler']['toolchain']=='ee-gcc-2.96'
    assert net.project_store(project['id']).one('SELECT COUNT(*) n FROM functions')['n']>60
    assert game.read_bytes()==before
    from elftools.elf.elffile import ELFFile

    from decompnet.forks import csource
    with (demo/'build/demoq_ps2.elf').open('rb') as stream:
        address=next(symbol['st_value'] for symbol in ELFFile(stream).get_section_by_name('.symtab').iter_symbols()
                     if symbol.name=='clamp_s32')
    fn=net.project_store(project['id']).one('SELECT name FROM functions WHERE vram=?',address)
    original=(demo.parent/'demo-n64/original/game.c').read_text()
    code=csource.get(original,'clamp_s32').replace('clamp_s32(',fn['name']+'(',1)
    agent,_=net.local_agent('runtime-check')
    assert net.attempt(agent,fn['name'],code,slug)['matched']
