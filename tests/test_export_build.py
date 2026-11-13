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
