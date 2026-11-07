"""Atomic return bundles: real bookkeeping, mocked expensive builds, and conservative caller gates."""
import json

import pytest

from decompnet.app import return_repair_apply as application
from decompnet.app import return_repair_plan as plans
from decompnet.app import return_repair_stage as staging
from decompnet.app.engine import RomNetwork
from decompnet.network import Network, NetworkError
from tests.test_deep_permuter import Log
from tests.test_return_contract_repairs import asm
from tests.test_return_path_search import SOURCE, TARGET

CANDIDATE=SOURCE.replace('void f(', 'int f(').replace('if(value<=0)', 'if(value>0) return value;\n if(value<=0)')
CALLER='void f(int a);\nvoid caller(void){f(1);}'


def game(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'game.db',tmp_path)
    (tmp_path/'rom.z64').write_bytes(b'private test image')
    (tmp_path/'splat.yaml').write_text('segments: []\n')
    cfg={'kind':'rom','platform':'n64','rom':{'file':'rom.z64'},'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,?,?,?,?,?,?)',
                   ('game','Game','mips',1,str(tmp_path),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,unit,header) VALUES(1,1,'','')")
        for fid,name,address,source,status,target,relocs in [
            (1,'f',0x80001000,SOURCE,'open',TARGET,[]),
            (2,'caller',0x80002000,CALLER,'matched',asm('jal f','nop','li $v0, 0','jr $ra','nop'),[[0,4,0x80001000]])]:
            db.execute('INSERT INTO functions(id,project_id,name,vram,size,n_insns,target,relocs,asm,unit,status,best_score,best_source,matched_source) VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (fid,name,address,28,7,b'\0'*28,json.dumps(relocs),target,'',status,0 if status=='matched' else 215,source,source if status=='matched' else None))
    monkeypatch.setattr(net,'_asm_text',lambda fn,proj:fn['asm'])
    monkeypatch.setattr(net,'on_match',lambda *args:None)
    agent,_=net.local_agent('autopilot')
    return net,agent


def invoke(net,agent):
    return application.apply(net,agent,net.project_row('game'),net._function('f','game'),CANDIDATE,Log())


def test_atomic_records_headers_bundles_and_notifications(tmp_path,monkeypatch):
    net,agent=game(tmp_path,monkeypatch);notifications=[]
    def verified(*args):
        # Staging cannot publish even the focal source before the full rebuild succeeds.
        assert net._function('f','game')['status']=='open'
        assert net._function('caller','game')['matched_source']==CALLER
        return {'image_bytes':18,'image_sha256':'test','verified_functions':2}
    monkeypatch.setattr(staging,'verify',verified)
    def notify(*args):
        assert net._function('f','game')['status']=='matched'
        assert net._function('caller','game')['matched_source'].startswith('int f(')
        notifications.append(args[1])
    monkeypatch.setattr(net,'on_match',notify)
    result=invoke(net,agent)
    assert result['matched'] and result['affected']==['f','caller']
    assert notifications==[1,2]
    assert net.store.one('SELECT COUNT(*) n FROM attempts')['n']==2
    assert net._function('f','game')['best_source']==CANDIDATE
    assert (tmp_path/'rom.z64').read_bytes()==b'private test image'


@pytest.mark.parametrize('failure',['build','stale','claim','record','cancel'])
def test_bundle_failure_leaves_all_sources_and_statuses_unchanged(tmp_path,monkeypatch,failure):
    net,agent=game(tmp_path,monkeypatch)
    before=[(r['status'],r['best_source'],r['matched_source']) for r in net.store.all('SELECT * FROM functions ORDER BY id')]
    def verify(*args):
        if failure=='build':raise NetworkError('Rebuild failed')
        if failure=='stale':
            with net.store.tx() as db:db.execute("UPDATE projects SET name='Changed' WHERE id=1")
        if failure=='claim':
            other,_=net.local_agent('other')
            with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(2,?,0,9999999999)',(other.id,))
        if failure=='cancel':raise RuntimeError('Cancelled')
        return {}
    monkeypatch.setattr(staging,'verify',verify)
    if failure=='record':
        original=Network._record
        def fail(net,fn,*args,**kwargs):
            if fn['id']==2:raise NetworkError('Second record failed')
            return original(net,fn,*args,**kwargs)
        monkeypatch.setattr(Network,'_record',fail)
    if failure=='cancel':
        with pytest.raises(RuntimeError,match='Cancelled'):invoke(net,agent)
    else:assert not invoke(net,agent)['matched']
    after=[(r['status'],r['best_source'],r['matched_source']) for r in net.store.all('SELECT * FROM functions ORDER BY id')]
    assert before==after
    assert not net.store.all('SELECT * FROM attempts')
    assert not net.store.all("SELECT * FROM events WHERE kind='match'")


@pytest.mark.parametrize('case',['consume','unknown','pointer','reloc','unmatched','source_use'])
def test_ambiguous_callers_decline_before_build(tmp_path,monkeypatch,case):
    net,agent=game(tmp_path,monkeypatch)
    with net.store.tx() as db:
        if case=='consume':db.execute('UPDATE functions SET asm=? WHERE id=2',(asm('jal f','nop','sw $v0, 0($s0)','jr $ra','nop'),))
        if case=='unknown':db.execute('UPDATE functions SET asm=? WHERE id=2',(asm('jal f','nop','jr $ra','nop'),))
        if case=='reloc':db.execute('UPDATE functions SET relocs=? WHERE id=2',(json.dumps([[4,5,0x80001000]]),))
        if case=='unmatched':db.execute("UPDATE functions SET status='open' WHERE id=2")
        if case=='source_use':db.execute('UPDATE functions SET matched_source=? WHERE id=2',('int caller(){return f(1);}',))
    if case=='pointer':(tmp_path/'rom.z64').write_bytes(b'table'+bytes.fromhex('80001000'))
    monkeypatch.setattr(staging,'verify',lambda *a:pytest.fail('ambiguous evidence must not build'))
    assert not invoke(net,agent)['matched']
    assert net._function('f','game')['status']=='open'


def test_only_matching_ansi_prototypes_are_rewritten():
    assert plans.declarations(CALLER,'f','void (int)','int (int)')=='int f(int a);\nvoid caller(void){f(1);}'
    for source in ['void f(float x);', 'void (*p)(int)=f;', 'void g(){int x=f(1);}', 'void g(){if(f(1)) x++;}']:
        with pytest.raises(NetworkError):plans.declarations(source,'f','void (int)','int (int)')


def test_stage_rebuild_rejects_false_identical_claim(tmp_path,monkeypatch):
    net,agent=game(tmp_path,monkeypatch);proj=net.project_row('game')
    state=staging.snapshot(net,proj);fn=net._function('f','game')
    files=staging.input_files(proj)
    updates=plans.plan(net,proj,fn,CANDIDATE,state['functions'],state['contexts'],files['rom.z64'])
    monkeypatch.setattr(RomNetwork,'_compile',lambda *a:type('Compiled',(),{'ok':True,'obj':b'test'})())
    monkeypatch.setattr(RomNetwork,'_evaluate',lambda *a:(b'test',True,None))
    def export(self,stage,slug,out,log):
        out.mkdir();(out/'original').write_bytes(b'original');(out/'output').write_bytes(b'corrupt')
        (out/'build.json').write_text(json.dumps({'image':'original','output':'output','sha1':'not valid','functions':[{'name':'f','kind':'c'},{'name':'caller','kind':'c'}]}))
        return {'identical':True}
    from decompnet.consoles import Console
    monkeypatch.setattr(Console,'export_project',export)
    scratch=tmp_path/'stage';scratch.mkdir()
    with pytest.raises(NetworkError,match='differs'):
        staging.verify(net,state,updates,files,scratch,Log())
    assert net._function('f','game')['status']=='open'


def test_postcommit_notification_error_does_not_claim_a_rollback(tmp_path,monkeypatch):
    net,agent=game(tmp_path,monkeypatch)
    monkeypatch.setattr(staging,'verify',lambda *a:{})
    def notify(*args):raise NetworkError('Follow-up failed')
    monkeypatch.setattr(net,'on_match',notify)
    result=invoke(net,agent)
    assert result['matched'] and result['notification_error']=='Follow-up failed'
    assert net._function('f','game')['status']=='matched'
    assert net._function('caller','game')['matched_source'].startswith('int f(')


def test_autopilot_checks_return_lane_before_old_completed_draft_skip(tmp_path,monkeypatch):
    from decompnet.app.engine.autopilot import AutopilotMixin
    net,agent=game(tmp_path,monkeypatch);seen=[]
    def repair(net,agent,proj,fn):
        assert net.store.one('SELECT agent_id FROM claims WHERE function_id=1')['agent_id']==agent.id
        seen.append(fn['id']);return True
    monkeypatch.setattr('decompnet.app.return_repair_autopilot.run',repair)
    monkeypatch.setattr(net,'_seed_for',lambda *a:pytest.fail('a matched return bundle must skip draft generation'))
    assert AutopilotMixin._autopilot_one_work(net,agent,net.project_row('game'),1)=='match'
    assert seen==[1] and not net.store.all('SELECT * FROM claims')
