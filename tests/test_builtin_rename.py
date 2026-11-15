"""Real compiler proof using homemade code, never SDK or game fixtures."""
import json
import subprocess
from io import BytesIO

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app.engine import RomNetwork
from decompnet.compiler import compile_source
from decompnet.elfobj import _function_symbols
from decompnet.project import compiler_config, compiler_for


def test_period_gcc_builtin_rename_preserves_external_call_bytes(tmp_path,monkeypatch):
    if not toolchains.installed('gcc-2.7.2-kmc') or not toolchains.installed('binutils-mips'):
        pytest.skip('Pinned period GCC and linker unavailable; no downloads')
    profile={'toolchain':'gcc-2.7.2-kmc','flags':['-G0','-mips3','-mgp32','-mfp32','-O2']}
    header='extern void *oldCopy(void *, const void *, unsigned int);'
    source='void caller(char *p,char *q){oldCopy(p,q,16);}'
    original=compile_source(compiler_config(tmp_path,{'compiler':profile}),header,source)
    assert original.ok,original.log
    size=_function_symbols(ELFFile(BytesIO(original.obj)))['caller'][2]
    obj=tmp_path/'caller.o';obj.write_bytes(original.obj)
    layout=tmp_path/'layout.ld';layout.write_text('SECTIONS { . = 0x80001000; .text : { *(.text) } } oldCopy = 0x80002000;')
    image=tmp_path/'caller.elf'
    linker=next(toolchains.path('binutils-mips').glob('*-ld'))
    subprocess.run([str(linker),'-EB','-T',str(layout),'-o',str(image),str(obj)],capture_output=True,check=True)
    with image.open('rb') as h:code=ELFFile(h).get_section_by_name('.text').data()[:size]
    net=RomNetwork(tmp_path/'db',tmp_path)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'compiler':profile}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',('toy','Toy',str(tmp_path),'mips',1,json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status,matched_source,best_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (pid,'caller',0x80001000,0,size,size//4,code,json.dumps([[8,4,0x80002000]]),'matched',source,source))
        db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status) VALUES(?,?,?,?,?,?)',(pid,'oldCopy',0x80002000,4,1,'asm'))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'oldCopy',0x80002000,4,'func'))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at,unit) VALUES(?,1,?,0,'')",(pid,header))
    monkeypatch.setattr(net,'reader',lambda *a:lambda addr,size:None)
    agent,_=net.local_agent('you')
    result=net.rename_symbol(agent,'toy','oldCopy','memcpy')
    assert result.startswith('✅') and 'Builtin optimizations disabled in 1' in result,result
    row=net._function('caller','toy');proj=net.project_row('toy')
    assert '-fno-builtin' in compiler_for(net.cfg(proj),row['vram'],row)['flags']
    assert net.cfg(proj)['compiler']==profile
    assert net.regression_test(proj,header.replace("oldCopy","memcpy"),only=[row])==[]
