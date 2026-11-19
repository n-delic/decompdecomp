"""Export builds index function source files once, including shared-source aliases."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from decompnet.rom import export as rom_export


def builder(tmp_path, functions, monkeypatch):
    script = tmp_path/'build.py'
    script.write_text(Path(rom_export.__file__).with_name('export_build.py').read_text())
    (tmp_path/'build.json').write_text(json.dumps({'functions':functions,'compiler':{'toolchain':'test','flags':[]}}))
    spec = importlib.util.spec_from_file_location('test_export_builder', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'binutils', lambda _: 'unused-assembler')
    monkeypatch.setattr(module, 'compiler_config', lambda *_: SimpleNamespace(cmd=['compiler','{src}','{obj}']))
    def compile_object(cfg, root, argv, *, inputs, outputs):
        (root/outputs[0]).write_bytes(b'object')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(module, 'run_in', compile_object)
    monkeypatch.setattr(module, 'trim', lambda _:None)
    monkeypatch.setattr(module, 'weaken_data', lambda _:None)
    return module


def test_building_many_functions_reads_each_source_path_once(tmp_path, monkeypatch):
    functions = [{'name':f'f{i}','vram':i*16,'c':f'src/f{i}.c'} for i in range(64)]
    module = builder(tmp_path, functions, monkeypatch)
    reads = []
    monkeypatch.setattr(module, 'c_source', lambda fn: reads.append(fn['name']) or fn['c'])
    module.build_objects([('c',fn['c'],f"build/{fn['name']}.o") for fn in functions])
    assert reads == [fn['name'] for fn in functions]
    assert len(list((tmp_path/'build').glob('*.o'))) == len(functions)


def test_source_aliases_keep_existing_compiler_and_entry_selection(tmp_path, monkeypatch):
    functions = [{'name':'first','vram':0,'c':'src/shared.c','entry_only':True},
                 {'name':'last','vram':16,'c':'src/shared.c'}]
    module = builder(tmp_path, functions, monkeypatch)
    monkeypatch.setattr(module, 'c_source', lambda fn:fn['c'])
    compilers, entries = [], []
    def compiler_for(cfg, address, fn):
        compilers.append(fn['name'])
        return cfg['compiler']
    monkeypatch.setattr(module, 'compiler_for', compiler_for)
    monkeypatch.setattr(module, 'normalize_entry', lambda fn,_:entries.append(fn['name']))
    module.build_objects([('c','src/shared.c','build/shared.o')])
    assert compilers == ['last'] and entries == ['first']


def test_check_accepts_the_c_symbol_and_old_splitter_name(tmp_path, monkeypatch):
    from decompnet import compiler
    from decompnet.rom import export_entry, match, split

    from .test_export_entry import object_file

    fn = {'name':'old_entry','symbol':'renamed_entry','vram':0x80001000,
          'c':'src/entry.c','asm':'target.s','seg':'game'}
    module = builder(tmp_path, [fn], monkeypatch)
    module.B.update(symbols={}, labels={}, big_endian=True, segments=[], image='baserom')
    (tmp_path/'baserom').write_bytes(b'original')
    (tmp_path/'src').mkdir()
    (tmp_path/fn['c']).write_text('int renamed_entry(void) { return 3; }')
    monkeypatch.setattr(module, 'setup', lambda:None)
    obj = export_entry.object_for(object_file(), 'renamed_entry', bytes.fromhex('03e0000824020003'))
    monkeypatch.setattr(compiler, 'compile_source', lambda *args, **kwargs:SimpleNamespace(ok=True, obj=obj))
    monkeypatch.setattr(split, 'parse_function', lambda *args:None)
    monkeypatch.setattr(match, 'target_from_split', lambda *args:None)
    def link(obj, symbol, target, table, read):
        assert symbol == 'renamed_entry'
        assert table.resolve('old_entry') == table.resolve('renamed_entry') == fn['vram']
        return SimpleNamespace(exact=True, placements={})
    monkeypatch.setattr(match, 'link', link)
    assert module.check('old_entry')
    assert module.check('renamed_entry')
    assert not module.B['functions'][0].get('entry_only')


def test_rebuild_verifies_image_with_mixed_mips_abis(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from decompnet import toolchains

    if toolchains.installed('binutils-mips') is None:
        pytest.skip('needs binutils-mips')
    module = builder(tmp_path, [], monkeypatch)
    prefix = toolchains.binary('binutils-mips').parent / 'mips-ps2-decompals-'
    monkeypatch.setattr(module, 'binutils', lambda tool: str(prefix) + tool)
    (tmp_path / 'build').mkdir()
    todo = []
    expected = bytes.fromhex('03e0000800000000')
    for abi, word in [('32', '0x03e00008'), ('eabi', '0')]:
        src, obj = f'{abi}.s', f'build/{abi}.o'
        (tmp_path / src).write_text(f'.text\n.word {word}\n')
        subprocess.run([module.binutils('as'), '-EB', '-march=vr4300', f'-mabi={abi}',
                        '-no-pad-sections', '-o', obj, src], cwd=tmp_path, check=True)
        todo.append(('asm', src, obj))
    (tmp_path / 'baserom').write_bytes(expected)
    module.B.update(image='baserom', output='build/game.bin', ld_flags=['-EB'])
    monkeypatch.setattr(module, 'setup', lambda: None)
    monkeypatch.setattr(module, 'objects', lambda: (todo, {}, []))
    monkeypatch.setattr(module, 'build_objects', lambda _: None)
    monkeypatch.setattr(module, 'fill_gaps', lambda _: [])
    monkeypatch.setattr(module, 'linker_script', lambda *_: f'SECTIONS {{ .text 0x80000000 : {{ *(.text) }} /DISCARD/ : {{ {module.DISCARD} }} }}')
    assert module.build()
    assert (tmp_path / 'build/game.bin').read_bytes() == expected

    (tmp_path / 'baserom').write_bytes(bytes(len(expected)))
    assert not module.build()


def test_mips_abi_retry_requires_only_abi_diagnostics(tmp_path, monkeypatch):
    module = builder(tmp_path, [], monkeypatch)
    diagnostics = ('ld: entry.o: linking 32-bit code with 64-bit code\n'
                   'ld: entry.o: failed to merge target specific data of file entry.o\n')
    assert module.mixed_mips_abi(diagnostics)
    assert not module.mixed_mips_abi(diagnostics + 'ld: undefined reference to missing\n')
    assert not module.mixed_mips_abi('ld: cannot find entry.o\n')


def test_placement_index_uses_rom_offsets_for_overlays(tmp_path, monkeypatch):
    module = builder(tmp_path, [], monkeypatch)
    placements = module.placements_by_rom([
        (0x80000000, 0x200, 'overlay2.o'),
        (0x80000000, 0x100, 'overlay1.o'),
        (0x80000004, 0x104, 'tail.o'),
        (0x80000008, None, 'unmapped.o'),
    ])
    assert placements(0x100, 0x200) == [(0x80000000, 0x100, 'overlay1.o'), (0x80000004, 0x104, 'tail.o')]
    assert placements(0x200, 0x204) == [(0x80000000, 0x200, 'overlay2.o')]
    assert placements(0x105, 0x200) == []


def test_pinned_default_compiler_rebuilds_distinct_objects_concurrently(tmp_path, monkeypatch):
    from threading import Event

    from decompnet.rom import export_workers

    functions = [{'name': name, 'vram': i * 8, 'c': f'{name}.c'} for i, name in enumerate(('one', 'two'))]
    module = builder(tmp_path, functions, monkeypatch)
    module.B['compiler'] = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2'], 'timeout': 20}
    for fn in functions:
        (tmp_path / fn['c']).touch()
    monkeypatch.setattr(export_workers.os, 'cpu_count', lambda: 4)
    monkeypatch.setattr(export_workers.compiler, 'concurrency_limit', lambda: 2)
    compile_object = module.run_in
    second_started = Event()

    def wait_for_second(cfg, root, argv, *, inputs, outputs):
        if outputs == ['one.o']:
            assert second_started.wait(3)
        else:
            second_started.set()
        return compile_object(cfg, root, argv, inputs=inputs, outputs=outputs)

    monkeypatch.setattr(module, 'run_in', wait_for_second)
    module.build_objects([('c', fn['c'], f"{fn['name']}.o") for fn in functions])
    assert (tmp_path / 'one.o').read_bytes() == (tmp_path / 'two.o').read_bytes() == b'object'
