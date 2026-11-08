"""Verified Splat references never replace a project's existing configuration."""

import copy
import hashlib
import json
import shlex
from types import SimpleNamespace

import pytest
import yaml
from elftools.elf.elffile import ELFFile

from decompnet import consoles, toolchains
from decompnet.forks import splatrefs
from decompnet.forks.project import ForkError, ForkProject

from .test_ps2_debug import SHF_ALLOC, SHF_EXECINSTR, SHT_PROGBITS, _elf


def config(root):
    (root / 'src').mkdir(exist_ok=True)
    (root / 'build/src').mkdir(parents=True, exist_ok=True)
    (root / 'src/demo.c').write_text('int demo_func(void) { return 8; }')
    (root / 'build/src/demo.o').write_bytes(b'candidate, not a reference')
    return {'options': {'src_path': 'src', 'build_path': 'build', 'platform': 'n64'},
            'segments': [{'type': 'code', 'start': 0, 'vram': 0x80000400,
                          'subsegments': [[0, 'c', 'demo'], {'type': 'bss', 'vram': 0x80000410}]}, [16]]}

def test_plan_keeps_original_rom_addresses_and_candidate_paths(tmp_path):
    result = splatrefs.plan(tmp_path, config(tmp_path))
    assert result == [{'name': 'demo', 'source': tmp_path / 'src/demo.c',
                       'base': tmp_path / 'build/src/demo.o', 'offset': 0, 'vram': 0x80000400,
                       'delta': -0x80000400}]

@pytest.mark.parametrize('change', ['overlay', 'compression', 'unknown', 'duplicate', 'escape', 'missing'])
def test_unsupported_layouts_cannot_publish_partial_configuration(tmp_path, change):
    cfg = config(tmp_path)
    segment = cfg['segments'][0]
    if change == 'overlay':
        segment['exclusive_ram_id'] = 'bank'
    elif change == 'compression':
        segment['compressed'] = True
    elif change == 'unknown':
        segment['subsegments'][0][1] = 'custom_decoder'
    elif change == 'duplicate':
        segment['subsegments'].append([8, 'c', 'demo'])
    elif change == 'escape':
        segment['subsegments'][0][2] = '../../outside'
    else:
        (tmp_path / 'build/src/demo.o').unlink()
    with pytest.raises(ForkError):
        splatrefs.plan(tmp_path, cfg)
    assert not (tmp_path / 'objdiff.json').exists()

def test_verification_rejects_wrong_bytes_addresses_and_unmapped_sections(tmp_path):
    image = b'original image bytes'
    unit = {'delta': -0x80000400}
    path = tmp_path / 'reference.elf'
    for section, address, data in [('.text', 0x80000400, b'changed!'),
                                   ('.text', 0x80000300, image),
                                   ('.text', 0x80000400, image + b'extra'),
                                   ('.other', 0x80000400, image)]:
        path.write_bytes(_elf([(section, SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, address, data, 0)]))
        with pytest.raises(ForkError):
            splatrefs.verify(path, image, unit)
    path.write_bytes(_elf([('.text', SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0x80000400, image, 0)]))
    splatrefs.verify(path, image, unit)

def test_existing_objdiff_is_never_replaced_or_even_read(tmp_path):
    cfg = tmp_path / 'objdiff.json'
    cfg.write_text('user-managed configuration')
    assert not splatrefs.generate(SimpleNamespace(root=tmp_path), tmp_path / 'unused.yaml', {}, None)
    assert cfg.read_text() == 'user-managed configuration'

def test_wrong_rom_is_rejected_before_running_any_tools(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg['sha1'] = '0' * 40
    cfg['options']['target_path'] = 'baserom.z64'
    (tmp_path / 'baserom.z64').write_bytes(b'wrong image')
    monkeypatch.setattr(splatrefs, 'run', lambda *a, **k: pytest.fail('tools must not run'))
    with pytest.raises(ForkError, match='exact ROM'):
        splatrefs.generate(SimpleNamespace(root=tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))

def test_setup_calls_the_generator_only_after_a_successful_build(tmp_path, monkeypatch):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'unpack_disc', lambda *a, **k: None)
    monkeypatch.setattr(project, 'setup_commands', lambda *a: [['make']])
    seen = []
    monkeypatch.setattr(project, 'run', lambda *a, **k: (seen.append('build') or True, 'built'))
    def generate(**kwargs):
        seen.append('generate')
        (tmp_path / 'objdiff.json').write_text('{"units": []}')
    monkeypatch.setattr(project, 'generate_objdiff', generate)
    project.setup(log=lambda *a: None)
    assert seen == ['build', 'generate']
    project.setup(log=lambda *a: None)
    assert seen == ['build', 'generate', 'build']

def synthetic_project(root, platform):
    """Assemble a tiny original program and a deliberately different candidate, without a proprietary compiler."""
    cfg = config(root)
    console = consoles.get(platform)
    cfg['options']['platform'] = platform
    tools = toolchains.require('binutils-mips')
    suffix = '.exe' if (tools / 'mips-ps2-decompals-as.exe').exists() else ''
    assembler, linker = (str(tools / ('mips-ps2-decompals-' + name + suffix)) for name in ['as', 'ld'])
    source = '.set noreorder\n.text\n.globl demo_func\n.type demo_func,@function\ndemo_func:\naddiu $v0,$zero,7\njr $ra\nnop\n.size demo_func,.-demo_func\n'
    (root / 'original.s').write_text(source)
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/original.o', 'original.s'], root, print)
    splatrefs.run([linker, *(flag for flag in console.as_flags if flag in ('-EB', '-EL')), '-Ttext', '0x80000400', '-e', 'demo_func', '-o', 'build/program.elf', 'build/original.o'], root, print)
    with (root / 'build/program.elf').open('rb') as stream:
        image = ELFFile(stream).get_section_by_name('.text').data()
    (root / 'baserom.z64').write_bytes(image)
    (root / 'build/program.z64').write_bytes(image)
    (root / 'candidate.s').write_text(source.replace(',7', ',8'))
    (root / 'Makefile').write_text(
        f'all:\n\t{shlex.quote(assembler)} {" ".join(console.as_flags)} -o build/src/demo.o candidate.s\n')
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/src/demo.o', 'candidate.s'], root, print)
    (root / 'include').mkdir()
    (root / 'include/macro.inc').write_text('.macro glabel name\n.global \\name\n.type \\name,@function\n\\name:\n.endm\n.macro endlabel name\n.size \\name,.-\\name\n.endm\n.macro nonmatching name,size=1\n.endm\n')
    (root / 'include/ld_symbols.h').write_text('#define ORIGINAL_HEADER 1\n')
    cfg['options']['ld_symbol_header_path'] = 'include/ld_symbols.h'
    cfg['segments'][0]['subsegments'] = [[0, 'c', 'demo']]
    cfg['sha1'] = hashlib.sha1(image).hexdigest()
    cfg['options'].update(basename='demo', target_path='baserom.z64', base_path='.', o_as_suffix=True,
                          compiler='IDO' if platform == 'n64' else 'GCC', ld_script_path='demo.ld', symbol_addrs_path=['symbols.txt'])
    (root / 'symbols.txt').write_text('demo_func = 0x80000400; // type:func\n')
    cfg['segments'][-1] = [len(image)]
    (root / 'splat.yaml').write_text(yaml.safe_dump(cfg))
    return cfg

@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('platform', ['n64', 'psx'])
def test_generated_targets_are_rom_derived_and_failed_generation_is_atomic(tmp_path, monkeypatch, platform):
    cfg = synthetic_project(tmp_path, platform)
    source_before = (tmp_path / 'src/demo.c').read_bytes()
    includes_before = {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / 'include').rglob('*') if p.is_file()}
    real_verify = splatrefs.verify
    monkeypatch.setattr(splatrefs, 'verify', lambda *a: (_ for _ in ()).throw(ForkError('injected verification failure')))
    with pytest.raises(ForkError, match='injected verification failure'):
        splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', copy.deepcopy(cfg), consoles.get(platform))
    assert not (tmp_path / 'objdiff.json').exists()
    assert not (tmp_path / '.decomp-wtf/objdiff-references').exists()
    monkeypatch.setattr(splatrefs, 'verify', real_verify)
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get(platform))
    objdiff = json.loads((tmp_path / 'objdiff.json').read_text())
    expected_build = ForkProject(tmp_path).setup_commands()[-1]
    assert [objdiff['custom_make'], *objdiff['custom_args']] == expected_build
    built, message = ForkProject(tmp_path).run(expected_build, log=lambda *_: None)
    assert built, message
    target = tmp_path / objdiff['units'][0]['target_path']
    base = tmp_path / objdiff['units'][0]['base_path']
    with target.open('rb') as stream:
        expected = ELFFile(stream).get_section_by_name('.text').data()
    with base.open('rb') as stream:
        candidate = ELFFile(stream).get_section_by_name('.text').data()
    assert expected != candidate
    assert expected == (tmp_path / 'baserom.z64').read_bytes()
    assert (tmp_path / 'src/demo.c').read_bytes() == source_before
    assert {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / 'include').rglob('*') if p.is_file()} == includes_before
    assert cfg['segments'][0]['subsegments'][0][1] == 'c'
    assert not splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get(platform))

def test_paths_cannot_escape_the_checkout_through_symlinks(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'project'
    root.mkdir()
    try:
        (root / 'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('host does not allow directory symlinks')
    with pytest.raises(ForkError, match='inside this checkout'):
        splatrefs.inside(root, 'linked/reference.o')


@pytest.mark.parametrize('nested', [False, True])
def test_versioned_splat_uses_the_selected_yaml_and_make_arguments(tmp_path, monkeypatch, nested):
    template = ('config/$(VERSION)/$(BASENAME).$(VERSION).yaml' if nested
                else '$(BASENAME).$(VERSION).yaml')
    (tmp_path / 'Makefile').write_text('BASENAME := game\nVERSION ?= us\n'
                                      f'extract:\n\tpython -m splat split {template}\n')
    for version in ('pal', 'us'):
        path = (tmp_path / 'config' / version / f'game.{version}.yaml' if nested
                else tmp_path / f'game.{version}.yaml')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'options:\n  platform: n64\n  target_path: baserom.{version}.z64\n'
            'segments:\n  - type: code\n    start: 0\n    vram: 2147484672\n')
    project = ForkProject(tmp_path)
    seen = []
    monkeypatch.setattr(type(consoles.get('n64')), 'generate_objdiff',
                        lambda self, fp, path, config, **kwargs: seen.append((path.name, kwargs['version'])) or True)
    assert project.generate_objdiff(version='us', log=lambda *_: None)
    assert seen == [('game.us.yaml', 'us')]
    assert project.generate_objdiff(version='pal', log=lambda *_: None)
    assert seen[-1] == ('game.pal.yaml', 'pal')
    with pytest.raises(ForkError, match='several Splat configs'):
        project.generate_objdiff(log=lambda *_: None)
    with pytest.raises(ForkError, match='Could not identify'):
        project.generate_objdiff(version='jp', log=lambda *_: None)


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_generated_objdiff_keeps_versioned_project_make_command(tmp_path, monkeypatch):
    cfg = synthetic_project(tmp_path, 'n64')
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'setup_commands',
                        lambda version=None: [['make', f'VERSION={version}', '-j2']])
    assert splatrefs.generate(project, tmp_path / 'splat.yaml', cfg, consoles.get('n64'),
                              version='us', log=lambda *_: None)
    data = json.loads((tmp_path / 'objdiff.json').read_text())
    assert data['custom_make'] == 'make'
    assert data['custom_args'] == ['VERSION=us', '-j2']
    built, message = project.run([data['custom_make'], *data['custom_args']], log=lambda *_: None)
    assert built, message
