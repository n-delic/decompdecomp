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
