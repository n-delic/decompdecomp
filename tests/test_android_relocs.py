"""ARMv7 PIC/GOT relocation verdicts against the pinned linker's output, using homemade C (#133)."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.arch import arm
from decompnet.arch.arm_image import Image, image, plt_entries
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
extern int external(int);
extern int external_data;
int local_fn(int x) { return x + 5; }
__attribute__((visibility("hidden"))) int hidden_fn(int x) { return x * 3; }
int (*only[])(int) = { external };
int *only_data[] = { &external_data };
int (*mixed[])(int) = { external, local_fn };
int (*hidden_tab[])(int) = { hidden_fn, external };
const char *strs[] = { "hi", "yo" };
int use_only(int i) { return only[i](i); }
int use_only_data(int i) { return *only_data[i]; }
int use_mixed(int i) { return mixed[i](i); }
int use_hidden(int i) { return hidden_tab[i](i); }
const char *get_str(int i) { return strs[i]; }
'''

# The pack's own flags, for the host LLVM variant below.
FLAGS = ['-O2', '-fPIC', '-march=armv7-a', '-mfloat-abi=softfp', '-mfpu=vfpv3-d16', '-mthumb']


class _Host:
    """A host clang with the ARM backend standing in for the NDK's (#248): the library and every candidate are built
    by the same compiler, so the judge's linker-facing rules run on machines without the pinned NDK."""

    def __init__(self, clang, root):
        self.clang, self.root, self.n = clang, root, 0

    def __call__(self, header, source):
        self.n += 1
        c = self.root / f'candidate{self.n}.c'
        c.write_text(f'{header}\n{source}')
        run = subprocess.run(capped([self.clang, '-target', 'armv7-none-linux-androideabi', *FLAGS, '-c', str(c),
                                     '-o', str(c.with_suffix('.o'))]), capture_output=True, text=True, timeout=60)
        return SimpleNamespace(ok=run.returncode == 0, log=run.stderr,
                               obj=c.with_suffix('.o').read_bytes() if run.returncode == 0 else None)


# gold is what NDK r10e's arm-linux-androideabi-ld is; its ld.bfd writes the Thumb-to-ARM PLT entries
# (`bx pc; mov r8, r8`); LLD writes its own thunks. "host-lld" is a host clang with LLD, no NDK.
@pytest.fixture(scope='module', params=['gold', 'bfd', 'lld', 'host-lld'])
def linked(tmp_path_factory, request):
    root = tmp_path_factory.mktemp(f'android-relocs-{request.param}')
    if request.param == 'host-lld':
        clang = shutil.which('clang')
        if clang is None:
            pytest.skip('needs a host clang with the ARM backend')
        compile = _Host(clang, root)
        probe = compile('', 'int probe(void) { return 0; }')
        if not probe.ok:
            pytest.skip(f'the host clang has no ARM backend: {probe.log[:200]}')
    else:
        if toolchains.installed('android-clang-3.5') is None:
            pytest.skip('needs pinned Android Clang 3.5')
        cfg = compiler_config(root, {'compiler': {'toolchain': 'android-clang-3.5'}})
        def compile(header, source):
            return compile_source(cfg, header, source)
    compiled = compile('', SOURCE)
    assert compiled.ok, compiled.log
    (root / 'native.o').write_bytes(compiled.obj)
    if request.param in ('lld', 'host-lld'):
        linker = shutil.which('ld.lld')
        if linker is None:
            pytest.skip('needs LLVM lld for the Android LLD linker integration')
        command = [linker, '-flavor', 'gnu', '-m', 'armelf_linux_eabi']
    else:
        gcc = toolchains.path('android-clang-3.5') / 'gcc/bin'
        command = [str(gcc / ('arm-linux-androideabi-ld.bfd' if request.param == 'bfd' else
                              'arm-linux-androideabi-ld'))]
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
    return root, compile, compiled.obj, program, split, symbols, read


@pytest.mark.parametrize('name', ['get_global', 'set_global', 'get_string', 'call_external', 'call_local', 'call_thumb', 'call_arm'])
def test_pic_functions_match_the_real_linkers_bytes(linked, name):
    root, compile, obj, program, split, symbols, read = linked
    fn = next(f for f in split.functions if f.name == name)
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(obj, name, target, symbols, read)
    assert result.exact, result.problems


def test_got_slots_for_other_symbols_do_not_match(linked):
    root, compile, obj, program, split, symbols, read = linked
    compiled = compile('extern int second;', 'int get_global(void) { return second; }')
    assert compiled.ok, compiled.log
    fn = next(f for f in split.functions if f.name == 'get_global')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(compiled.obj, 'get_global', target, symbols, read)
    assert not result.exact and any('GOT reference' in p for p in result.problems)


def test_own_string_data_is_compared_after_a_relative_relocation(linked):
    root, compile, obj, program, split, symbols, read = linked
    compiled = compile('', 'const char *get_string(void) { return "native scam"; }')
    assert compiled.ok, compiled.log
    fn = next(f for f in split.functions if f.name == 'get_string')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(compiled.obj, 'get_string', target, symbols, read)
    assert not result.exact and any('data' in p for p in result.problems)


def test_plt_calls_are_tied_to_the_import_the_stub_uses(linked):
    root, compile, obj, program, split, symbols, read = linked
    context = image(program)
    assert context.plt['external']
    compiled = compile('extern int other(int);', 'int call_external(int x) { return other(x); }')
    fn = next(f for f in split.functions if f.name == 'call_external')
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    assert not arm.link(compiled.obj, 'call_external', target, symbols, read).exact


_EXTERNS = ('extern int external(int); extern int external_data; extern int local_fn(int); extern int use_only(int);'
            ' __attribute__((visibility("hidden"))) int hidden_fn(int);')


# #248: the original's dynamic slots (ABS32 against an import or a preemptible local, RELATIVE) are zero or an
# addend in the file, so each is compared by what it resolves to, and must be the candidate's pointer too.
@pytest.mark.parametrize('name,source,exact', [
    ('use_only', 'int (*only[])(int) = { external };\nint use_only(int i) { return only[i](i); }', True),
    ('use_only', 'int (*only[])(int) = { 0 };\nint use_only(int i) { return only[i](i); }', False),
    ('use_only', 'int (*only[])(int) = { local_fn };\nint use_only(int i) { return only[i](i); }', False),
    ('use_only_data', 'int *only_data[] = { &external_data };\nint use_only_data(int i) { return *only_data[i]; }',
     True),
    ('use_only_data', 'int *only_data[] = { 0 };\nint use_only_data(int i) { return *only_data[i]; }', False),
    ('use_mixed', 'int (*mixed[])(int) = { external, local_fn };\nint use_mixed(int i) { return mixed[i](i); }', True),
    ('use_mixed', 'int (*mixed[])(int) = { local_fn, external };\nint use_mixed(int i) { return mixed[i](i); }', False),
    ('use_mixed', 'int (*mixed[])(int) = { external, 0 };\nint use_mixed(int i) { return mixed[i](i); }', False),
    ('use_mixed', 'int (*mixed[])(int) = { external, use_only };\nint use_mixed(int i) { return mixed[i](i); }',
     False),
    ('use_hidden', 'int (*hidden_tab[])(int) = { hidden_fn, external };\nint use_hidden(int i) '
     '{ return hidden_tab[i](i); }', True),
    ('use_hidden', 'int (*hidden_tab[])(int) = { 0, external };\nint use_hidden(int i) { return hidden_tab[i](i); }',
     False),
    ('get_str', 'const char *strs[] = { "hi", "yo" };\nconst char *get_str(int i) { return strs[i]; }', True),
    ('get_str', 'const char *strs[] = { "hi", "yA" };\nconst char *get_str(int i) { return strs[i]; }', False),
    ('get_str', 'const char *strs[] = { "hi", 0 };\nconst char *get_str(int i) { return strs[i]; }', False),
])
@pytest.mark.parametrize('bss', [True, False])
def test_pointer_tables_are_judged_by_the_loaders_slots(linked, name, source, exact, bss):
    root, compile, obj, program, split, symbols, read = linked
    # a table of zeros lands in .bss by default, and in .data with -fno-zero-initialized-in-bss: both are checked
    compiled = compile(_EXTERNS, source if bss else source.replace('= {', '__attribute__((section(".data"))) = {', 1))
    assert compiled.ok, compiled.log
    fn = next(f for f in split.functions if f.name == name)
    target = ANDROID.link_target(ANDROID.target_from_split(fn, symbols.resolve, False, 0), program)
    result = arm.link(compiled.obj, name, target, symbols, read)
    assert result.exact == exact, result.problems


def test_bfd_thumb_plt_entries_are_recorded_where_thumb_code_calls_them():
    """NDK ld.bfd: `bx pc; mov r8, r8` and then the ARM entry; Thumb callers BL to the first word (#248)."""
    def word(value):
        return value.to_bytes(4, 'little')
    plt = (word(0x46C04778)  # 0x340: bx pc; mov r8, r8
           + word(0xE28FC600) + word(0xE28CCA01) + word(0xE5BCF020)  # 0x344: add ip, pc, #0; add ip, #0x1000; ldr pc
           + word(0xE28FC600) + word(0xE28CCA01) + word(0xE5BCF018))  # 0x350: an ARM-only entry (gold's shape)
    slots = {0x344 + 8 + 0x1000 + 0x20: ('external', None), 0x350 + 8 + 0x1000 + 0x18: ('other', None)}
    assert plt_entries(plt, 0x340, slots) == {'external': [0x344, 0x341], 'other': [0x350]}
    context = Image(program=plt, segments=[(0x340, 0, len(plt))])
    assert context.through_veneer(0x340) == 0x340  # the Thumb entry is the import's own address, not a veneer


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
