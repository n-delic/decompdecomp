"""Boundary decisions preserve original evidence, history and existing matches."""
import json
import struct
import time

import pytest

from decompnet.app import boundary_repairs as repair
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def raw(*words):return struct.pack('>'+str(len(words))+'I',*words)


@pytest.fixture
def game(tmp_path,monkeypatch):
    net=RomNetwork(tmp_path/'db',tmp_path)
    image=raw(0x03e00008,0x27bd0018,0x03e00008,0,0x03e00008,0,0,0)
    (tmp_path/'rom.z64').write_bytes(image)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'gp':0,'rom':{'file':'rom.z64'},
         'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']},
         'segments':[{'name':'code','rom_start':0,'rom_end':len(image),'vram':0x80001000}]}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(1,'game','Game',?,'mips',1,?)",(str(tmp_path),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
        for name,offset,size,status in [('head',0,4,'open'),('tail',4,4,'open'),('intact',8,8,'matched'),('normal',16,8,'open'),('padding',24,4,'open')]:
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,status,best_source,best_score,matched_source) VALUES(1,?,?,?,?,?,?, '[]','','code',?,'saved C',100,?)",
                       (name,0x80001000+offset,offset,size,size//4,image[offset:offset+size],status,'matched C' if status=='matched' else None))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,'func')",(name,0x80001000+offset,size))
    checked=[]
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:checked.append(True) or [])
    return net,checked


def decide(net,name,action,**kwargs):
    plan=repair.preview(net,'game',name,action,kwargs.get('end'))
    return repair.apply(net,'game',name,action,plan['revision'],plan['image'],kwargs.get('end'),note='Reviewed original flow')


def test_audit_quarantines_fragments_but_not_valid_small_functions(game):
    net,_=game
    findings=repair.audit(net,'game')['findings']
    assert {r['function'] for r in findings}=={'head','tail','padding'}
    blocked=repair.blocked(net,net.project_row('game'))
    assert net._function('head','game')['id'] in blocked
    assert net._function('normal','game')['id'] not in blocked
    agent,_=net.local_agent('worker')
    assert net.claim_row(agent,'game')['name']=='normal'
    why,todo=net.selection_wait_reason('game',agent)
    assert 'waiting for boundary review' in why and 'Review & repair boundaries' in todo
    with pytest.raises(NetworkError,match='review'):
        other,_=net.local_agent('other')
        net.claim_row(other,'game','head')


def test_delay_slot_preview_apply_and_undo_preserve_child_history(game):
    net,checked=game
    head=dict(net._function('head','game'));tail=dict(net._function('tail','game'))
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,score,compiled,source,created_at) VALUES(?,100,1,?,?)',(tail['id'],'old tail C',time.time()))
        db.execute('INSERT INTO notes(function_id,text,created_at) VALUES(?,?,?)',(tail['id'],'original note',time.time()))
    plan=repair.preview(net,'game','head','delay_slot')
    assert plan['new_size']==8 and {r['name'] for r in plan['affected']}=={'head','tail'}
    result=decide(net,'head','delay_slot')
    new=net._function('head','game')
    assert new['target']==head['target']+tail['target'] and new['best_score'] is None
    assert new['best_source']=='saved C' and net._function('tail','game')['gone_mark'] is not None
    assert net.store.one('SELECT function_id FROM attempts')['function_id']==tail['id']
    assert net.store.one('SELECT text FROM notes')['text']=='original note'
    assert net._function('intact','game')['status']=='matched' and checked
    repair.undo(net,'game',result['key'])
    assert dict(net._function('head','game'))==head
    assert dict(net._function('tail','game'))==tail


def test_join_preserves_ids_and_undo(game):
    net,_=game
    result=decide(net,'tail','join_previous')
    assert net._function('head','game')['size']==8
    assert net._function('tail','game')['gone_mark'] is not None
    repair.undo(net,'game',result['key'])
    assert net._function('tail','game')['gone_mark'] is None


def test_padding_classification_is_not_a_match_and_undo_restores_queue(game):
    net,_=game
    result=decide(net,'padding','padding')
    fn=net._function('padding','game')
    assert fn['gone_mark'] is not None and fn['status']=='open'
    from decompnet.app.code_coverage import snapshot
    assert snapshot(net,net.project_row('game'))['classified_noncode']['n']==1
    repair.undo(net,'game',result['key'])
    assert net._function('padding','game')['gone_mark'] is None


def test_keep_is_an_explicit_reversible_override(game):
    net,_=game
    result=decide(net,'head','keep')
    assert net._function('head','game')['id'] not in repair.blocked(net,net.project_row('game'))
    repair.undo(net,'game',result['key'])
    assert net._function('head','game')['id'] in repair.blocked(net,net.project_row('game'))


def test_stale_preview_claims_and_match_regression_reject_without_changes(game,monkeypatch):
    net,_=game
    plan=repair.preview(net,'game','head','delay_slot')
    before=net._function('head','game')['target']
    with net.store.tx() as db:db.execute("UPDATE functions SET best_score=99 WHERE name='head'")
    with pytest.raises(NetworkError,match='changed'):
        repair.apply(net,'game','head','delay_slot',plan['revision'],plan['image'],note='evidence')
    assert net._function('head','game')['target']==before
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:[('intact','failed')])
    with pytest.raises(NetworkError,match='reverified'):decide(net,'head','delay_slot')
    assert not repair.audit(net,'game')['history']


def test_reject_matched_overlap_and_mapping_ambiguity(game):
    net,_=game
    with pytest.raises(NetworkError,match='matched'):repair.preview(net,'game','head','adjust',0x80001010)
    cfg=net.cfg(net.project_row('game'));cfg=json.loads(json.dumps(cfg));cfg['segments'].append(cfg['segments'][0])
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?',(json.dumps(cfg),))
    with pytest.raises(NetworkError,match='unambiguous'):repair.preview(net,'game','head','delay_slot')


def test_undo_rejects_new_attempts(game):
    net,_=game
    result=decide(net,'head','delay_slot')
    with net.store.tx() as db:db.execute('INSERT INTO attempts(function_id,score,compiled,created_at) VALUES(?,90,1,?)',(net._function('head','game')['id'],time.time()))
    with pytest.raises(NetworkError,match='New attempts'):repair.undo(net,'game',result['key'])


def test_custom_end_retains_tail_and_undo_refuses_new_work(game):
    net,_=game
    result=decide(net,'normal','adjust',end=0x80001014)
    tail=net.store.one("SELECT * FROM functions WHERE name='boundary_tail_80001014'")
    assert tail and tail['target']==bytes(4) and tail['gone_mark'] is None
    assert tail['id'] in repair.blocked(net,net.project_row('game'))
    repair.undo(net,'game',result['key'])
    assert net._function('normal','game')['size']==8
    assert net.store.one('SELECT gone_mark FROM functions WHERE id=?',tail['id'])['gone_mark'] is not None


def test_foreign_project_undo_and_active_claim_are_rejected(game):
    net,_=game
    with pytest.raises(NetworkError,match='this project'):repair.undo(net,'game','boundary-repair:999:other')
    worker,_=net.local_agent('active')
    fn=net._function('tail','game')
    with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],worker.id,time.time(),time.time()+3600))
    with pytest.raises(NetworkError,match='claims'):repair.preview(net,'game','head','delay_slot')


def test_undo_refuses_header_changes_and_apply_refuses_image_changes(game):
    net,_=game
    plan=repair.preview(net,'game','head','delay_slot')
    image=net._rom(net.project_row('game'))[0]
    path=__import__('pathlib').Path(net.project_row('game')['root'])/'rom.z64'
    path.write_bytes(image[:-4]+b'1234')
    with pytest.raises(NetworkError,match='changed'):
        repair.apply(net,'game','head','delay_slot',plan['revision'],plan['image'],note='evidence')
    path.write_bytes(image)
    result=decide(net,'head','delay_slot')
    with net.store.tx() as db:db.execute("UPDATE context_versions SET header='changed'")
    with pytest.raises(NetworkError,match='headers changed'):repair.undo(net,'game',result['key'])


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_boundary_checks_use_console_byte_order_and_keep_valid_tiny_functions(platform):
    from decompnet.consoles import get
    console=get(platform)
    def bytes_for(*words):return struct.pack(('>' if console.big_endian else '<')+str(len(words))+'I',*words)
    assert repair.reason(console,{'status':'open','target':bytes_for(0x03e00008),'meta':'{}'})
    assert repair.reason(console,{'status':'open','target':bytes_for(0x03e00008,0),'meta':'{}'}) is None
    assert repair.reason(console,{'status':'open','target':bytes(4),'meta':'{}'})


def test_reviewed_classification_does_not_delete_history_and_archives_cannot_be_claimed(game):
    net,_=game
    result=decide(net,'padding','data')
    assert net._function('padding','game')['gone_mark'] is not None
    agent,_=net.local_agent('worker')
    with pytest.raises(NetworkError,match='archived'):
        net.claim_row(agent,'game','padding')
    repair.undo(net,'game',result['key'])
    assert net._function('padding','game')['target']==bytes(4)


def test_shared_room_repairs_are_blocked_and_newer_metadata_prevents_undo(game):
    net,_=game
    net.rooms['game']=object()
    with pytest.raises(NetworkError,match='room'):decide(net,'head','delay_slot')
    net.rooms.clear()
    result=decide(net,'head','delay_slot')
    with net.store.tx() as db:db.execute("UPDATE functions SET meta=json_set(meta,'$.newer_decision',1) WHERE name='head'")
    with pytest.raises(NetworkError,match='newer work'):repair.undo(net,'game',result['key'])


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_delay_slot_repair_uses_each_console_target_adapter(game,platform):
    from pathlib import Path

    from decompnet.consoles import get
    net,_=game;console=get(platform);proj=net.project_row('game');cfg=json.loads(proj['config'])
    cfg['platform']=platform;cfg['big_endian']=console.big_endian
    path=Path(proj['root'])/'rom.z64';image=path.read_bytes()
    if not console.big_endian:image=b''.join(image[i:i+4][::-1] for i in range(0,len(image),4))
    path.write_bytes(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?,big_endian=?',(json.dumps(cfg),int(console.big_endian)))
        for fn in net.store.all('SELECT * FROM functions'):
            db.execute('UPDATE functions SET target=? WHERE id=?',(image[fn['rom_offset']:fn['rom_offset']+fn['size']],fn['id']))
    result=decide(net,'head','delay_slot')
    assert net._function('head','game')['target']==image[:8]
    repair.undo(net,'game',result['key'])
    assert net._function('head','game')['target']==image[:4]


def test_unrelated_worker_can_improve_during_match_verification(game,monkeypatch):
    net,_=game
    def verify(*args,**kwargs):
        with net.store.tx() as db:db.execute("UPDATE functions SET best_score=95 WHERE name='normal'")
        return []
    monkeypatch.setattr(net,'regression_test',verify)
    decide(net,'head','delay_slot')
    assert net._function('head','game')['size']==8
    assert net._function('normal','game')['best_score']==95


JR_RA,NOP,STEP,FRAME,UNFRAME=0x03e00008,0,0x25080001,0x27bdffe8,0x27bd0018
BASE=0x80001000


def jal(address):return 0x0c000000|((address&0x0fffffff)>>2)


def layout(tmp_path,monkeypatch,pieces):
    """A tiny mapped MIPS game of (name, words, status) pieces laid end to end."""
    net=RomNetwork(tmp_path/'db',tmp_path);image=b''.join(raw(*words) for _,words,_ in pieces)
    (tmp_path/'rom.z64').write_bytes(image)
    cfg={'kind':'rom','platform':'n64','big_endian':True,'gp':0,'rom':{'file':'rom.z64'},
         'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']},'segments':[{'name':'code','rom_start':0,'rom_end':len(image),'vram':BASE}]}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,root,arch,big_endian,config) VALUES(1,'game','Game',?,'mips',1,?)",(str(tmp_path),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
        offset=0
        for name,words,status in pieces:
            size=4*len(words)
            db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,status,matched_source) VALUES(1,?,?,?,?,?,?,'[]','','code',?,?)",
                       (name,BASE+offset,offset,size,len(words),image[offset:offset+size],status,'matched C' if status=='matched' else None))
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,'func')",(name,BASE+offset,size))
            offset+=size
    checked=[]
    monkeypatch.setattr(net,'regression_test',lambda *args,**kwargs:checked.append(True) or [])
    return net,checked


def test_safe_fixes_apply_settled_repairs_once_and_leave_the_rest(game):
    net,checked=game
    result=repair.apply_safe(net,'game')
    assert [f['action'] for f in result['fixed']]==['delay_slot'] and result['remaining']==1
    assert net._function('head','game')['size']==8 and net._function('tail','game')['gone_mark'] is not None
    assert {f['function'] for f in repair.audit(net,'game')['findings']}=={'padding'}  # a lone nop needs a person
    assert len(checked)==1  # matches are reverified once per run, not once per fix
    history=repair.audit(net,'game')['history']
    assert len(history)==1 and history[0]['evidence'].startswith('Automatic safe fix')
    repair.undo(net,'game',history[0]['key'])
    assert net._function('head','game')['size']==4 and net._function('tail','game')['gone_mark'] is None
    assert repair.apply_safe(net,'game')['fixed']  # undone fixes are offered again


def test_safe_fixes_join_code_that_runs_on_into_the_next_piece(tmp_path,monkeypatch):
    net,_=layout(tmp_path,monkeypatch,[('start',[STEP,STEP],'open'),('rest',[JR_RA,NOP],'open'),('after',[JR_RA,NOP],'open')])
    result=repair.apply_safe(net,'game')
    assert [(f['flagged'],f['action'],f['function']) for f in result['fixed']]==[('start','join_previous','start')]
    assert net._function('start','game')['size']==16 and net._function('rest','game')['gone_mark'] is not None
    assert net._function('after','game')['gone_mark'] is None and result['remaining']==0


@pytest.mark.parametrize('case',['call that never returns','own stack frame','called directly','matched','claimed','set aside by you'])
def test_safe_fixes_never_absorb_a_separate_function(tmp_path,monkeypatch,case):
    first=[jal(BASE+0x100),NOP] if case=='call that never returns' else [STEP]
    second=[FRAME,JR_RA,UNFRAME] if case=='own stack frame' else [JR_RA,NOP]
    caller=[jal(BASE+4*len(first)),NOP,JR_RA,NOP] if case=='called directly' else [JR_RA,NOP]
    net,_=layout(tmp_path,monkeypatch,[('first',first,'open'),('second',second,'matched' if case=='matched' else 'open'),('caller',caller,'open')])
    if case=='claimed':
        worker,_=net.local_agent('worker')
        with net.store.tx() as db:db.execute('INSERT INTO claims VALUES(?,?,?,?)',(net._function('second','game')['id'],worker.id,time.time(),time.time()+3600))
    if case=='set aside by you':net.set_aside_by_you('second','game','hand-written')
    result=repair.apply_safe(net,'game')
    assert result['fixed']==[] and result['remaining']==1
    assert net._function('first','game')['size']==4*len(first) and not repair.audit(net,'game')['history']


def test_safe_fixes_respect_rooms_and_reviewed_entries(game):
    net,checked=game
    decide(net,'head','keep')
    assert repair.apply_safe(net,'game')['fixed']==[]  # a kept entry was a person's decision
    net.rooms['game']=object()
    with pytest.raises(NetworkError,match='room'):repair.apply_safe(net,'game')
