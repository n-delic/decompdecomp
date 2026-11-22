
import pytest

from decompnet.app import donor_sources as ds
from decompnet.network import NetworkError


def test_literal_build_hints_do_not_execute_make_or_mix_assembly_flags(tmp_path):
    (tmp_path/'sdk').mkdir();(tmp_path/'include').mkdir()
    marker=tmp_path/'executed'
    (tmp_path/'Makefile').write_text(f'''IINC += -I sdk -I $(ROOT)/ignored -I ../outside
COMMON_DEFINES := -DWORD_BITS=32
C_DEFINES := -D_LANGUAGE_C
AS_DEFINES := -D_LANGUAGE_ASSEMBLY
ifeq ($(VERSION),other)
C_DEFINES += -DOTHER=1
endif
BAD := $(shell touch {marker})
''')
    roots,macros=ds.build_hints(tmp_path)
    assert roots[0]==tmp_path/'sdk'
    assert macros=={'WORD_BITS':'32','_LANGUAGE_C':'1'}
    assert not marker.exists()


def test_conditional_includes_are_left_for_the_destination_preprocessor(tmp_path):
    path=tmp_path/'entry.c'
    path.write_text('#ifdef ASSEMBLY\n#include "missing.h"\n#endif\n#include "types.h"\nint f(Word x){return x;}')
    (tmp_path/'types.h').write_text('typedef int Word;')
    reader=ds.DonorReader({'root':str(tmp_path)})
    text=reader.flatten(path)
    assert '#ifdef ASSEMBLY\n#error donor_header_missing' in text
    assert 'typedef int Word;' in text
    path.write_text('#include "../outside.h"')
    with pytest.raises(NetworkError,match='within'):reader=ds.DonorReader({'root':str(tmp_path)});reader.flatten(path)


def test_reader_rejects_symlink_escapes_and_unbounded_context(tmp_path):
    outside=tmp_path.parent/'donor-outside.h';outside.write_text('typedef int Secret;')
    (tmp_path/'escape.h').symlink_to(outside)
    (tmp_path/'entry.c').write_text('#include "escape.h"\nint f(void){return 0;}')
    reader=ds.DonorReader({'root':str(tmp_path)})
    with pytest.raises(NetworkError,match='leaves'):reader.flatten(tmp_path/'entry.c')
    (tmp_path/'large.c').write_text(' '* (ds.MAX_HEADER+1))
    with pytest.raises(NetworkError,match='2 MB'):reader.flatten(tmp_path/'large.c')


def test_compact_retains_only_required_declarations_and_no_foreign_include():
    text='typedef unsigned short Small; typedef struct {Small x;} Rec; typedef int Unused; int unrelated(void){return 5;} void f(Rec *r){r->x=3;}'
    source,header=ds.compact(text,'f')
    assert source.lstrip().startswith('void f(')
    assert 'typedef unsigned short Small;' in header and 'Rec;' in header
    assert 'Unused' not in header and 'unrelated' not in header
    assert '#include' not in source+header
    with pytest.raises(NetworkError,match='initialized'):
        ds.compact('int global=2; int f(void){return global;}','f')


def test_same_name_preprocessing_and_context_compile_with_pinned_compiler(tmp_path):
    from types import SimpleNamespace

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs installed pinned compiler')
    settings={'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    net=SimpleNamespace(cfg=lambda p:settings)
    proj={'root':str(tmp_path)}
    fn={'vram':0,'name':'f'}
    body='int f(Small x){return (x + OFFSET) * 2;}'
    context='#if 0\n#error missing conditional header\n#endif\n#define OFFSET 3\ntypedef unsigned short Small;'
    source,header=ds.preprocess(net,proj,fn,body,context)
    assert 'OFFSET' not in source and len(header)<100
    cfg=compiler_config(tmp_path,settings)
    candidate=compile_source(cfg,header,source)
    control=compile_source(cfg,'','int f(unsigned short x){return (x+3)*2;}')
    assert candidate.ok,candidate.log
    assert control.ok,control.log
    assert extract_function(candidate.obj,'f').code==extract_function(control.obj,'f').code


def test_relative_include_with_comment_stays_in_the_checkout(tmp_path):
    folder = tmp_path / 'src'
    folder.mkdir()
    (tmp_path / 'types.h').write_text('typedef int Word;\n')
    path = folder / 'entry.c'
    path.write_text('#include "../types.h" /* relative to this file */\nWord f(void);\n')
    reader = ds.DonorReader({'root': str(tmp_path)})
    assert 'typedef int Word;' in reader.flatten(path)


def test_donor_file_profile_prefers_objdiff_and_reads_literal_make_overrides(tmp_path):
    from types import SimpleNamespace

    from decompnet.app.donor_build import Settings

    (tmp_path / 'Makefile').write_text(
        'CC := tools/ido-static-recomp/build/7.1/out/cc\n'
        'OPT_FLAGS := -O2\nCFLAGS := -G 0 -I include\n'
        'build/entry.c.o: CC := tools/ido-static-recomp/build/5.3/out/cc\n'
        'build/entry.c.o: OPT_FLAGS := -O1\n'
        'build/entry.c.o: private CPPFLAGS += -DFILE_LIMIT=32 -I src -o /tmp/ignored\n'
        'build/%.c.o: %.c\n\t$(CC) -c -DSOURCE_MODE=1 -o $@ $<\n'
    )
    project = Settings(SimpleNamespace(root=tmp_path))
    unit = SimpleNamespace(base=tmp_path / 'build/entry.c.o', source='entry.c', scratch=None)
    assert project.for_unit(unit) == {'toolchain': 'ido-5.3', 'flags': ['-G', '0', '-O1', '-DFILE_LIMIT=32', '-DSOURCE_MODE=1']}
    unit.scratch = {'compiler': 'ido7.1', 'c_flags': '-O0 -mips3 -DN=1 -G 0 -woff 0'}
    assert project.for_unit(unit) == {'toolchain': 'ido-7.1', 'flags': ['-O0', '-mips3', '-DN=1', '-G', '0', '-woff', '0', '-DFILE_LIMIT=32', '-DSOURCE_MODE=1']}


def test_whole_unit_preprocessor_refuses_foreign_include_spellings():
    from decompnet.compiler import CompilerConfig, preprocess_donor_unit

    config = CompilerConfig(['a compiler that must not start', '-c'])
    for spelling in ('#include "secret"', '#/**/include "secret"', '%:include "secret"',
                     '#inc\\\nlude "secret"', '??=include "secret"'):
        assert preprocess_donor_unit(config, spelling) is None


def test_whole_unit_ido_preprocessing_accepts_line_comments(tmp_path):
    import pytest

    from decompnet import toolchains
    from decompnet.compiler import preprocess_donor_unit
    from decompnet.project import compiler_config

    if not toolchains.installed('ido-7.1'):
        pytest.skip('requires the installed pinned IDO 7.1 compiler')
    invocation = compiler_config(tmp_path, {
        'compiler': {'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0', '-non_shared', '-Xcpluscomm']}})
    source = ("// The original compiler doesn't recognize this as a comment in preprocessing mode. " + chr(92) + "\n"
              "continued comment contents\n"
              'char *part = "https://example.org";\nint entry(void) { return 7; }\n')
    output = preprocess_donor_unit(invocation, source)
    assert output is not None
    assert 'int entry' in output and 'https://example.org' in output
    assert 'continued comment contents' not in output


def test_replacement_source_is_literal_and_checkout_local(tmp_path):
    from types import SimpleNamespace

    from decompnet.app.donor_build import Settings

    root = tmp_path / 'donor'
    sources = root / 'src'
    sources.mkdir(parents=True)
    source = sources / 'replacement.c'
    source.write_text('int original(void) { return 7; }\n')
    outside = tmp_path / 'outside.c'
    outside.write_text('outside')
    (sources / 'escape.c').symlink_to(outside)
    (root / 'Makefile').write_text(
        'build/non_matching/src/unit.c.o: private REPLACE_FUNC_BODY += '
        'original=build/non_matching/src/replacement.c.o.linked.elf.bound.elf '
        'escape=build/src/escape.c.o traversal=build/src/../outside.c.o '
        'variable=$(OTHER) cpp=build/src/other.cpp.o\n')
    settings = Settings(SimpleNamespace(root=root))
    unit = SimpleNamespace(base=root / 'build/non_matching/src/unit.c.o')
    assert settings.replacement_source(unit, 'original') == (source, None)
    assert all(settings.replacement_source(unit, name) is None for name in ('escape', 'traversal', 'variable', 'cpp'))

def test_cpp_replacement_uses_pinned_driver_and_literal_recipe(tmp_path):
    from types import SimpleNamespace

    from decompnet import toolchains
    from decompnet.app.donor_build import Settings

    source = tmp_path / 'src' / 'replacement.cpp'
    source.parent.mkdir()
    source.write_text('extern "C" int entry() { return 7; }\n')
    (tmp_path / 'Makefile').write_text(
        'IDO_DIR := tools/ido-static-recomp/build/7.1/out\n'
        'NCC := $(IDO_DIR)/NCC\n'
        'FLAGS := -O2 -mips2 -32\n'
        'OBJECT := build/src/replacement.cpp.o\n'
        'ENTRIES := entry another\n'
        'build/src/%.cpp.o: src/%.cpp\n'
        '\t$(NCC) -c $(FLAGS) -o $@ $<\n'
        'build/src/unit.c.o: private REPLACE_FUNC_BODY += $(foreach f,$(ENTRIES),$(f)=$(OBJECT))\n')
    settings = Settings(SimpleNamespace(root=tmp_path))
    unit = SimpleNamespace(base=tmp_path / 'build/src/unit.c.o')
    profile = {'toolchain': 'ido-7.1-ncc', 'flags': ['-O2', '-mips2', '-32']}
    assert settings.replacement_source(unit, 'entry') == (source, profile)
    assert settings.replacement_source(unit, 'another') == (source, profile)
    assert settings.replacement_source(unit, 'unlisted') is None
    assert toolchains.path(profile['toolchain']) == toolchains.path('ido-7.1')
    assert toolchains.get(profile['toolchain']).source_extension == 'cpp'


def test_literal_c_recipe_uses_the_selected_recipe_flags_for_each_object(tmp_path):
    from types import SimpleNamespace

    from decompnet.app.donor_build import Settings

    root = tmp_path
    (root / 'src').mkdir()
    replacement = root / 'src' / 'replacement.c'
    replacement.write_text('int entry(void) { return 3; }\n')
    (root / 'Makefile').write_text(
        'CC := tools/ido-static-recomp/build/7.1/out/cc\n'
        'CFLAGS := -G 0 -non_shared\nOPT_FLAGS := -O2\n'
        'build/src/unit.c.o: REPLACE_FUNC_BODY += entry=build/src/replacement.c.o\n'
        'build/src/unit.c.o: src/unit.c\n'
        '\t$(CC) -c $(CFLAGS) -O3 -DNATIVE -o $@ build/$<\n'
        'build/src/replacement.c.o: CC := tools/ido-static-recomp/build/5.3/out/cc\n'
        'build/src/replacement.c.o: src/replacement.c\n'
        '\t$(CC) -c $(CFLAGS) -O1 -o $@ $<\n')
    profiles = Settings(SimpleNamespace(root=root))
    unit = SimpleNamespace(base=root / 'build/src/unit.c.o', source='src/unit.c', scratch=None)
    assert profiles.for_unit(unit) == {
        'toolchain': 'ido-7.1', 'flags': ['-G', '0', '-non_shared', '-O3', '-DNATIVE']}
    assert profiles.replacement_source(unit, 'entry') == (replacement, {
        'toolchain': 'ido-5.3', 'flags': ['-G', '0', '-non_shared', '-O1']})
    assert profiles.fallback_for_unit(unit, profiles.for_unit(unit)) == {
        'toolchain': 'ido-7.1', 'flags': ['-G', '0', '-non_shared', '-O2', '-DNATIVE']}


def test_a_replacement_recipe_can_reproduce_bytes_its_resident_profile_cannot(tmp_path):
    from types import SimpleNamespace

    import pytest

    from decompnet import toolchains
    from decompnet.app.donor_build import Settings
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('ido-7.1'):
        pytest.skip('requires the installed pinned IDO 7.1 compiler')
    (tmp_path / 'src').mkdir()
    path = tmp_path / 'src' / 'body.c'
    source = 'int entry(int x) { int result = x; if (result) result += 1; return result; }'
    path.write_text(source)
    (tmp_path / 'Makefile').write_text(
        'CC := tools/ido-static-recomp/build/7.1/out/cc\n'
        'CFLAGS := -G 0 -non_shared\nOPT_FLAGS := -O0\n'
        'build/src/resident.c.o: REPLACE_FUNC_BODY += entry=build/src/body.c.o\n'
        'build/src/body.c.o: src/body.c\n'
        '\t$(CC) -c $(CFLAGS) -O2 -o $@ $<\n')
    settings = Settings(SimpleNamespace(root=tmp_path))
    unit = SimpleNamespace(base=tmp_path / 'build/src/resident.c.o', source='src/resident.c', scratch=None)
    _, replacement = settings.replacement_source(unit, 'entry')

    def code(profile):
        cfg = compiler_config(tmp_path, {'compiler': profile})
        result = compile_source(cfg, '', source)
        assert result.ok, result.log
        return extract_function(result.obj, 'entry').code

    expected = {'toolchain': 'ido-7.1', 'flags': ['-G', '0', '-non_shared', '-O2']}
    assert code(replacement) == code(expected)
    assert code(settings.for_unit(unit)) != code(expected)


def test_replacement_preprocesses_once_per_compiler_and_keeps_each_destination_name(tmp_path, monkeypatch):
    from decompnet.app import donor_unit
    from decompnet.compiler import CompilerConfig

    path = tmp_path / 'replacement.c'
    path.write_text('typedef int Word;\nWord donor(Word value) { return value; }\n')
    calls = []

    def preprocessor(cfg, source):
        calls.append(cfg.cmd)
        return source

    monkeypatch.setattr(donor_unit, 'preprocess_donor_unit', preprocessor)
    donor = {'name': 'donor', 'meta': None}
    cache = {}
    project = {'root': str(tmp_path)}
    first = CompilerConfig(['cc', '-O2'])
    second = CompilerConfig(['cc', '-O1'])

    def source(name, config):
        body, header = donor_unit.replacement(project, donor, name, config, path, cache)
        assert 'typedef int Word;' in header
        return body

    assert 'Word alpha(Word value)' in source('alpha', first)
    assert 'Word beta(Word value)' in source('beta', first)
    assert 'Word gamma(Word value)' in source('gamma', second)
    assert calls == [first.cmd, second.cmd]


def test_donor_aliases_preserve_format_and_only_replace_complete_code_identifiers():
    from decompnet.app.source_symbol_recovery import rename

    source = 'f(foo, foobar, foo_2); /* foo */ char *s = "foo"; // f\n'
    expected = 'foo(other, foobar, foo_2); /* foo */ char *s = "foo"; // f\n'
    assert rename(source, {'f': 'foo', 'foo': 'other'}) == expected
    assert rename(source, {'missing': 'other', 'foo': 'foo'}) == source


def test_compact_drops_unrelated_redeclarations_for_ido(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('ido-7.1'):
        pytest.skip('IDO 7.1 is unavailable')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'ido-7.1', 'flags': ['-O2', '-G', '0']}})
    source = 'int entry(void) { return shared[2]; }'
    required = 'extern short shared[];'
    header = 'extern int unused;\nextern short unused[];\n' + required
    conflict = compile_source(cfg, header, source)
    assert not conflict.ok and "redeclaration of 'unused'" in conflict.log
    body, context = ds.compact(header + '\n' + source, 'entry')
    rebuilt = compile_source(cfg, context, body)
    assert rebuilt.ok, rebuilt.log
    reference = compile_source(cfg, required, source)
    assert reference.ok, reference.log
    assert extract_function(rebuilt.obj, 'entry') == extract_function(reference.obj, 'entry')


def test_cpp_replacement_can_try_the_resident_optimization_without_replacing_its_compiler(tmp_path):
    from decompnet import toolchains
    from decompnet.app.donor_build import replacement_profiles
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config

    if not toolchains.installed('ido-7.1-ncc'):
        pytest.skip('needs IDO 7.1 NCC')
    path = tmp_path / 'replacement.cpp'
    source = 'extern "C" int entry(int x) { int y = x + 2; return y * 3; }'
    replacement = {'toolchain': 'ido-7.1-ncc', 'flags': ['-O2', '-G', '0', '-mips2', '-non_shared']}
    resident = {'toolchain': 'ido-7.1', 'flags': ['-O0', '-G', '8', '-DNON_MATCHING']}
    declared, alternate = replacement_profiles(path, replacement, resident)
    assert declared == replacement
    assert alternate == {'toolchain': 'ido-7.1-ncc', 'flags': ['-O0', '-G', '0', '-mips2', '-non_shared']}
    expected = replacement | {'flags': ['-O0', *replacement['flags'][1:]]}

    def instruction_bytes(profile):
        built = compile_source(compiler_config(tmp_path, {'compiler': profile}), '', source)
        assert built.ok, built.log
        return extract_function(built.obj, 'entry').code

    assert instruction_bytes(alternate) == instruction_bytes(expected)
    assert instruction_bytes(alternate) != instruction_bytes(declared)
    assert replacement_profiles(path, replacement, resident | {'flags': ['-O0', '-O1']}) == [replacement]


def test_cpp_replacement_finds_a_unique_out_of_class_method(tmp_path, monkeypatch):
    from decompnet.app import donor_unit
    from decompnet.compiler import CompilerConfig

    source = ('class Camera { public: void update(); };\n'
              'void Camera::update() { int value = 1; }\n')
    path = tmp_path / 'method.cpp'
    path.write_text(source)
    monkeypatch.setattr(donor_unit, 'preprocess_donor_unit', lambda config, text: text)
    donor = {'name': 'exported_name', 'meta': None}
    body, header = donor_unit.replacement({'root': str(tmp_path)}, donor, 'target_entry',
                                          CompilerConfig(['cc', '-O2']), path)
    assert 'void target_entry();' in header
    assert 'void Camera::target_entry()' in body

    path.write_text(source + 'void Other::draw() { int value = 2; }\n')
    assert donor_unit.replacement({'root': str(tmp_path)}, donor, 'target_entry',
                                  CompilerConfig(['cc', '-O2']), path) is None
