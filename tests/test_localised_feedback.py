import struct
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app import deep_repair_feedback, imported_output, repair_sequences
from decompnet.app import repair_localisation as local
from tests.test_permute import FakeNet

SOURCE='''void f(int x) {
 int donor;
 if (x) {
  callee(x);
 }
 else {
  callee(x);
 }
}'''
CHANGED=SOURCE.replace('  callee(x);','  donor = (short)x;\n  callee(donor);')


def test_localise_only_mapped_standalone_call_and_keep_whole_proposal():
    mapping=local.bind(SOURCE,dict(available=True,lines=[{'line':4}]))
    proposals=local.proposals(SOURCE,[('narrow_call_destination',CHANGED),('unknown','other')],mapping)
    assert len(proposals)==3
    assert proposals[0][1].count('donor = (short)x;')==1
    assert 'else {\n  callee(x);' in proposals[0][1]
    assert proposals[1]==('narrow_call_destination',CHANGED)
    assert proposals[-1]==('unknown','other')


@pytest.mark.parametrize('mapping',[None,{},dict(available=False,lines=[{'line':4}])])
def test_no_debug_mapping_never_invents_locations(mapping):
    tagged=[('narrow_call_destination',CHANGED)]
    assert local.proposals(SOURCE,tagged,local.bind(SOURCE,mapping) if mapping else None)==tagged


def test_stale_source_and_structural_families_cannot_be_split():
    mapping=local.bind(SOURCE,dict(available=True,lines=[{'line':4}]))
    tagged=[('typed_pointer_snapshot',CHANGED)]
    assert local.proposals(SOURCE,tagged,mapping)==tagged
    assert local.proposals(SOURCE+' ',[('narrow_call_destination',CHANGED)],mapping)==[('narrow_call_destination',CHANGED)]


def test_productive_sequences_need_repeated_real_improvements_and_are_profile_scoped():
    net=FakeNet()
    for _ in range(2):
        repair_sequences.observe(net,'profile','shape',['first'],'useful',20,10,'before','after')
        repair_sequences.observe(net,'profile','shape',['first'],'unchanged',20,20,'same','same')
        repair_sequences.observe(net,'profile','shape',['first'],'unknown',20,1,None,None)
    assert repair_sequences.next_families(net,'profile','shape',['first'])==['useful']
    assert repair_sequences.next_families(net,'other','shape',['first'])==[]
    assert repair_sequences.next_families(net,'profile','other',['first'])==[]
    assert repair_sequences.next_families(net,'profile','shape',[])==[]
    assert not net.store.one('SELECT * FROM repair_sequences')  # productive is not exact
    repair_sequences.learn(net,'profile','shape',['first','exact'])
    assert repair_sequences.next_families(net,'profile','shape',['first'])==['exact','useful']
    assert 'before' not in str(net.store.all('SELECT * FROM repair_sequence_outcomes'))


def coff(code):
    # One section containing a single named function; relocation changes are covered too.
    return (struct.pack('<HHIIIHH',0x14c,1,0,60+len(code),1,0,0)
      +struct.pack('<8sIIIIIIHHI',b'.text',0,0,len(code),60,0,0,0,0,0)
      +code+struct.pack('<8sIhHBB',b'_f',0,1,0x20,2,0)+struct.pack('<I',4))


def test_coff_fingerprint_is_section_scoped_and_unknown_input_stays_unknown(tmp_path):
    p=tmp_path/'f.obj';p.write_bytes(coff(b'\xc3'))
    a=imported_output.read(p,'_f')
    assert a['granularity']=='section'
    p.write_bytes(coff(b'\x90\xc3'))
    assert imported_output.read(p,'_f')['fingerprint']!=a['fingerprint']
    assert imported_output.read(p,'missing') is None
    for data in (b'not an object',b'\x7fELF',b'\x4c\x01'+b'\xff'*20):
        p.write_bytes(data);assert imported_output.read(p,'_f') is None
    assert imported_output.read(tmp_path/'missing','f') is None


def test_section_feedback_can_rotate_but_never_earns_function_continuation(monkeypatch):
    feedback=deep_repair_feedback.Feedback('start',30,[],output='section',granularity='section')
    feedback.record('start','child','f',[],20,[],'different',granularity='section')
    monkeypatch.setattr(deep_repair_feedback.repair_sequence_lane,'eligible',lambda *a:True)
    assert not feedback.eligible('child',4,'x86')
    feedback.proofs['child']['granularity']='function'
    assert feedback.eligible('child',4,'x86')


@pytest.mark.skipif(toolchains.installed('gcc-2.7.2-kmc') is None,reason='Needs installed pinned compiler')
def test_real_elf_fingerprint_tracks_function_code_and_relocations(tmp_path):
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfg=compiler_config(tmp_path,{'compiler':dict(toolchain='gcc-2.7.2-kmc',flags=['-G0','-mips3','-O2','-g3'])})
    p=tmp_path/'f.o'
    def fingerprint(source):
        result=compile_source(cfg,'',source);assert result.ok
        p.write_bytes(result.obj);return imported_output.read(p,'f')
    first=fingerprint('int f(int x){return x+1;}')
    assert first['granularity']=='function'
    assert first!=fingerprint('int f(int x){return x+2;}')
    assert fingerprint('extern int a(int); int f(int x){return a(x);}')!=fingerprint('extern int b(int); int f(int x){return b(x);}')
    assert imported_output.read(p,'missing') is None


def test_imported_trial_exposes_evidence_to_permuter_before_restore():
    from decompnet.app.permute import Permuter
    net=FakeNet();net.forks=SimpleNamespace(attempt=lambda *a,**k:dict(score=20,
        compiled_output=dict(fingerprint='real-output',granularity='function'),repair_locations={'bound':'source'}))
    worker=Permuter(net)
    assert worker._score_work(SimpleNamespace(),net.project_row('game'),net.function(),'candidate',40,'fork')==20
    assert worker._trial_output=='real-output'
    assert worker._trial_locations=={'bound':'source'}
    net.forks.attempt=lambda *a,**k:dict(score=None)
    assert worker._score_work(SimpleNamespace(),net.project_row('game'),net.function(),'broken',40,'fork') is None
    assert worker._trial_output is None and worker._trial_locations is None


def test_compiler_flag_changes_cannot_reuse_productive_sequence_statistics():
    net=FakeNet();proj=net.project_row('game');fn=net.function()
    original=repair_sequences.profile(net,proj,fn)
    with net.store.tx() as db:
        import json
        cfg=json.loads(proj['config']);cfg['compiler']['flags']=['-O1']
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    assert repair_sequences.profile(net,net.project_row('game'),fn)!=original


def test_unique_function_substring_rebases_debug_lines_without_fuzzy_mapping():
    source='// context\n\n'+SOURCE
    evidence=local.rebase(source,SOURCE,dict(available=True,lines=[dict(line=4,instructions=['private rows'])]))
    assert local.lines(source,evidence)=={6}
    assert 'private rows' not in str(evidence)
    assert local.rebase(SOURCE+SOURCE,SOURCE,dict(available=True,lines=[])) is None
    assert local.rebase(source,'not the compiled source',dict(available=True,lines=[])) is None


def test_repeated_changed_output_can_guide_exploration_without_claiming_a_match():
    net=FakeNet()
    for _ in range(4):repair_sequences.observe(net,'p','f',[],'changes',10,10,'old','new')
    assert repair_sequences.next_families(net,'p','f',[])==['changes']
    assert not net.store.one('SELECT * FROM repair_sequences')


def test_live_targeted_pool_receives_mapped_local_alternatives(monkeypatch):
    from decompnet.app import allocation_search, draft_repairs, iterative_repairs
    def variants(source,*a,**kw):
        labels=a[4]
        labels[CHANGED]='narrow_call_destination'
        return [CHANGED]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    monkeypatch.setattr(allocation_search,'proposals',lambda *a,**kw:[])
    result=iterative_repairs.targeted_pool(SOURCE,dict(rows=[],repair_locations=local.bind(SOURCE,
        dict(available=True,lines=[dict(line=4)]))),dict(name='f',asm=''),'mips',[])
    assert len(result)==2 and result[0][1].count('donor = (short)x;')==1
    assert result[1]==('narrow_call_destination',CHANGED)


def test_deep_search_compiles_local_alternative_before_the_whole_rewrite(monkeypatch):
    from decompnet.app import deep_permuter, draft_repairs
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import Log, setup
    from tests.test_permute import AGENT
    net=setup(monkeypatch)
    with net.store.tx() as db:db.execute('UPDATE functions SET best_source=?,best_score=40',(SOURCE,))
    def variants(source,*a,**kw):
        kw['labels'][CHANGED]='narrow_call_destination'
        return [CHANGED]
    monkeypatch.setattr(draft_repairs,'variants',variants)
    monkeypatch.setattr(deep_permuter.Search,'_repair_preflight',lambda *a:40)
    tested=[]
    def judge(self,agent,proj,fn,source,*args):
        tested.append(source)
        with net.store.tx() as db:db.execute("UPDATE functions SET best_source=?,best_score=0,status='matched'",(source,))
        return 0
    monkeypatch.setattr(Permuter,'_score',judge)
    search=deep_permuter.Search(net,1,10,Log())
    search.winner_locations=local.bind(SOURCE,dict(available=True,lines=[dict(line=4)]))
    best,_=search.local_repairs(AGENT,net.project_row('game'),net.function(),40,None)
    assert best==0 and len(tested)==1 and tested[0].count('donor = (short)x;')==1


def test_new_advice_rules_invalidate_paused_candidate_contexts():
    from decompnet import claim_stalls
    paths={name for name,_ in claim_stalls.repair_rules()}
    assert {'repair_localisation.py','imported_output.py'}<=paths
