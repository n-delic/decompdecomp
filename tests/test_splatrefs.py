"""Synthetic references must come from the input image, with failed or unsupported imports leaving no config."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from elftools.elf.elffile import ELFFile

from decompnet import consoles, toolchains
from decompnet.forks import objdiff, splatrefs
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
@pytest.mark.parametrize('platform', ['n64', 'psx', 'ps2'])
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


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('object_suffix', ['.c.o', '.o'])
@pytest.mark.parametrize("layout", ["overlay", "implicit-vram", "implicit-vram-padded"])
def test_verified_full_rebuild_snapshots_linked_overlay_objects(tmp_path, object_suffix, layout):
    cfg = synthetic_project(tmp_path, 'n64')
    object_path = 'build/src/demo' + object_suffix
    tools = toolchains.require('binutils-mips')
    assembler = str(tools / 'mips-ps2-decompals-as')
    linker = str(tools / 'mips-ps2-decompals-ld')
    objcopy = str(tools / 'mips-ps2-decompals-objcopy')
    splatrefs.run([assembler, *consoles.get('n64').as_flags, '-o', object_path, 'original.s'], tmp_path, print)
    splatrefs.run([linker, '-EB', '-Ttext', '0x0', '-Map', 'build/program.map', '-e', 'demo_func',
                   '-o', 'build/program.elf', object_path], tmp_path, print)
    splatrefs.run([objcopy, '-O', 'binary', 'build/program.elf', 'baserom.z64'], tmp_path, print)
    if layout == 'implicit-vram-padded':
        splatrefs.run([objcopy, '-O', 'binary', '--gap-fill', '0xff',
                       'build/program.elf', 'baserom.z64'], tmp_path, print)
        with (tmp_path / 'baserom.z64').open('ab') as image:
            image.write(b'\xff' * 32)
    cfg['sha1'] = hashlib.sha1((tmp_path / 'baserom.z64').read_bytes()).hexdigest()
    if layout == 'overlay':
        cfg['segments'].insert(0, {'type': 'code', 'start': 0, 'vram': 0,
                                   'subsegments': [[0, 'textbin', 'microcode']]})
        cfg['segments'][1]['exclusive_ram_id'] = 'overlay'
    else:
        del cfg['segments'][0]['vram']
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    objdiff = json.loads((tmp_path / 'objdiff.json').read_text())
    assert len(objdiff['units']) == 1
    unit = objdiff['units'][0]
    assert (tmp_path / unit['target_path']).read_bytes() == (tmp_path / unit['base_path']).read_bytes()


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_overlay_references_verify_code_even_when_untracked_trailer_differs(tmp_path):
    cfg = synthetic_project(tmp_path, 'n64')
    cfg['segments'][0]['exclusive_ram_id'] = 'overlay'
    original_code = (tmp_path / 'baserom.z64').read_bytes()
    altered = original_code + b'not rebuilt'
    (tmp_path / 'baserom.z64').write_bytes(altered)
    cfg['sha1'] = hashlib.sha1(altered).hexdigest()
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    unit = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    with (tmp_path / unit['target_path']).open('rb') as stream:
        assert ELFFile(stream).get_section_by_name('.text').data() == original_code
    assert (tmp_path / unit['target_path']).read_bytes() != (tmp_path / unit['base_path']).read_bytes()


def test_an_unsupported_console_leaves_generation_to_manual_setup(tmp_path):
    project = ForkProject(tmp_path)
    cfg = config(tmp_path)
    cfg['options']['platform'] = 'unknown-platform'
    (tmp_path / 'splat.yaml').write_text(yaml.safe_dump(cfg))
    assert not project.generate_objdiff()
    assert not (tmp_path / 'objdiff.json').exists()


def test_plan_accepts_source_extension_in_object_name_and_rejects_ambiguity(tmp_path):
    cfg = config(tmp_path)
    short = tmp_path / 'build/src/demo.o'
    long = tmp_path / 'build/src/demo.c.o'
    short.rename(long)
    assert splatrefs.plan(tmp_path, cfg)[0]['base'] == long
    short.write_bytes(b'stale object')
    with pytest.raises(ForkError, match='ambiguous built objects'):
        splatrefs.plan(tmp_path, cfg)


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_assembly_units_are_visible_without_counting_original_assembly_as_c(tmp_path):
    cfg = synthetic_project(tmp_path, 'n64')
    cfg['segments'][0]['subsegments'][0][1] = 'asm'
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, consoles.get('n64'))
    entry = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    assert entry['name'] == 'asm/demo'
    assert 'base_path' not in entry and 'metadata' not in entry
    assert (tmp_path / entry['target_path']).is_file()


@pytest.mark.parametrize('root_elf', [False, True])
@pytest.mark.parametrize('source_tree', [True, False])
def test_verified_rebuild_retains_nested_config_version(tmp_path, monkeypatch, root_elf, source_tree):
    cfg = config(tmp_path)
    data = b'full original cartridge'
    image = tmp_path/'baserom.jp.z64'
    image.write_bytes(data)
    elf = tmp_path / ('demo.elf' if root_elf else 'build/demo.elf')
    if root_elf:
        cfg['options']['elf_path'] = 'demo.elf'
    mapping = elf.with_suffix('.map') if source_tree else tmp_path / (elf.stem + '.map')
    if not source_tree:
        cfg['options']['src_path'] = 'not-created-yet'
    elf.write_bytes(b'linked ELF read by mocked objcopy')
    monkeypatch.setattr(splatrefs, 'binary_span', lambda _: len(data))
    mapping.write_text(' .text 0x80000400 0x10 build/src/demo.o\n')
    (elf.parent / 'unrelated.map').write_text('other image, not this ELF')
    (tmp_path/'asm').mkdir()
    (tmp_path/'build/asm').mkdir()
    (tmp_path/'src/demo.s').write_text('assembly alternative for the same linked C object')
    (tmp_path/'asm/start.s').write_text('glabel boot\n  jr $ra\n  nop\n')
    (tmp_path/'build/asm/start.o').write_bytes(b'linked original assembly object')
    (tmp_path/'asm/microcode.s').write_text('.incbin "program.bin"\n')
    (tmp_path/'build/asm/microcode.o').write_bytes(b'whole program blob')
    with mapping.open('a') as f:
        f.write(' .text 0x80000410 0x8 build/asm/start.o\n .text 0x80000418 0x80 build/asm/microcode.o\n')
    (tmp_path/'shared').mkdir()
    (tmp_path/'shared/common.c').write_text('int shared(void) { return 1; }')
    (tmp_path/'build/shared').mkdir()
    (tmp_path/'build/shared/common.c.o').write_bytes(b'linked shared source object')
    with mapping.open('a') as f:
        f.write(' .text 0x80000500 0x10 build/shared/common.c.o\n')
    elf.touch()

    bindir = tmp_path/'tools'
    bindir.mkdir()
    (bindir/'mips-ps2-decompals-objcopy').touch()
    monkeypatch.setattr(toolchains, 'require', lambda _: bindir)
    def objcopy(cmd, *args, **kwargs):
        # The root ELF needs cartridge FF-fill before its full-image hash can pass.
        Path(cmd[-1]).write_bytes(data if not root_elf or '--gap-fill' in cmd else b'wrong fill')
    monkeypatch.setattr(splatrefs, 'run', objcopy)
    project = ForkProject(tmp_path)
    selected = tmp_path/'config/jp/demo.jp.yaml'
    monkeypatch.setattr(project, '_splat_version_configs', lambda: {'jp':selected})
    assert splatrefs.verified_rebuild(project, selected, cfg, image, hashlib.sha1(data).hexdigest(), lambda *_: None)
    result = json.loads((tmp_path/'objdiff.json').read_text())
    assert result['custom_args'] == ['VERSION=jp']
    asm = next(u for u in result['units'] if u['name'] == 'asm/start')
    assert 'base_path' not in asm and 'source_path' not in asm['metadata']
    assert asm['metadata']['assembly_source'] == 'asm/start.s'
    for unit in result['units']:
        for key in ('name', 'base_path', 'target_path'):
            assert '\\' not in unit.get(key, '')
        for value in unit.get('metadata', {}).values():
            assert '\\' not in value
    assert len(result['units']) == 3
    shared = next(u for u in result['units'] if u['name'] == 'shared/common')
    assert shared['metadata']['source_path'] == 'shared/common.c'
    assert shared['base_path'] == 'build/shared/common.c.o'
    assert '1 linked binary assembly' in result['coverage_notes'][0]


@pytest.mark.parametrize("layout", ["padding", "implicit-vram"])
def test_padding_fallback_rejects_an_unreadable_build(tmp_path, monkeypatch, layout):
    cfg = config(tmp_path)
    image = tmp_path/'baserom.z64'
    image.write_bytes(b'original cartridge')
    cfg.update(sha1=hashlib.sha1(image.read_bytes()).hexdigest())
    cfg['options']['target_path'] = 'baserom.z64'
    if layout == 'padding':
        cfg['segments'][0]['subsegments'].insert(1, [8, 'pad'])
    else:
        del cfg['segments'][0]['vram']
    (tmp_path/'build/demo.elf').write_bytes(b'nonmatching ELF')
    bindir = tmp_path/'tools'
    bindir.mkdir()
    (bindir/'mips-ps2-decompals-objcopy').touch()
    monkeypatch.setattr(toolchains, 'require', lambda _: bindir)
    monkeypatch.setattr(splatrefs, 'run', lambda cmd, *a, **kw: __import__('pathlib').Path(cmd[-1]).write_bytes(b'wrong'))
    with pytest.raises(ForkError, match='readable linked ELF|exactly one ELF'):
        splatrefs.generate(ForkProject(tmp_path), tmp_path/'demo.yaml', cfg, consoles.get('n64'))
    assert not (tmp_path/'objdiff.json').exists()


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('overlay', [False, True])
def test_incomplete_rebuild_still_gets_only_original_verified_reference_bytes(tmp_path, overlay):
    cfg = synthetic_project(tmp_path, 'n64')
    if overlay:
        cfg['segments'][0]['exclusive_ram_id'] = 'bank'
    console = consoles.get('n64')
    tools = toolchains.require('binutils-mips')
    linker = tools / ('mips-ps2-decompals-ld.exe' if (tools / 'mips-ps2-decompals-ld.exe').exists()
                       else 'mips-ps2-decompals-ld')
    splatrefs.run([str(linker), '-EB', '-Ttext', '0x80000400', '-e', 'demo_func', '-o',
                  'build/program.elf', 'build/src/demo.o'], tmp_path, lambda *_: None)
    (tmp_path / 'build/program.z64').write_bytes(b'incomplete source rebuild')
    assert splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console, log=lambda *_: None)
    unit = json.loads((tmp_path / 'objdiff.json').read_text())['units'][0]
    with (tmp_path / unit['target_path']).open('rb') as stream:
        target = ELFFile(stream).get_section_by_name('.text').data()
    with (tmp_path / unit['base_path']).open('rb') as stream:
        candidate = ELFFile(stream).get_section_by_name('.text').data()
    assert target == (tmp_path / 'baserom.z64').read_bytes()
    assert target != candidate


def test_original_reference_labels_reject_conflicting_addresses(tmp_path):
    (tmp_path / 'asm').mkdir()
    (tmp_path / 'asm/a.s').write_text('glabel same\n/* 0 80000400 00000000 */ nop\n')
    (tmp_path / 'asm/b.s').write_text('glabel same\n/* 0 80000500 00000000 */ nop\n')
    with pytest.raises(ForkError, match='ambiguous'):
        splatrefs.reference_symbols(tmp_path)


def test_plan_resolves_segment_directory_before_finding_candidates(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / 'src/overlay').mkdir()
    (tmp_path / 'build/src/overlay').mkdir()
    (tmp_path / 'src/demo.c').rename(tmp_path / 'src/overlay/demo.c')
    (tmp_path / 'build/src/demo.o').rename(tmp_path / 'build/src/overlay/demo.o')
    cfg['segments'][0]['dir'] = 'overlay'
    unit = splatrefs.plan(tmp_path, cfg)[0]
    assert unit['name'] == 'overlay/demo'
    assert unit['source'] == tmp_path / 'src/overlay/demo.c'
    assert unit['base'] == tmp_path / 'build/src/overlay/demo.o'


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_suffixed_original_address_is_resolved_and_byte_checked(tmp_path):
    import struct
    work = tmp_path / 'work'
    (work / 'asm').mkdir(parents=True)
    (work / 'asm/demo.s').write_text('.set noreorder\n.text\n.globl entry\n'
                                   'entry:\nlui $a0,%hi(D_80000502_bank)\njr $ra\n'
                                   'addiu $a0,$a0,%lo(D_80000502_bank)\n')
    directory = toolchains.require('binutils-mips')
    suffix = '.exe' if (directory / 'mips-ps2-decompals-as.exe').exists() else ''
    assembler, linker = (str(directory / ('mips-ps2-decompals-' + name + suffix)) for name in ['as', 'ld'])
    image = struct.pack('>IIII', 0x3C048000, 0x03E00008, 0x24840502, 0)
    unit = {'name': 'demo', 'vram': 0x80000400, 'delta': -0x80000400}
    target = splatrefs.assemble(tmp_path, work, unit, {'D_80000502_bank': 0x80000512}, image, assembler, linker, ('-EB',), lambda *_: None)
    assert target.is_file()


def test_explicit_version_selects_config_without_original_rom_mapping(tmp_path, monkeypatch):
    for version in ('us', 'jp'):
        (tmp_path / f'demo.{version}.yaml').write_text('options: {platform: n64}\nsegments: []\n')
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, '_splat_yaml', lambda: tmp_path / 'demo.us.yaml')
    monkeypatch.setattr(project, '_splat_version_configs', lambda: {})
    seen = []
    monkeypatch.setattr(consoles, 'get', lambda _: SimpleNamespace(
        generate_objdiff=lambda _p, path, _cfg, **kw: seen.append(path) or True))
    assert project.generate_objdiff('jp')
    assert seen == [tmp_path / 'demo.jp.yaml']


def test_plan_keeps_explicit_data_addresses_without_relying_on_comments(tmp_path):
    cfg = config(tmp_path)
    cfg['segments'][0]['subsegments'].append([8, '.data', 'demo'])
    cfg['segments'][0]['subsegments'][1]['name'] = 'demo'
    unit = splatrefs.plan(tmp_path, cfg)[0]
    assert unit['sections'] == {'.data': 0x80000408, '.bss': 0x80000410}


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
def test_reference_data_without_address_comments_requires_an_explicit_map(tmp_path):
    import struct
    work = tmp_path / 'work'
    (work / 'asm/data').mkdir(parents=True)
    (work / 'asm/demo.s').write_text('.set noreorder\n.text\njr $ra\nnop\n')
    (work / 'asm/data/demo.data.s').write_text('.data\n.word 0x12345678\n')
    directory = toolchains.require('binutils-mips')
    suffix = '.exe' if (directory / 'mips-ps2-decompals-as.exe').exists() else ''
    assembler, linker = (str(directory / ('mips-ps2-decompals-' + name + suffix)) for name in ['as', 'ld'])
    image = struct.pack('>8I', 0x03E00008, 0, 0, 0, 0x12345678, 0, 0, 0)
    unit = {'name': 'demo', 'vram': 0x80000400, 'delta': -0x80000400}
    with pytest.raises(ForkError, match='original address'):
        splatrefs.assemble(tmp_path, work, unit, {}, image, assembler, linker, ('-EB',), lambda *_: None)
    unit['sections'] = {'.data': 0x80000410}
    assert splatrefs.assemble(tmp_path, work, unit, {}, image, assembler, linker, ('-EB',), lambda *_: None).exists()


def test_plan_skips_explicitly_empty_code_without_hiding_other_units(tmp_path):
    cfg = config(tmp_path)
    cfg['segments'][0]['subsegments'] = [[0, 'c', 'demo'], [0, 'data', 'demo'], [8, 'asm', 'real']]
    units = splatrefs.plan(tmp_path, cfg)
    assert [unit['name'] for unit in units] == ['real']


@pytest.mark.parametrize('verified', [False, True])
@pytest.mark.parametrize('make', ['make', 'gmake'])
def test_checksum_failure_can_finish_only_with_verified_references(tmp_path, monkeypatch, verified, make):
    project = ForkProject(tmp_path)
    monkeypatch.setattr(project, 'unpack_disc', lambda *a, **k: None)
    monkeypatch.setattr(project, 'setup_commands', lambda *a: [[make]])
    monkeypatch.setattr(project, 'run', lambda *a, **k: (False, 'sha1sum: computed checksum did NOT match'))
    def generate(**kwargs):
        if not verified:
            raise ForkError('reference bytes differ')
        (tmp_path / 'objdiff.json').write_text('{"units": []}')
    monkeypatch.setattr(project, 'generate_objdiff', generate)
    if verified:
        project.setup(log=lambda *_: None)
    else:
        with pytest.raises(ForkError, match='computed checksum did NOT match'):
            project.setup(log=lambda *_: None)
        assert not (tmp_path / 'objdiff.json').exists()


def test_flat_binary_span_uses_load_addresses_and_ignores_bss(tmp_path, monkeypatch):
    image = tmp_path / 'program.elf'
    image.touch()
    sections = [{'sh_flags': 2, 'sh_type': 'SHT_PROGBITS', 'sh_size': 16, 'sh_addr': 0x80000400},
                {'sh_flags': 2, 'sh_type': 'SHT_PROGBITS', 'sh_size': 8, 'sh_addr': 0x90000000},
                {'sh_flags': 2, 'sh_type': 'SHT_NOBITS', 'sh_size': 0x100000, 'sh_addr': 0xa0000000}]
    class Load(dict):
        def section_in_segment(self, section):
            return section['sh_addr'] == self['p_vaddr']
    loads = [Load(p_type='PT_LOAD', p_vaddr=0x80000400, p_paddr=0),
             Load(p_type='PT_LOAD', p_vaddr=0x90000000, p_paddr=32)]
    class Elf:
        def iter_sections(self):
            return iter(sections)
        def iter_segments(self):
            return iter(loads)
    monkeypatch.setattr(splatrefs, 'ELFFile', lambda _: Elf())
    assert splatrefs.binary_span(image) == 40
    loads.clear()
    assert splatrefs.binary_span(image) == 0x90000008 - 0x80000400


def test_overlay_skips_objcopy_when_flat_image_exceeds_original(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    image = tmp_path / 'baserom.z64'
    image.write_bytes(b'small original')
    (tmp_path / 'build/program.elf').write_bytes(b'linked image')
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'mips-ps2-decompals-objcopy').touch()
    monkeypatch.setattr(toolchains, 'require', lambda _: tools)
    monkeypatch.setattr(splatrefs, 'binary_span', lambda _: 2**31)
    monkeypatch.setattr(splatrefs, 'run', lambda *a, **kw: pytest.fail('oversized objcopy must not run'))
    with pytest.raises(ForkError, match='exactly one ELF'):
        splatrefs.verified_rebuild(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, image,
                                   hashlib.sha1(image.read_bytes()).hexdigest(), lambda *_: None)
    assert not (tmp_path / 'objdiff.json').exists()


@pytest.mark.skipif(toolchains.installed('binutils-mips') is None, reason='needs pinned MIPS binutils')
@pytest.mark.parametrize('platform', ['n64', 'psx', 'ps2'])
def test_import_maps_generated_names_to_linked_c_names(tmp_path, platform):
    cfg = synthetic_project(tmp_path, platform)
    console = consoles.get(platform)
    tools = toolchains.require('binutils-mips')
    suffix = '.exe' if (tools / 'mips-ps2-decompals-as.exe').exists() else ''
    assembler = str(tools / ('mips-ps2-decompals-as' + suffix))
    linker = str(tools / ('mips-ps2-decompals-ld' + suffix))
    candidate = (tmp_path / 'original.s').read_text().replace('demo_func', 'readable_function')
    (tmp_path / 'candidate.s').write_text(candidate)
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/src/demo.o', 'candidate.s'], tmp_path, print)
    splatrefs.run([linker, *(f for f in console.as_flags if f in ('-EB', '-EL')), '-Ttext', '0x80000400',
                   '-e', 'readable_function', '-o', 'build/program.elf', 'build/src/demo.o'], tmp_path, print)
    splatrefs.generate(ForkProject(tmp_path), tmp_path / 'splat.yaml', cfg, console)
    result = json.loads((tmp_path / 'objdiff.json').read_text())
    unit = next(u for u in result['units'] if u.get('base_path'))
    assert unit['symbol_mappings'] == {'demo_func': 'readable_function'}
    (tmp_path / 'src/demo.c').write_text('int readable_function(void) { return 7; }')
    report = objdiff.report(tmp_path, tmp_path / 'report.json')
    function = next(f for u in report['units'] for f in u.get('functions', []) if f['name'] == 'demo_func')
    assert function['fuzzy_match_percent'] == 100
    (tmp_path / 'candidate.s').write_text(candidate.replace(',7', ',8'))
    splatrefs.run([assembler, *console.as_flags, '-o', 'build/src/demo.o', 'candidate.s'], tmp_path, print)
    report = objdiff.report(tmp_path, tmp_path / 'report.json')
    function = next(f for u in report['units'] for f in u.get('functions', []) if f['name'] == 'demo_func')
    assert function['fuzzy_match_percent'] < 100
    target = tmp_path / unit['target_path']
    base = tmp_path / unit['base_path']
    assert splatrefs.source_symbol_mappings(target, base, {'readable_function': 0x80000404}, 0x80000400) == {}
    assert splatrefs.source_symbol_mappings(target, tmp_path / 'missing.o', {}, 0x80000400) == {}
