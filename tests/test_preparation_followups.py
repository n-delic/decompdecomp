import os
import threading
import time
from types import SimpleNamespace

from decompnet.app import helper_call_diagnostics, matching_preflight, preparation_diagnostics
from decompnet.app import source_preparation as prep


def test_math_mapping_requires_call_only_diff_and_explains_float_prototype():
    rows=[['i','jal func_80001000','jal ?sqrtf'],[' ','move $a0, $v0','move $a0, $v0']]
    hint=helper_call_diagnostics.suggestions(rows)[0]
    assert 'float func_80001000(float);' in hint and 'implicit int' in hint
    assert not helper_call_diagnostics.suggestions(rows+[['r','move $a1, $v1','move $a1, $v0']])
    assert not helper_call_diagnostics.suggestions(rows+[['i','jal other','jal ?sqrtf']])
    assert 'double dest(double);' in helper_call_diagnostics.suggestions([['i','jal dest','jal ?sqrt']])[0]


def test_allocation_diagnostic_does_not_invent_layout_or_rewrite_source():
    source='void f(void){p=alHeapAlloc(h,1,sizeof(Bus));}'
    rows=[['i','addiu $t2, $zero, 0x4c','addiu $t2, $zero, 0x48'],[' ','sw $t2, 0x10($sp)','sw $t2, 0x10($sp)'],[' ','jal alHeapDBAlloc','jal alHeapDBAlloc']]
    hint=preparation_diagnostics.allocations(source,rows)[0]
    assert 'target 76, candidate 72' in hint and 'does not establish a missing field' in hint
    assert not preparation_diagnostics.allocations(source,rows+[['i','nop','move $v0, $a0']])
    assert not preparation_diagnostics.allocations(source,rows[:1])
    assert not preparation_diagnostics.allocations('void f(void){}',rows)


def test_first_compiler_error_and_complete_private_log(tmp_path):
    output='warning: first\ncfe: Error: missing STATE\n STATE x;\n'+('cascading error\n'*300)
    hint=preparation_diagnostics.compile_error(SimpleNamespace(workspace=tmp_path),{'id':42},output)
    assert hint.startswith('cfe: Error: missing STATE') and len(hint)<1400
    path=tmp_path/'.decomp/logs/source-preparation/42.log'
    assert path.read_text()==output
    if os.name!='nt':assert path.stat().st_mode & 0o777 == 0o600


def test_followup_uses_saved_context_shared_budget_and_stops_when_exhausted(monkeypatch):
    current={'name':'f','status':'open','best_score':20}
    net=SimpleNamespace(project_row=lambda s:{'slug':s},_function=lambda *args:current)
    report={'tested':1,'improved':1,'matched':0};calls=[]
    def profiles(net,proj,fn,log,check,**kw):
        assert check() and fn['best_score']==20 and kw['max_profiles']==2
        calls.append(fn.copy());current.update(status='matched',best_score=0)
        return {'tested':2}
    monkeypatch.setattr(matching_preflight,'run',profiles)
    st=SimpleNamespace(stop=threading.Event(),add=lambda **kw:None)
    prep.followup(net,{'slug':'game'},current,st,report,lambda s:None,3,time.monotonic()+10)
    assert report=={'tested':3,'improved':2,'matched':1} and len(calls)==1
    prep.followup(net,{'slug':'game'},current,st,report,lambda s:None,3,time.monotonic()+10)
    assert len(calls)==1


def test_preparation_releases_its_claim_before_profile_followup(tmp_path,monkeypatch):
    import json

    from decompnet.app.permute import Permuter, State
    from decompnet.network import Agent
    from tests.test_source_preparation import config, prepared_net
    net,proj,fn=prepared_net();net.cfg=lambda p:json.loads(p['config'])
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.fork_slug=lambda s:None;net.links={};net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    net._comparison_digest=lambda *a:'scope'
    cfg=config(tmp_path);(tmp_path/'types.h').write_text('');(tmp_path/'game.c').write_text('int original(void){return 1;}')
    prep.configure(net,proj,cfg)
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    def attempt(*args):
        assert net.store.one('SELECT 1 FROM claims')
        return 20
    seen=[]
    def follow(*args):
        assert not net.store.one('SELECT 1 FROM claims')
        seen.append(True)
    monkeypatch.setattr(prep,'attempt',attempt);monkeypatch.setattr(prep,'followup',follow)
    prep.run(Permuter(net),proj,State(),budget=3)
    assert seen==[True]
