"""Nested original images are measured together only after all references pass verification."""

import hashlib
import json
from types import SimpleNamespace

import pytest
import yaml

from decompnet import consoles, toolchains
from decompnet.forks import splatnested, splatrefs
from decompnet.forks.drafts import find_asm
from decompnet.forks.project import ForkError, ForkProject

from .test_splatrefs import synthetic_project


def nested(root):
    (root / 'original.z64').write_bytes(b'cartridge')
    cfg = {'options': {'target_path': 'original.z64', 'sha1': hashlib.sha1(b'cartridge').hexdigest()},
           'segments': [[0, 'bin', 'program'], [9]]}
    child = root / 'programs'
    child.mkdir()
    (child / 'Makefile').write_text('extract:\n\tpython split.py startup.$(VERSION).yaml\n'
                                  '\tpython split.py game.$(VERSION).yaml\n')
    for name in ('startup', 'game'):
        (child / (name + '.us.bin')).write_bytes(name.encode())
        (child / (name + '.us.sha1')).write_text(hashlib.sha1(name.encode()).hexdigest())
        (child / (name + '.us.yaml')).write_text(yaml.safe_dump({
            'options': {'target_path': name + '.us.bin', 'base_path': '.'},
            'segments': [{'type': 'code', 'start': 0, 'vram': 0x80000400,
                          'subsegments': [[0, 'asm', 'main'], [8, 'linker', 'data'], [8, 'bin', 'data']]}]}))
    return cfg


def fake_generator(fail=None):
    seen = []
    def generate(project, path, cfg, **kw):
        assert not (project.root / 'objdiff.json').exists()
        assert all(e[1] != 'linker' for e in cfg['segments'][0]['subsegments'])
        data = (project.root / cfg['options']['target_path']).read_bytes()
        seen.append(data)
        if fail == data:
            raise ForkError('verification failed')
        refs = project.root / '.decomp-wtf/objdiff-references'
        (refs / 'objects').mkdir(parents=True)
        (refs / 'objects/main.o').write_bytes(data)
        (refs / 'asm').mkdir()
        (refs / 'asm/main.s').write_text('/* original assembly */')
        (project.root / 'objdiff.json').write_text(json.dumps({'units': [
            {'name': 'asm/main', 'target_path': '.decomp-wtf/objdiff-references/objects/main.o'}]}))
        return True
    return SimpleNamespace(id='n64', generate_objdiff=generate), seen


def test_combines_all_declared_images_and_preserves_version_and_originals(tmp_path):
    cfg = nested(tmp_path)
    console, seen = fake_generator()
    before = {p: p.read_bytes() for p in (tmp_path / 'programs').iterdir() if p.is_file()}
    assert splatnested.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console, version='us')
    result = json.loads((tmp_path / 'objdiff.json').read_text())
    assert result['custom_args'] == ['VERSION=us']
    assert len(result['units']) == 2
    assert set(seen) == {b'game', b'startup'}
    assert {p: p.read_bytes() for p in before} == before
    for unit in result['units']:
        assert not unit.get('base_path')  # Original assembly never masquerades as recovered C.
        assert (tmp_path / unit['target_path']).read_bytes() in seen
        assert find_asm(tmp_path, unit['name'], 'a_function', None).read_text() == '/* original assembly */'
    assert 'Other opaque binary' in result['coverage_notes'][0]
    assert not splatnested.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console, version='us')


def test_a_failed_image_publishes_no_partial_references(tmp_path):
    cfg = nested(tmp_path)
    console, seen = fake_generator(fail=b'startup')
    with pytest.raises(ForkError, match='verification failed'):
        splatnested.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console, version='us')
    assert len(seen) == 2  # Failure after the first successful image still publishes nothing.
    assert not (tmp_path / 'objdiff.json').exists()
    assert not (tmp_path / '.decomp-wtf/objdiff-references').exists()
    assert not list((tmp_path / '.decomp-wtf').iterdir())


@pytest.mark.parametrize('change', ['original', 'image', 'hash', 'escape', 'edition', 'undeclared'])
def test_invalid_or_unselected_images_never_run_the_generator(tmp_path, change):
    cfg = nested(tmp_path)
    if change == 'original':
        (tmp_path / 'original.z64').write_bytes(b'wrong cartridge')
    elif change == 'image':
        (tmp_path / 'programs/game.us.bin').write_bytes(b'changed')
    elif change == 'hash':
        (tmp_path / 'programs/game.us.sha1').unlink()
    elif change == 'escape':
        p = tmp_path / 'programs/game.us.yaml'
        d = yaml.safe_load(p.read_text())
        d['options']['target_path'] = '../original.z64'
        p.write_text(yaml.safe_dump(d))
    elif change == 'edition':
        (tmp_path / 'programs/Makefile').write_text('extract:\n\tpython split.py game.jp.yaml\n')
    else:
        (tmp_path / 'programs/Makefile').write_text('all:\n\techo no split\n')
    console, seen = fake_generator()
    with pytest.raises(ForkError):
        splatnested.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console, version='us')
    assert seen == []
    assert not (tmp_path / 'objdiff.json').exists()


def test_uses_only_explicit_image_targets_in_the_existing_build(tmp_path, monkeypatch):
    cfg = nested(tmp_path)
    (tmp_path / 'programs/game.us.bin').unlink()
    project = ForkProject(tmp_path)
    calls = []
    def run(command, **kw):
        calls.append(command)
        (tmp_path / 'programs/game.us.bin').write_bytes(b'game')
        return True, ''
    monkeypatch.setattr(project, 'run', run)
    console, _ = fake_generator()
    splatnested.generate(project, tmp_path / 'splat.yaml', cfg, console, version='us')
    assert calls == [['make', 'VERSION=us', '--', 'programs/game.us.bin']]


def test_import_setup_generates_the_combined_configuration_automatically(tmp_path, monkeypatch):
    cfg = nested(tmp_path)
    (tmp_path / 'splat.yaml').write_text(yaml.safe_dump(cfg))
    console, seen = fake_generator()
    monkeypatch.setattr(consoles, 'get', lambda *_: console)
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'unpack_disc', lambda *a, **kw: None)
    monkeypatch.setattr(project, 'setup_commands', lambda *a: [['make']])
    monkeypatch.setattr(project, 'run', lambda *a, **kw: (True, 'built'))
    project.setup(version='us', log=lambda *_: None)
    assert len(seen) == 2
    assert len(project.units()) == 2


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('platform', ['n64', 'psx', 'ps2'])
def test_assembly_only_references_need_no_candidate_elf(tmp_path, platform):
    cfg = synthetic_project(tmp_path, platform)
    cfg['segments'][0]['subsegments'][0][1] = 'asm'
    (tmp_path / 'build/program.elf').unlink()
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get(platform))
    result = json.loads((tmp_path / 'objdiff.json').read_text())
    assert len(result['units']) == 1
    assert not result['units'][0].get('base_path')
    from elftools.elf.elffile import ELFFile
    with (tmp_path / result['units'][0]['target_path']).open('rb') as stream:
        assert ELFFile(stream).get_section_by_name('.text').data() == (tmp_path / 'baserom.z64').read_bytes()
