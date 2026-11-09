"""ARMv7 PIC/GOT relocation verdicts against the pinned linker's output, using homemade C (#133)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from decompnet import toolchains
from decompnet.arch import arm
from decompnet.arch.arm_image import Image, image
from decompnet.compiler import compile_source
from decompnet.consoles.android import ANDROID, elf
from decompnet.limits import capped
from decompnet.project import compiler_config
from decompnet.rom.symbols import Symbol, SymbolTable


def test_pic_arm_veneer_resolves_its_relative_destination():
    address, target = 0x1000, 0x1400
    offset = (target - address - 12).to_bytes(4, 'little')
    code = bytes.fromhex('04 c0 9f e5 0c c0 8f e0 1c ff 2f e1') + offset
    context = Image(program=code, segments=[(address, 0, len(code))], veneers={address})
    assert context.through_veneer(address) == target


def test_lld_thumb_pic_long_thunk_resolves_its_relative_destination():
    code = bytes.fromhex('41 f2 34 2c c0 f2 01 0c fc 44 60 47')
    context = Image(program=code, segments=[(0x1000, 0, len(code))],
                    veneers={0x1000}, thumb={0x1000})
    assert context.through_veneer(0x1001) == 0x12240

SOURCE = '''int first = 7;
int second = 9;
int get_global(void) { return first; }
void set_global(int x) { first = x; }
const char *get_string(void) { return "native demo"; }
int call_external(int x) { extern int external(int); return external(x); }
int call_local(int x) { return get_global() + x; }
__attribute__((noinline,visibility("hidden"))) int thumb_helper(int x) { return x + 1; }
__attribute__((target("arm"))) int call_thumb(int x) { return thumb_helper(x) + 3; }
__attribute__((target("arm"),noinline,visibility("hidden"))) int arm_helper(int x) { return x + 2; }
int call_arm(int x) { return arm_helper(x) + 4; }
'''


@pytest.fixture(scope='module', params=['gnu', 'lld'])
def linked(tmp_path_factory, request):
    if toolchains.installed('android-clang-3.5') is None:
        pytest.skip('needs pinned Android Clang 3.5')
    root = tmp_path_factory.mktemp(f'android-relocs-{request.param}')
    cfg = compiler_config(root, {'compiler': {'toolchain': 'android-clang-3.5'}})
    compiled = compile_source(cfg, '', SOURCE)
    assert compiled.ok, compiled.log
    (root / 'native.o').write_bytes(compiled.obj)
    if request.param == 'lld':
        linker = shutil.which('ld.lld')
        if linker is None:
            pytest.skip('needs LLVM lld for the Android LLD linker integration')
        command = [linker, '-flavor', 'gnu', '-m', 'armelf_linux_eabi']
    else:
        command = [str(toolchains.path('android-clang-3.5') / 'gcc/bin/arm-linux-androideabi-ld')]
    subprocess.run(capped([*command, '-shared', str(root / 'native.o'), '-o', str(root / 'game.so')]),
                   check=True, capture_output=True, timeout=60)
    program = (root / 'game.so').read_bytes()
    split = elf.split(program, root, log=lambda *_: None)
    symbols = SymbolTable([Symbol(f.name, f.vram, f.size, 'func') for f in split.functions]
                          + [Symbol(d.name, d.vram, d.size, d.kind) for d in split.data])
    def read(address, size):
        for segment in split.segments:
            if segment.contains(address, size):
                offset = segment.rom_start + address - segment.vram
                return program[offset:offset + size]
        return None
    return root, cfg, compiled.obj, program, split, symbols, read


@pytest.mark.parametrize('name', ['get_global', 'set_global', 'get_string', 'call_external', 'call_local', 'call_thumb', 'call_arm'])
def test_pic_functions_match_the_real_linkers_bytes(linked, name):
    root, cfg, obj, program, split, symbols, read = linked
    fn = next(f for f in split.functions if f.name == name)
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(obj, name, target, symbols, read)
    assert result.exact, result.problems


def test_got_slots_for_other_symbols_do_not_match(linked):
    root, cfg, obj, program, split, symbols, read = linked
    compiled = compile_source(cfg, 'extern int second;', 'int get_global(void) { return second; }')
    assert compiled.ok, compiled.log
    fn = next(f for f in split.functions if f.name == 'get_global')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(compiled.obj, 'get_global', target, symbols, read)
    assert not result.exact and any('GOT reference' in p for p in result.problems)


def test_own_string_data_is_compared_after_a_relative_relocation(linked):
    root, cfg, obj, program, split, symbols, read = linked
    compiled = compile_source(cfg, '', 'const char *get_string(void) { return "native scam"; }')
    assert compiled.ok, compiled.log
    fn = next(f for f in split.functions if f.name == 'get_string')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(compiled.obj, 'get_string', target, symbols, read)
    assert not result.exact and any('data' in p for p in result.problems)


def test_plt_calls_are_tied_to_the_import_the_stub_uses(linked):
    root, cfg, obj, program, split, symbols, read = linked
    context = image(program)
    assert context.plt['external']
    compiled = compile_source(cfg, 'extern int other(int);', 'int call_external(int x) { return other(x); }')
    fn = next(f for f in split.functions if f.name == 'call_external')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    assert not arm.link(compiled.obj, 'call_external', target, symbols, read).exact


@pytest.mark.parametrize('mode,extra', [('-marm', []), ('-mthumb', []), ('-marm', ['-mno-movt'])])
def test_movw_movt_references_and_defined_data_are_verified(tmp_path, mode, extra):
    if toolchains.installed('android-clang-3.5') is None:
        pytest.skip('needs pinned Android Clang 3.5')
    from io import BytesIO

    from elftools.elf.elffile import ELFFile
    flags = ['-O2', '-fno-pic', '-march=armv7-a', mode, *extra]
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'android-clang-3.5', 'flags': flags}})
    source = 'int datum = 7; int *address(void) { return &datum; }'
    compiled = compile_source(cfg, '', source)
    assert compiled.ok, compiled.log
    (tmp_path / 'input.o').write_bytes(compiled.obj)
    linker = toolchains.path('android-clang-3.5') / 'gcc/bin/arm-linux-androideabi-ld'
    subprocess.run(capped([str(linker), '-Ttext=0x1000', '-Tdata=0x8000', '-e', 'address',
                           str(tmp_path / 'input.o'), '-o', str(tmp_path / 'linked.elf')]),
                   check=True, capture_output=True, timeout=60)
    program = (tmp_path / 'linked.elf').read_bytes()
    linked = ELFFile(BytesIO(program))
    table = linked.get_section_by_name('.symtab')
    function = next(s for s in table.iter_symbols() if s.name == 'address')
    section = linked.get_section(function['st_shndx'])
    start = (function['st_value'] & ~1) - section['sh_addr']
    symbols = SymbolTable([Symbol(s.name, s['st_value'] & ~1 if s['st_info']['type'] == 'STT_FUNC' else s['st_value'],
                                  s['st_size'], 'func' if s['st_info']['type'] == 'STT_FUNC' else 'data')
                           for s in table.iter_symbols() if s.name and s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT')])
    target = arm.ArmTarget('address', function['st_value'] & ~1, section.data()[start:start + function['st_size']],
                           mode == '-mthumb', thumb2=True, image=image(program))
    assert arm.link(compiled.obj, 'address', target, symbols, target.image.read).exact
    changed = compile_source(cfg, '', source.replace('datum = 7', 'datum = 9'))
    result = arm.link(changed.obj, 'address', target, symbols, target.image.read)
    assert not result.exact and any('data' in message for message in result.problems)
