"""Homemade privileged-code objects, never SDK/game code."""
import json
import subprocess

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.app import sdk_assembly, source_assembly
from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_names import name_functions
from decompnet.app.twins import rom_print
from decompnet.rom import match
from decompnet.rom.symbols import SymbolTable


@pytest.fixture(params=['external','owned_data'])
def example(tmp_path,monkeypatch,request):
    if not toolchains.installed('binutils-mips'):pytest.skip('Pinned assembler unavailable; no downloads')
    tools=toolchains.path('binutils-mips');assembler=next(tools.glob('*-as'));linker=next(tools.glob('*-ld'))
    source=tmp_path/'sample.s'
    source.write_text('''.text
.set noreorder
.globl sdkToy
.ent sdkToy
sdkToy:
 mfc0 $v0,$12
 nop
 lui $t0,%hi(external)
 lw $t1,%lo(external)($t0)
 xori $t1,$t1,3
 xor $v0,$v0,$t1
 mtc0 $v0,$12
 nop
 jr $ra
 nop
.end sdkToy
'''+('\n.rdata\n.globl external\nexternal: .word 0x12345678\n' if request.param=='owned_data' else ''))
    obj=tmp_path/'sample.o'
    subprocess.run([str(assembler),'-EB','-32','-mips3','-o',str(obj),str(source)],check=True,capture_output=True)
    layout=tmp_path/'sample.ld';layout.write_text('SECTIONS { . = 0x80001000; .text : { *(.text) } . = 0x80002000; .rodata : { *(.rodata*) *(.rdata*) } } external = 0x80002000;')
    image=tmp_path/'sample.elf';subprocess.run([str(linker),'-EB','-T',str(layout),'-o',str(image),str(obj)],check=True,capture_output=True)
    with image.open('rb') as h:code=ELFFile(h).get_section_by_name('.text').data()[:40]
    net=RomNetwork(tmp_path/'db',tmp_path)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-G0']}}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,arch,big_endian,config) VALUES(?,?,?,?,?,?)',('game','Game',str(tmp_path),'mips',1,json.dumps(cfg))).lastrowid
        fid=db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,target,relocs,status,meta) VALUES(?,?,?,?,?,?,?,?,?)',(pid,'func_80001000',0x80001000,40,10,code,json.dumps([[8,5,0x80002000],[12,6,0x80002000]]),'asm','{}')).lastrowid
    monkeypatch.setattr(net,'symbols',lambda _:SymbolTable())
    monkeypatch.setattr(net,'_asm_text',lambda *a:'')
    monkeypatch.setattr(net,'reader',lambda *a:lambda addr,size:bytes.fromhex('12345678')[:size] if addr==0x80002000 else None)
    monkeypatch.setattr(net,'_rom_target',lambda fn:match.RomTarget(fn['name'],fn['vram'],bytes(fn['target']),json.loads(fn['relocs']),True))
    net.twins.import_sdk_objects(obj,'Toy SDK')
    return net,net.project_row('game'),fid,obj


def test_indexed_sdk_recognized_linked_named_without_source_rename_or_c_credit(example,monkeypatch):
    net,proj,fid,obj=example
    monkeypatch.setattr(net,'rename_symbols',lambda *a:pytest.fail('Assembly display names must not rewrite C'))
    assert name_functions(net,'game')==0
    fn=net._function('func_80001000','game');proof=source_assembly.detail(proj,fn)
    assert proof['verified'] and proof['kind']=='sdk' and proof['name']=='sdkToy' and proof['bytes']==40
    assert fn['status']=='asm' and fn['best_score'] is None
    assert json.loads(fn['meta'])['sdk_display']['name']=='sdkToy'
    assert net.function_detail('game',fn['name'],include_hints=False)['display_name']=='sdkToy'
    assert source_assembly.breakdown(net,proj)['verified_sdk_assembly']==1
    changed=dict(fn);changed['target']=b'bad!'+bytes(fn['target'])[4:]
    assert not source_assembly.verified(proj,changed)
    agent,_=net.local_agent('worker')
    assert net.claim_row(agent,'game') is None
    obj.write_bytes(obj.read_bytes()+b'changed')
    assert not source_assembly.verified(proj,fn)
    from decompnet.app.sdk_identity import identify
    assert fid not in identify(net,proj,{},include_asm=True)


def test_signature_only_wrong_link_data_conflict_and_changed_library_never_verified(example,monkeypatch):
    net,proj,fid,path=example;fn=net._function('func_80001000','game')
    key=rom_print(fn,'mips',True,hex).key;hint={'names':['sdkToy'],'corpora':['Toy SDK'],'fingerprint':key}
    original=net._rom_target
    monkeypatch.setattr(net,'_rom_target',lambda f:match.RomTarget(f['name'],f['vram'],b'nope'+bytes(f['target'])[4:],json.loads(f['relocs']),True))
    assert not sdk_assembly.verify(net,proj,fn,hint)
    monkeypatch.setattr(net,'_rom_target',original)
    path.write_bytes(path.read_bytes()+b'changed')
    assert not sdk_assembly.verify(net,proj,fn,hint)
    assert not source_assembly.detail(proj,fn)


def test_sdk_owned_data_is_verified_not_only_instruction_signature(example,monkeypatch):
    net,proj,fid,path=example;fn=net._function('func_80001000','game')
    from io import BytesIO
    elf=ELFFile(BytesIO(path.read_bytes()))
    if elf.get_section_by_name('.rodata') is None:pytest.skip('External reference case has no owned data')
    monkeypatch.setattr(net,'reader',lambda *a:lambda addr,size:b'\0'*size)
    assert not sdk_assembly.verify(net,proj,fn,{'names':['sdkToy'],'corpora':['Toy SDK']})


def test_disabled_corpus_and_live_claim_prevent_automatic_proof(example):
    net,proj,fid,path=example
    with net.store.tx() as db:
        cfg=json.loads(proj['config']);cfg['sdk_libraries']=[]
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    sdk_assembly.discover(net,net.project_row('game'))
    assert not source_assembly.detail(net.project_row('game'),net._function('func_80001000','game'))


def test_ambiguous_linked_names_do_not_get_identity(example):
    net,proj,fid,path=example
    source=path.with_suffix('.s');text=source.read_text();body=text.split('.rdata')[0]
    source.write_text(text+'\n.text\n'+body.replace('sdkToy','anotherToy'))
    assembler=next(toolchains.path('binutils-mips').glob('*-as'))
    subprocess.run([str(assembler),'-EB','-32','-mips3','-o',str(path),str(source)],check=True,capture_output=True)
    net.twins.import_sdk_objects(path,'Toy SDK')
    assert sdk_assembly.discover(net,proj)==0
    assert not source_assembly.detail(proj,net._function('func_80001000','game'))


def test_truncated_lead_sets_are_not_misreported_as_unique(example):
    net,proj,fid,path=example
    with net.store.tx() as db:
        for i in range(33):db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',('Toy SDK','mips',1,'candidate'+str(i),'shape'+str(i),40))
    assert sdk_assembly.discover(net,proj)==0
    assert not source_assembly.detail(proj,net._function('func_80001000','game'))


def test_another_workers_claim_is_preserved(example):
    net,proj,fid,path=example;fn=net._function('func_80001000','game')
    agent,_=net.local_agent('other')
    assert net.permuter._hold(agent,fn,count_turn=False,seconds=60)
    assert not sdk_assembly.verify(net,proj,fn,{'names':['sdkToy'],'corpora':['Toy SDK']})
    assert net.project_store(proj['id']).one('SELECT agent_id FROM claims WHERE function_id=?',fid)['agent_id']==agent.id
