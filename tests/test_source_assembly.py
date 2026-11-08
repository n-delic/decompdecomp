import hashlib
import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet.app import source_assembly as assembly
from decompnet.app import source_preparation as prep
from decompnet.app.saves import restored_aside
from decompnet.compiler import compile_assembly
from decompnet.project import CompilerConfig, compiler_config


def fixture(tmp_path,monkeypatch,exact=True):
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE reuse_trials(function_id,signature,outcome,reason, UNIQUE(function_id,signature))')
    db.execute('CREATE TABLE functions(id,name,project_id,vram,size,target,status,meta,gone_mark,relocs)')
    db.execute('INSERT INTO functions VALUES(1,"entry",1,4096,8,?,"open","{}",NULL,"[]")',(b'12345678',))
    class Store:
        def tx(self):return db
        def one(self,sql,*args):
            if 'FROM claims' in sql:return {'agent_id':7,'expires_at':float('inf')}
            return db.execute(sql,args).fetchone()
        def all(self,sql,*args):return db.execute(sql,args).fetchall()
    cfg={'compiler':{'toolchain':'ido-5.3','flags':['-O2']},'rom':{'sha1':'owned-game'}}
    proj={'id':1,'slug':'game','root':str(tmp_path),'config':json.dumps(cfg)}
    net=SimpleNamespace(store=Store(),is_rom=lambda p:True,fork_slug=lambda s:None,
        project_row=lambda s:proj,_function=lambda n,s:dict(db.execute('SELECT * FROM functions WHERE name=?',(n,)).fetchone()),
        local_agent=lambda n:(SimpleNamespace(id=7),None),_event=lambda *a:None,
        permuter=SimpleNamespace(_hold=lambda *a,**kw:True,_let_go=lambda *a:None),
        progress=SimpleNamespace(judging=lambda s:nullcontext(),_project_lock=lambda s:nullcontext()),
        _evaluate=lambda *a:(object(),exact,[]))
    net.project_store = lambda _project: net.store
    monkeypatch.setattr(prep,'inputs',lambda *a:('stable',))
    monkeypatch.setattr(assembly,'compiler_config',lambda *a:object())
    monkeypatch.setattr(assembly.toolchains,'installed',lambda t:Path('/installed'))
    monkeypatch.setattr(assembly,'compile_assembly',lambda *a:SimpleNamespace(ok=True,obj=b'compiled',log=''))
    (tmp_path/'body.S').write_text('.text\n.globl original\noriginal:\n jr $31\n nop\n')
    settings={'root':str(tmp_path),'assembly_mappings':'entry = original @ body.S'}
    return net,proj,settings,db


def test_discovery_is_only_a_hypothesis_and_preserves_status(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch)
    leads=assembly.scan(net,proj,cfg)
    assert [(x['function'],x['symbol']) for x in leads]==[('entry','original')]
    assert db.execute('SELECT status FROM functions').fetchone()[0]=='open'


def test_only_exact_linked_assembly_is_classified_without_c_credit(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch)
    result=assembly.run(net,proj,cfg,lambda s:None)
    assert result=={'tested':1,'verified':1,'cached':0,'errors':[]}
    fn=net._function('entry','game');assert fn['status']=='asm' and assembly.verified(proj,fn)
    assert assembly.breakdown(net,proj)=={'exact_c':0,'verified_source_assembly':1,'verified_sdk_assembly':0,'other_assembly':0,'open':0,'exact_c_bytes':0,'total_bytes':8}
    fn['target']=b'changed!';assert not assembly.verified(proj,fn)
    restored=restored_aside(json.loads(net._function('entry','game')['meta']))
    assert restored['verified_assembly']['verified'] is False


def test_failed_data_or_code_judge_cannot_create_assembly_proof(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch,exact=False)
    result=assembly.run(net,proj,cfg,lambda s:None)
    assert result['verified']==0 and result['errors']
    assert net._function('entry','game')['status']=='open'


def test_assembly_compilation_rejects_file_and_binary_directives():
    cfg=CompilerConfig(cmd=['cc','-c','{src}','-o','{obj}'])
    for text in ['.incbin "game.bin"','.include "/tmp/foreign.s"','#include "unflattened.h"']:
        assert not compile_assembly(cfg,'',text).ok


@pytest.mark.parametrize('toolchain,flags',[('ido-5.3',['-O2','-mips2','-G','0','-non_shared']),('gcc-2.7.2-kmc',['-O2','-G0','-mips3','-mgp32','-mfp32']),('gcc-2.7.2-psx',['-O2','-G0'])])
def test_installed_pinned_driver_assembles_preprocessed_homemade_source(tmp_path,toolchain,flags):
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.elfobj import _function_symbols
    if toolchains.installed(toolchain) is None:pytest.skip('needs installed '+toolchain)
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':toolchain,'flags':flags}})
    result=compile_assembly(cfg,'#define REG_RA $31','.text\n.set noreorder\n.globl entry\n.ent entry\nentry:\n jr REG_RA\n addiu $2,$4,1\n.end entry\n')
    assert result.ok,result.log
    e=ELFFile(BytesIO(result.obj));section,start,size=_function_symbols(e)['entry']
    assert size==8
    code=e.get_section(section).data()[start:start+8];order='little' if e.little_endian else 'big'
    assert [int.from_bytes(code[i:i+4],order) for i in (0,4)]==[0x03e00008,0x24820001]
    assert hashlib.sha256(result.obj).hexdigest()


def test_restored_provenance_never_restores_a_trusted_verdict():
    proof={'verified':True,'source_file':'original.s','fingerprint':'old-target'}
    result=restored_aside({'verified_assembly':proof,'not_c':{}})
    assert result['verified_assembly']['verified'] is False
    assert proof['verified'] is True


def test_assembly_settings_validate_source_roots_and_mapping_extensions(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch)
    db.execute('CREATE TABLE settings(key PRIMARY KEY,value)')
    data={**cfg,'enabled':True}
    saved=prep.configure(net,proj,data)
    assert saved['assembly_mappings']==data['assembly_mappings']
    with pytest.raises(prep.NetworkError,match='source files'):
        prep.configure(net,proj,{**data,'assembly_mappings':'entry = original @ body.bin'})
    with pytest.raises(prep.NetworkError,match='must agree'):
        prep.configure(net,proj,{**data,'aliases':'original = other'})


def test_preprocessor_cannot_generate_a_hidden_binary_include(tmp_path):
    from decompnet import toolchains
    if toolchains.installed('ido-5.3') is None:pytest.skip('needs installed IDO 5.3')
    cfg=compiler_config(tmp_path,{'compiler':{'toolchain':'ido-5.3','flags':['-O2','-mips2','-G','0','-non_shared']}})
    result=compile_assembly(cfg,'#define DIRECTIVE(name) .name','DIRECTIVE(incbin) "not_allowed.bin"')
    assert not result.ok and 'must not include' in result.log


def test_unchanged_proofs_and_failures_do_not_keep_recompiling(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch)
    assert assembly.run(net,proj,cfg,lambda s:None)['verified']==1
    assert assembly.run(net,proj,cfg,lambda s:None)['cached']==1
    assert assembly.run(net,proj,cfg,lambda s:None)['tested']==0
    net._evaluate=lambda *a:(object(),False,[])
    (tmp_path/'body.S').write_text('.text\n.globl original\noriginal:\n jr $31\n addiu $2,$0,1')
    assert assembly.run(net,proj,cfg,lambda s:None)['tested']==2
    assert assembly.run(net,proj,cfg,lambda s:None)['tested']==0
    assert assembly.run(net,proj,cfg,lambda s:None,retry=True)['tested']==2


def test_assembly_reorder_profile_is_private_and_each_candidate_is_judged(tmp_path,monkeypatch):
    net,proj,cfg,db=fixture(tmp_path,monkeypatch)
    calls=[]
    def judge(*args):
        calls.append(1);return object(),len(calls)==2,[]
    net._evaluate=judge
    assert assembly.run(net,proj,cfg,lambda s:None)['tested']==2
    proof=json.loads(net._function('entry','game')['meta'])['verified_assembly']
    assert proof['compiler']['flags']==['-O1']
    assert json.loads(proj['config'])['compiler']['flags']==['-O2']


@pytest.mark.parametrize('mode',['wsl','linux-vm'])
def test_assembly_stages_use_the_same_explicit_remote_work_path(monkeypatch,mode):
    from subprocess import CompletedProcess

    from decompnet import compiler
    cfg=CompilerConfig(cmd=['cc','-c','{src}','-o','{obj}'],wsl=mode=='wsl',linux_vm=mode=='linux-vm')
    monkeypatch.setattr(compiler,'_root',lambda *a:'/work-assembly')
    calls=[]
    def execute(cfg,work,argv,timeout,**stage):
        calls.append(stage)
        output=stage['outputs'][0]
        (work/output).write_bytes(b'.text\n' if output=='func.s' else b'compiled object')
        return CompletedProcess(argv,0,'','')
    monkeypatch.setattr(compiler,'_exec',execute)
    assert compile_assembly(cfg,'','.text').ok
    assert [x['stage'] for x in calls]==['/work-assembly','/work-assembly']
    assert [x['inputs'] for x in calls]==[['input.c'],['func.s']]
