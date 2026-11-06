from decompnet.app import buffer_address_repairs as buffers
from decompnet.app import helper_call_diagnostics, source_diagnostics
from decompnet.app import source_preparation as prep
from decompnet.arch.mips_runtime import entry_context


def test_same_file_definition_preserved_with_plain_alternative(tmp_path):
    (tmp_path/'f.c').write_text('static unsigned long long clock = 62500000ULL;\nunsigned long long original(void){return clock;}')
    (tmp_path/'f.h').write_text('extern unsigned long long clock;')
    cfg={'root':str(tmp_path),'header':str(tmp_path/'f.h')}
    reader=prep.Reader(cfg)
    pairs=prep.prepare_variants(reader,cfg,('dest','original','f.c'),{'original':'dest'},'')
    assert any('mapped-file object clock' in hint for hint in reader.advisories)
    assert len(pairs)==2 and 'clock = 62500000ULL;' in pairs[0][0]
    assert 'clock = 62500000ULL;' not in pairs[1][0]
    assert '#line' in pairs[0][0] and 'return clock;' in pairs[0][0]


def test_unreferenced_or_ambiguous_objects_are_not_imported(tmp_path):
    (tmp_path/'f.c').write_text('int unused=7;\nint x=1,y=2;\nint original(void){return x;}')
    cfg={'root':str(tmp_path)}
    pairs=prep.prepare_variants(prep.Reader(cfg),cfg,('dest','original','f.c'),{'original':'dest'},'extern int x;')
    assert len(pairs)==1 and 'unused' not in pairs[0][0] and 'x=1' not in pairs[0][0]


def test_buffer_requires_existing_array_and_raw_target_address():
    asm='/* 0 80001000 3C048000 */ lui $a0, 0x8000\n/* 4 80001004 2484031C */ addiu $a0, $a0, 0x31c'
    source='void f(void){ use((void*)0x8000031c); }'
    header='extern char D_8000031C[64];'
    result=buffers.variants(source,'f','mips',asm,header)
    assert len(result)==1 and 'use(D_8000031C)' in result[0][1]
    assert not buffers.variants(source,'f','mips','',header)
    assert not buffers.variants(source,'f','mips',asm,'extern int D_8000031C;')
    assert not buffers.variants(source,'f','ppc',asm,header)
    assert not buffers.variants('int f(void){return *(char*)0x8000031c;}','f','mips',asm,header)


def test_runtime_helper_prototypes_only_for_call_only_difference():
    rows=[(' ','move $a0, $s0','move $a0, $s0'),('|','jal actual_div','jal ?__ull_div')]
    message=helper_call_diagnostics.suggestions(rows)[0]
    assert 'unsigned long long actual_div(unsigned long long, unsigned long long);' in message
    assert not helper_call_diagnostics.suggestions(rows+[('r','move $v0, $s0','move $v0, $s1')])


def test_entry_and_nop_diagnostics_are_advisory():
    assert entry_context('sw $s0, 0x10($sp)\nmove $s0, $a0\nlw $v0, 0($s0)\njr $ra')[0]==set()
    assert entry_context('lw $v0, 0($s2)\njr $ra')[0]=={'$s2'}
    source='int f(void){return 0;}'
    hints=source_diagnostics.hints(source,'f','lw $v0, 0($s2)\njr $ra','mips',[('<','nop','')])
    assert any('Entry-context' in s for s in hints) and any('Only target-only NOPs' in s for s in hints)
    assert not any('Only target-only NOPs' in s for s in source_diagnostics.hints(source,'f','','mips',[('|','addiu $v0, $zero, 1','addiu $v0, $zero, 2')]))


def test_same_file_object_alias_and_existing_header_definition(tmp_path):
    (tmp_path/'f.c').write_text('unsigned clock=9;\nunsigned original(void){return clock;}')
    cfg={'root':str(tmp_path)}
    pairs=prep.prepare_variants(prep.Reader(cfg),cfg,('dest','original','f.c'),{'original':'dest','clock':'D_80001000'},'extern unsigned clock;')
    assert 'D_80001000=9;' in pairs[0][0]
    pairs=prep.prepare_variants(prep.Reader(cfg),cfg,('dest','original','f.c'),{'original':'dest'},'unsigned clock=9;')
    assert len(pairs)==1 and 'clock=9' not in pairs[0][0]


def test_private_profile_keeps_unpromoted_context_without_changing_working_settings(monkeypatch):
    import json
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace

    from decompnet import toolchains
    from decompnet.app import profile_hypotheses, source_lead_analysis
    from decompnet.rom import export
    evidence=[]
    class Store:
        def one(self,sql,*args):return fn if sql.startswith('SELECT *') else None
        @contextmanager
        def tx(self):yield self
        def execute(self,sql,args):
            if sql.startswith('SELECT'):return SimpleNamespace(fetchone=lambda:None)
            evidence.append(json.loads(args[-1]))
    class Net:
        store=Store()
        def is_rom(self,p):return True
        def project_row(self,slug):return proj
        def flag_variants(self,*args):return [['-O2'],['-O3']]
        def _compile(self,p,h,s,f):
            assert h=='private typedefs' and s=='original source'
            assert json.loads(p['config'])['compiler']['flags']==['-O3']
            assert json.loads(proj['config'])['compiler']['flags']==['-O2']
            return SimpleNamespace(ok=True,obj=b'object')
        def _evaluate(self,*args):return object(),True,[]
        def _target_obj(self,*args):return object()
    proj={'id':1,'arch':'mips','slug':'sample','root':'.','config':json.dumps({'compiler':{'toolchain':'ido-5.3','flags':['-O2']}})}
    fn={'id':1,'vram':0x80001000,'name':'example','best_score':10,'asm':''}
    monkeypatch.setattr(prep,'inputs',lambda *args:('unchanged',))
    monkeypatch.setattr(source_lead_analysis,'destination',lambda *args:'unchanged')
    monkeypatch.setattr(toolchains,'installed',lambda *args:True)
    monkeypatch.setattr(profile_hypotheses,'prioritized',lambda *args:[['-O3']])
    monkeypatch.setattr(export,'_single_definition',lambda *args:True)
    monkeypatch.setattr(prep.scoring,'diff',lambda *args:SimpleNamespace(score=4,bytes_match=False,rows=[]))
    st=SimpleNamespace(stop=SimpleNamespace(is_set=lambda:False),add=lambda **kw:None)
    report={'tested':1};messages=[]
    prep.private_profiles(Net(),proj,fn,'original source','private typedefs',st,report,messages.append,3,time.monotonic()+20)
    evidence[:]=[r for r in evidence if 'review_required' in r]
    assert report['tested']==2 and evidence[0]['exact'] and evidence[0]['score']==0
    assert evidence[0]['header']=='private typedefs' and evidence[0]['review_required']
    assert 'Working settings unchanged' in messages[0]
    report={'tested':3}
    prep.private_profiles(Net(),proj,fn,'original source','private typedefs',st,report,messages.append,3,time.monotonic()+20)
    assert len(evidence)==1
