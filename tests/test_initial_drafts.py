"""Initial drafts recover typed calls and overlapping frame views without editing shared inputs."""
import json
import shutil
import struct
import subprocess
from types import SimpleNamespace

import pytest

from decompnet import limits, seeds, toolchains
from decompnet.app import draft_data, draft_regeneration, draft_reports
from decompnet.app import initial_draft_repairs as repairs
from decompnet.arch import mips_drafts
from decompnet.rom.symbols import Symbol, SymbolTable

from .test_overlay_drafts import network  # noqa: F401

CALLBACK_ASM = '''glabel f
addiu $sp, $sp, -0x18
lui $v1, %hi(callbacks)
addiu $v1, $v1, %lo(callbacks)
sll $v0, $a0, 2
addu $v0, $v0, $v1
lw $v0, 0($v0)
jalr $v0
nop
jr $ra
addiu $sp, $sp, 0x18
'''
CALLBACK = '''extern M2C_UNK callbacks;
void f(s32 arg0) { *((arg0 * 4) + &callbacks)(); }
'''
FRAME_ASM = '''glabel f
addiu $sp, $sp, -0x30
sw $a0, 0x10($sp)
addiu $a0, $sp, 0x10
jal fill
nop
lhu $v0, 0x12($sp)
jr $ra
addiu $sp, $sp, 0x30
'''
FRAME = '''void fill(s32 *);
u16 f(s32 arg0) {
    s32 sp10;
    sp10 = arg0;
    fill(&sp10);
    return unksp12;
}
'''
FORWARD_ASM = '''glabel f
addiu $sp, $sp, -0x18
addiu $a1, $zero, 7
jal named_callee
addiu $a2, $zero, 9
jr $ra
addiu $sp, $sp, 0x18
'''
FORWARD = 's32 named_callee(void *, s32, s32);\ns32 f(void *arg0) { return named_callee(7, 9); }'


def test_indirect_tables_require_a_word_load_to_the_actual_call_register():
    assert mips_drafts.read(CALLBACK_ASM)['callback_tables'] == ['callbacks']
    assert not mips_drafts.read(CALLBACK_ASM.replace('jalr $v0','jalr $t9'))['callback_tables']
    assert not mips_drafts.read(CALLBACK_ASM.replace('jalr $v0','addiu $v0, $zero, 0\njalr $v0'))['callback_tables']
    assert not mips_drafts.read(CALLBACK_ASM.replace('lw $v0, 0($v0)','beqz $a1, .Lother\nnop\nlw $v0, 0($v0)'))['callback_tables']


def test_callback_context_is_private_and_respects_explicit_types():
    context = 'typedef int s32;'
    alternative = repairs.callback_context(CALLBACK, context, CALLBACK_ASM, 'mips')
    assert alternative == context+'\nextern int (*callbacks[])();'
    assert context == 'typedef int s32;'
    assert repairs.callback_context(CALLBACK, context+'\nextern int callbacks;', CALLBACK_ASM, 'mips') is None
    assert repairs.callback_context(CALLBACK, context, CALLBACK_ASM, 'arm') is None


def test_callback_declarations_are_kept_in_the_compiler_candidate(monkeypatch):
    calls = []
    def generate(*args, **kwargs):
        calls.append(args[3])
        return 'void f(s32 arg0) { callbacks[arg0](); }'
    monkeypatch.setattr(seeds,'generate_from_asm',generate)
    choices = list(repairs.recovery_drafts(CALLBACK,'f',CALLBACK_ASM,'mips','gcc-2.7.2-kmc','',None,{'failures':[]}))
    source = next(source for family,source in choices if family == 'initial_callback_context')
    assert 'extern int (*callbacks[])();' in source
    assert calls == ['\nextern int (*callbacks[])();']


def test_stack_views_share_storage_and_keep_original_offsets():
    source = repairs.stack_frame(FRAME,'f',FRAME_ASM,'mips')
    assert source and 'unsigned char bytes[48]' in source
    assert 'unsigned char pad[16]; s32 value;' in source
    assert 'unsigned char pad[18]; u16 value;' in source
    assert 'fill(&m2c_initial_frame.view_sp10.value)' in source
    assert 'return m2c_initial_frame.view_unksp12.value' in source
    assert ' = {0}' not in source


@pytest.mark.parametrize('asm', [FRAME_ASM.replace('lhu $v0, 0x12($sp)','nop'),
    FRAME_ASM.replace('0x12($sp)','0x32($sp)'), FRAME_ASM.replace('-0x30','-0x5000'),
    FRAME_ASM.replace('jal fill','addiu $sp, $sp, -8\njal fill')])
def test_stack_recovery_rejects_missing_loads_and_ambiguous_frames(asm):
    assert repairs.stack_frame(FRAME,'f',asm,'mips') is None


def test_stack_recovery_requires_an_escaped_known_view_and_preserves_other_functions():
    assert repairs.stack_frame(FRAME.replace('fill(&sp10);',''), 'f', FRAME_ASM,'mips') is None
    suffix='\nint other(void) { int sp10 = 3; return sp10; }'
    assert repairs.stack_frame(FRAME+suffix,'f',FRAME_ASM,'mips').endswith(suffix)


def test_stack_union_behaves_like_the_same_original_bytes(tmp_path):
    cc = shutil.which('cc')
    if not cc:pytest.skip('needs a native C compiler')
    repaired = repairs.stack_frame(FRAME,'f',FRAME_ASM,'mips')
    code = 'typedef int s32; typedef unsigned short u16;\n'+repaired+'\nvoid fill(s32 *p) { *p = 0x12345678; }\n'
    expected=struct.unpack('=H',struct.pack('=I',0x12345678)[2:4])[0]
    code += f'int main(void) {{ return f(0) != {expected}; }}\n'
    source=tmp_path/'frame.c';source.write_text(code);exe=tmp_path/'frame'
    subprocess.run(limits.capped([cc,'-O2',str(source),'-o',str(exe)]),check=True,capture_output=True,timeout=15)
    subprocess.run([str(exe)],check=True,timeout=5)


def test_missing_forwarded_arguments_work_for_descriptive_callees():
    repaired = repairs.forwarded_arguments(FORWARD,'f',FORWARD_ASM,'mips')
    assert 'named_callee(arg0, 7, 9)' in repaired
    assert repairs.forwarded_arguments(repaired,'f',FORWARD_ASM,'mips') is None
    assert repairs.forwarded_arguments(FORWARD,'f',FORWARD_ASM.replace('jal named_callee','beqz $a0, .Lx\njal named_callee'),'mips') is None
    assert repairs.forwarded_arguments(FORWARD.replace('s32, s32','float, s32'),'f',FORWARD_ASM,'mips') is None
    assert repairs.forwarded_arguments(FORWARD,'f',FORWARD_ASM,'ppc') is None


def test_original_data_uses_exact_overlay_names_and_keeps_external_table_entries_numeric():
    table='jtbl_80002000_overlay_001'
    symbols=SymbolTable([Symbol(table,0x80002000,8,'jtbl'),Symbol('jtbl_80002000_overlay_002',0x80002000,8,'jtbl'),
                         Symbol('STR_80003000',0x80003000,4,'string'),Symbol('RO_80004000',0x80004000,4,'rodata')])
    data={0x80002000:struct.pack('>2I',0x80001000,0x80005000),0x80003000:b'hi\0\0',0x80004000:b'\x3f\x80\0\0'}
    owners=[]
    def reader(proj,fn):
        owners.append(fn['rom_offset'])
        return lambda address,size:data.get(address)
    net=SimpleNamespace(symbols=lambda pid:symbols, reader=reader, cfg=lambda proj:{'big_endian':True})
    asm=f'lui $v0, %hi({table})\nlui $a0, %hi(STR_80003000)\nlui $a1, %hi(RO_80004000)\n/* 0 80001000 03E00008 */ jr $ra'
    text='\n'.join(draft_data.read(net,{'id':1},{'asm':asm,'rom_offset':123}))
    assert owners == [123] and 'dlabel '+table in text
    assert 'overlay_002' not in text and '.word .L80001000' in text and '.word 0x80005000' in text
    assert '.asciz "hi"' in text and '.byte 0x3F, 0x80, 0x00, 0x00' in text


def test_data_reader_refuses_short_or_unbounded_payloads():
    syms=SymbolTable([Symbol('RO_80004000',0x80004000,5000,'rodata')])
    net=SimpleNamespace(symbols=lambda pid:syms,reader=lambda *args:lambda *args:b'x',cfg=lambda proj:{})
    assert draft_data.read(net,{'id':1},{'asm':'lui $a0, %hi(RO_80004000)'}) == ['.section .rodata']


def test_receipts_keep_generation_and_compiler_errors_separate(network):  # noqa: F811
    net,proj,fn=network
    report={'key':'test','compiled':False,'failures':[]}
    draft_reports.failure(report,'generation','raw','missing table')
    draft_reports.failure(report,'compiler','frame','unknown identifier')
    draft_reports.save(net,fn['id'],report)
    stored=draft_reports.read(net,fn['id'])
    assert [r['phase'] for r in stored['failures']] == ['generation','compiler']
    assert 'unknown identifier' in draft_reports.summary(stored)
    assert net.store.one('SELECT count(*) AS n FROM attempts')['n']==0


def test_regeneration_preserves_matches_and_attempts_and_skips_claimed_functions(network, monkeypatch):  # noqa: F811
    net,proj,fn=network
    monkeypatch.setattr(net,'can_draft',lambda proj:True)
    agent,_=net.local_agent('test-holder')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)',(fn['id'],agent.id,0,99999999999))
    called=[]
    monkeypatch.setattr(net,'_seed_for',lambda *args:called.append(args))
    result=draft_regeneration.run(net,proj['slug'],lambda *args:None)
    assert result['skipped']==1 and not called
    assert net.store.one('SELECT seed,best_source,status FROM functions WHERE id=?',fn['id'])['status']=='open'
    assert net.store.one('SELECT count(*) AS n FROM attempts')['n']==0
    assert draft_reports.generation(net,fn['id'])==0


@pytest.mark.skipif(not seeds.available() or toolchains.installed('gcc-2.7.2-kmc') is None,
                    reason='needs pinned m2c and the period compiler')
def test_compiler_receipt_retries_changed_inputs_but_skips_identical_failures(network, monkeypatch):  # noqa: F811
    net,proj,fn=network
    agent,_=net.local_agent('autopilot')
    calls=[]
    def generate(*args,**kwargs):
        calls.append(args)
        return 'int '+fn['name']+'(void) { not_declared(); return unresolved_name; }'
    monkeypatch.setattr(seeds,'generate_from_asm',generate)
    assert net._autopilot_one(agent,proj,fn['id'])=='fail'
    report=draft_reports.read(net,fn['id'])
    assert report['complete'] and not report['compiled'] and report['failures'][-1]['phase']=='compiler'
    count=len(calls)
    assert net._autopilot_one(agent,proj,fn['id'])=='skip' and len(calls)==count
    with net.store.tx() as db:db.execute('UPDATE context_versions SET header=header || ?',('\nextern int unresolved_name;',))
    net._headers.clear()
    assert net._autopilot_one(agent,proj,fn['id']) in ('near','far','fail','match')
    assert draft_reports.read(net,fn["id"])["key"] != report["key"]


@pytest.mark.skipif(not seeds.available() or toolchains.installed('gcc-2.7.2-kmc') is None,
                    reason='needs pinned m2c and the period compiler')
def test_callback_recovery_is_compiled_and_scored_by_the_ordinary_judge(network):  # noqa: F811
    from pathlib import Path
    net,proj,fn=network
    instructions=[(0x27BDFFE8,'addiu $sp, $sp, -0x18'),(0xAFBF0014,'sw $ra, 0x14($sp)'),
                  (0x00041080,'sll $v0, $a0, 2'),(0x3C038000,'lui $v1, %hi(callbacks)'),
                  (0x24632000,'addiu $v1, $v1, %lo(callbacks)'),(0x00431021,'addu $v0, $v0, $v1'),
                  (0x8C420000,'lw $v0, 0($v0)'),(0x0040F809,'jalr $v0'),(0,'nop'),
                  (0x8FBF0014,'lw $ra, 0x14($sp)'),(0x03E00008,'jr $ra'),(0x27BD0018,'addiu $sp, $sp, 0x18')]
    target=b''.join(struct.pack('>I',word) for word,_ in instructions)
    asm='glabel '+fn['name']+'\n'+'\n'.join(f'/* {i*4:X} {fn["vram"]+i*4:08X} {word:08X} */ {text}' for i,(word,text) in enumerate(instructions))
    Path(proj['root'],'rom.z64').write_bytes(target)
    cfg=json.loads(proj['config']);cfg['segments'][0]['rom_end']=len(target)
    # The code relocations are part of the original target, just as an import records them.
    relocs=json.dumps([[12,5,0x80002000],[16,6,0x80002000]])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
        db.execute('UPDATE functions SET target=?,asm=?,relocs=?,size=?,n_insns=? WHERE id=?',
                   (target,asm,relocs,len(target),len(instructions),fn['id']))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                   (proj['id'],'callbacks',0x80002000,8,'data'))
    proj=net.project_row(proj['slug']);agent,_=net.local_agent('autopilot')
    assert net._autopilot_one(agent,proj,fn['id']) in ('match','near','far')
    report=draft_reports.read(net,fn['id'])
    assert report['compiled'] and report['scored'] and report['family']=='initial_callback_context'
    assert net.store.one('SELECT best_source FROM functions WHERE id=?',fn['id'])['best_source']
    assert net.store.one('SELECT compiled FROM attempts WHERE function_id=?',fn['id'])['compiled']==1


def test_regeneration_cancellation_does_not_clear_saved_work(network, monkeypatch):  # noqa: F811
    import threading
    net,proj,fn=network
    monkeypatch.setattr(net,'can_draft',lambda proj:True)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_source='saved exact C',best_source='saved best C',best_score=0 WHERE id=?",(fn['id'],))
    stop=threading.Event();stop.set()
    log=SimpleNamespace(cancel_event=stop)
    class Log:
        cancel_event=log.cancel_event
        def __call__(self,*args):pass
    result=draft_regeneration.run(net,proj['slug'],Log())
    assert result['cancelled'] and result['completed']==0
    row=net.store.one('SELECT status,matched_source,best_source,best_score FROM functions WHERE id=?',fn['id'])
    assert tuple(row)==('matched','saved exact C','saved best C',0)


@pytest.mark.parametrize('include_all',[None,True])
def test_machine_draft_api_scopes_reports_and_starts_a_reviewable_job(network, tmp_path, monkeypatch, include_all):  # noqa: F811
    from starlette.testclient import TestClient

    from decompnet.app.server import build
    net,proj,fn=network
    internal=tmp_path/'.decomp';internal.mkdir()
    monkeypatch.setattr(net,'can_draft',lambda proj:True)
    monkeypatch.setattr(net.progress,'sync',lambda *args,**kwargs:False)
    queued=[]
    monkeypatch.setattr(draft_regeneration,'run',lambda net,slug,log,**kwargs:queued.append((slug,kwargs)) or {'completed':0})
    draft_reports.save(net,fn['id'],{'key':'input','compiled':True,'scored':False})
    mcp,_,security,_=build(net,internal,6509,'draft-tests')
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                      base_url='http://127.0.0.1:6509',headers={'User-Agent':'decompdecomp-window/draft-tests','X-Decomp':'1'})
    response=client.get('/api/drafts',params={'slug':proj['slug'],'function':fn['name']})
    assert response.status_code==200 and response.json()['report']['compiled']
    assert client.get('/api/drafts',params={'slug':'other','function':fn['name']}).status_code==400
    assert client.post('/api/drafts',json={'slug':proj['slug'],'include_all':'false'}).status_code==400
    body={'slug':proj['slug']}
    if include_all is not None:body['include_all']=include_all
    response=client.post('/api/drafts',json=body)
    assert response.status_code==200 and response.json()['job']
    import time
    for _ in range(100):
        if queued:break
        time.sleep(.01)
    assert queued==[(proj['slug'],{'include_all':bool(include_all),'threads':0})]
    assert net.store.one('SELECT status FROM functions WHERE id=?',fn['id'])['status']=='open'


def test_background_initial_compiles_do_not_flood_the_shared_worker_pool(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from decompnet.app import drafts, source_runtime
    monkeypatch.setattr(source_runtime,'checkpoint',lambda *args:None)
    monkeypatch.setattr(source_runtime,'idle_wait',lambda *args:None)
    net=SimpleNamespace(links={},draft_mark=lambda p:1,project_row=lambda s:{'slug':s},can_draft=lambda p:True,
                        stale_drafts=lambda p:list(range(6)),local_agent=lambda s:(object(),None))
    state=drafts.State();manager=drafts.Drafter(net)
    guard=threading.Lock();active=0;peak=0;done=0
    def one(*args):
        nonlocal active,peak,done
        with guard:active+=1;peak=max(peak,active)
        time.sleep(.01)
        with guard:
            active-=1;done+=1
            if done==6:state.stop.set()
    monkeypatch.setattr(manager,'_one',one)
    with ThreadPoolExecutor(6) as pool:
        monkeypatch.setattr(drafts.workers,'pool',lambda:pool)
        manager._loop('demo',state)
    assert done==6 and peak==2


@pytest.mark.skipif(not seeds.available() or toolchains.installed('gcc-2.7.2-kmc') is None,
                    reason='needs pinned m2c and the period compiler')
def test_explicit_regeneration_rechecks_identical_c_without_erasing_history(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    monkeypatch.setattr(seeds,'generate_from_asm',lambda *args,**kwargs:'int '+fn['name']+'(int arg0) { return arg0 + 99; }')
    agent,_=net.local_agent('autopilot')
    assert net._autopilot_one(agent,proj,fn['id']) in ('near','far')
    count=net.store.one('SELECT count(*) AS n FROM attempts WHERE function_id=?',fn['id'])['n']
    assert net._autopilot_one(agent,proj,fn['id'])=='skip'
    cfg=json.loads(proj['config']);cfg['draft_generation']=1
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    assert net._autopilot_one(agent,net.project_row(proj['slug']),fn['id']) in ('near','far')
    assert net.store.one('SELECT count(*) AS n FROM attempts WHERE function_id=?',fn['id'])['n']>count


def test_regeneration_reports_function_before_work_finishes_and_final_counts(network, monkeypatch):  # noqa: F811
    import threading

    net, proj, fn = network
    monkeypatch.setattr(net, 'can_draft', lambda proj: True)
    published = threading.Event()
    snapshots = []
    def seed(*args):
        assert published.wait(5), 'Progress must be published while the draft is still running'
        return 'fresh C'
    monkeypatch.setattr(draft_regeneration, 'generate', seed)
    monkeypatch.setattr(draft_regeneration, 'trial', lambda *args: ('near', {'compiled':True}))
    monkeypatch.setattr(draft_reports, 'read', lambda *args, **kwargs: {'compiled': True})
    class Log:
        def __call__(self, *args): pass
        def progress(self, data):
            snapshots.append(data)
            published.set()
    result = draft_regeneration.run(net, proj['slug'], Log())
    assert snapshots[0]['done'] == 0 and snapshots[0]['total'] == 1
    assert snapshots[0]['function'].startswith(fn['name']+': ')
    assert snapshots[-1]['done'] == snapshots[-1]['total'] == 1
    assert snapshots[-1]['function'] == ''
    assert snapshots[-1]['compiled'] == result['compiled'] == 1
