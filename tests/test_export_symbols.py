"""Startup linker aliases retain original addresses after inferred segment boundaries change."""
import json
import runpy
import shutil
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import consoles, toolchains
from decompnet.rom import export


@pytest.mark.parametrize('branch_relocation', [False, True])
@pytest.mark.parametrize('platform', ['n64', 'ps1', 'ps2', 'psp'])
def test_startup_bss_relocations_keep_original_addresses(tmp_path, platform, branch_relocation):
    import subprocess

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS binutils')
    console = consoles.get(platform)
    order = 'big' if console.big_endian else 'little'
    endian = '-EB' if console.big_endian else '-EL'
    tools = toolchains.binary('binutils-mips').parent
    start, end = 0x80019000, 0x8001B030
    # The startup's addresses are beyond the inferred code segment, and LO16's
    # sign bit requires a carry into HI16. The first reference also has an addend.
    words = [0x3C080000 | ((start + 0x10 + 0x8000) >> 16),
             0x25080000 | ((start + 0x10) & 0xFFFF),
             0x3C090000 | ((end + 0x8000) >> 16), 0x25290000 | (end & 0xFFFF),
             *([0x10000003] if branch_relocation else []), 0x03E00008, 0]
    target = b''.join(word.to_bytes(4, order) for word in words)
    (tmp_path/'image.bin').write_bytes(target + bytes(8))
    (tmp_path/'entry.s').write_text('.text\n.set noreorder\n.globl entry\n.type entry,@function\nentry:\n'
                                  'lui $8,%hi(main_BSS_START+0x10)\naddiu $8,$8,%lo(main_BSS_START+0x10)\n'
                                  'lui $9,%hi(main_BSS_END)\naddiu $9,$9,%lo(main_BSS_END)\n'
                                  + ('beq $0,$0,external\n' if branch_relocation else '') + 'jr $31\nnop\n'
                                  '.size entry,.-entry\n')
    subprocess.run([str(tools/'mips-ps2-decompals-as'), endian, '-mips2', '-no-pad-sections',
                    '-o', 'entry.o', 'entry.s'], cwd=tmp_path, check=True, capture_output=True)
    config = {'image':'image.bin', 'symbols':{}, 'functions':[], 'segments':[
        {'name':'main', 'kind':'code', 'start':0, 'end':len(target), 'vram':0x80001000,
         'pieces':[{'vram':0x80001000, 'obj':'entry.o'}], 'bss':[]}]}
    (tmp_path/'build.json').write_text(json.dumps(config))
    shutil.copyfile(Path(export.__file__).with_name('export_build.py'), tmp_path/'build.py')
    script = runpy.run_path(str(tmp_path/'build.py'))
    assert script['segment_addresses']() == {'main_BSS_START':start, 'main_BSS_END':end}
    (tmp_path/'game.ld').write_text(script['linker_script']({}, [], []) + '\nexternal = 0x80001020;\n')
    subprocess.run([str(tools/'mips-ps2-decompals-ld'), endian, '-T', 'game.ld', '-o', 'game.elf', 'entry.o'],
                   cwd=tmp_path, check=True, capture_output=True)
    with (tmp_path/'game.elf').open('rb') as stream:
        elf = ELFFile(stream)
        assert elf.get_section_by_name('.main').data() == target
        symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
        assert symbols['main_BSS_SIZE'] == end - start
    # A register mismatch supplies no evidence; a known absolute symbol is retained.
    (tmp_path/'image.bin').write_bytes((words[0] ^ (1 << 16)).to_bytes(4, order) + target[4:])
    assert script['segment_addresses']() == {}
    assert f'main_BSS_START = 0x{0x80001000 + len(target):X};' in script['linker_script']({}, [], [])
    script['B']['symbols']['main_BSS_START'] = start
    assert script['segment_addresses']() == {'main_BSS_START':start}
    # Explicit addresses are emitted once rather than overwritten by inferred bounds.
    text = script['linker_script']({}, [], [])
    assert text.count('main_BSS_START = ') == 1
    assert f'main_BSS_START = 0x{start:08X};' in text
    script['B']['symbols'].clear()
    script['B']['segments'][0]['name'] = 'other'
    (tmp_path/'image.bin').write_bytes(target)
    assert script['segment_addresses']() == {}  # unrelated undefined names stay undefined
    script['B']['segments'][0]['name'] = 'main'
    # Two startup references to one generated name must agree on its original address.
    different = bytearray(target)
    different[4:8] = (words[1] + 0x100).to_bytes(4, order)
    (tmp_path/'image.bin').write_bytes(target + bytes(0x20 - len(target)) + different)
    script['B']['segments'][0]['pieces'].append({'vram':0x80001020, 'obj':'entry.o'})
    with pytest.raises(SystemExit, match='Conflicting original startup addresses'):
        script['segment_addresses']()


def test_segment_evidence_skips_functions_without_relevant_relocations(tmp_path, monkeypatch):
    import subprocess

    from decompnet.arch import mips_source_symbols

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs pinned MIPS binutils')
    tools = toolchains.binary('binutils-mips').parent
    target = bytes.fromhex('24020001 03e00008 00000000 3c088002 25089000 03e00008 00000000 '
                           '24030002 03e00008 00000000')
    (tmp_path / 'image.bin').write_bytes(target)
    (tmp_path / 'entry.s').write_text(
        '.text\n.set noreorder\n'
        '.globl before\n.type before,@function\nbefore:\naddiu $2,$0,1\njr $31\nnop\n.size before,.-before\n'
        '.globl entry\n.type entry,@function\nentry:\nlui $8,%hi(main_BSS_START)\n'
        'addiu $8,$8,%lo(main_BSS_START)\njr $31\nnop\n.size entry,.-entry\n'
        '.globl after\n.type after,@function\nafter:\naddiu $3,$0,2\njr $31\nnop\n.size after,.-after\n')
    subprocess.run([str(tools/'mips-ps2-decompals-as'), '-EB', '-mips2', '-no-pad-sections',
                    '-o', 'entry.o', 'entry.s'], cwd=tmp_path, check=True, capture_output=True)
    config = {'image': 'image.bin', 'symbols': {}, 'functions': [], 'segments': [
        {'name': 'main', 'kind': 'code', 'start': 0, 'end': len(target), 'vram': 0x80001000,
         'pieces': [{'vram': 0x80001000, 'obj': 'entry.o'}], 'bss': []}]}
    (tmp_path/'build.json').write_text(json.dumps(config))
    shutil.copyfile(Path(export.__file__).with_name('export_build.py'), tmp_path/'build.py')
    script = runpy.run_path(str(tmp_path/'build.py'))
    original = mips_source_symbols.bindings
    checked = []

    def track(obj, name, *args, **kwargs):
        checked.append(name)
        return original(obj, name, *args, **kwargs)

    monkeypatch.setattr(mips_source_symbols, 'bindings', track)
    assert script['segment_addresses']() == {'main_BSS_START': 0x80019000}
    assert checked == ['entry']
    # Once explicitly supplied, the label needs no inference at all.
    checked.clear()
    script['B']['symbols']['main_BSS_START'] = 0x80019000
    assert script['segment_addresses']() == {'main_BSS_START': 0x80019000}
    assert not checked
