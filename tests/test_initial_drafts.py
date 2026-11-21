"""Initial drafts recover typed calls and overlapping frame views without editing shared inputs."""
import json
import shutil
import struct
import subprocess
from types import SimpleNamespace

import pytest

from decompnet import limits, seeds, toolchains
from decompnet.app import draft_data, draft_regeneration, draft_repairs, draft_reports
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


def test_seed_uses_prepared_inputs_without_replanning(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    monkeypatch.setattr(seeds,'available',lambda:True)
    monkeypatch.setattr(net,'_draft_plan',lambda *a,**kw:pytest.fail('must use captured plan'))
    monkeypatch.setattr(net,'_jtbl_asm',lambda *a:pytest.fail('must use captured data'))
    monkeypatch.setattr(net,'_asm_text',lambda *a:pytest.fail('must use captured assembly'))
    calls=[]
    def generate(*args,**kw):
        calls.append(args)
        return 'int '+fn['name']+'(void){return 1;}'
    monkeypatch.setattr(seeds,'generate_from_asm',generate)
    prepared=('captured-key','gcc-2.7.2-kmc','captured header','captured data','captured assembly')
    seed=net._seed_for(fn,proj,prepared)
    assert seed and len(calls)==1
    assert calls[0]==('captured assembly',fn['name'],'gcc-2.7.2-kmc','captured header','captured data')


def test_completed_autopilot_receipt_skips_seed_preparation(network,monkeypatch):  # noqa: F811
    net,proj,fn=network
    from decompnet.app import return_repair_autopilot
    monkeypatch.setattr(return_repair_autopilot,'run',lambda *a:False)
    monkeypatch.setattr(net,'_draft_plan',lambda *a,**kw:('same-plan','gcc-2.7.2-kmc',''))
    monkeypatch.setattr(net,'_jtbl_asm',lambda *a:[])
    monkeypatch.setattr(net,'_seed_for',lambda *a:pytest.fail('unchanged completion must skip seed preparation'))
    ctx=net._context(proj['id'],fn['unit'])
    key=draft_reports.key('same-plan',ctx['header'],'',fn)
    draft_reports.save(net,fn['id'],{'key':key,'complete':True},project_id=proj['id'])
    agent,_=net.local_agent('autopilot')
    assert net._autopilot_one_work(agent,proj,fn['id'])=='skip'


# (shaped like an N64 game's near miss: a 0x20-byte stack object at sp+0x10, `$ra` saved at 0x30)
CALL_ASM = '''glabel f
addiu $sp, $sp, -0x38
addiu $v0, $zero, 0x1
sw $a0, 0x14($sp)
addiu $a0, $sp, 0x10
sw $ra, 0x30($sp)
jal fill
sw $v0, 0x24($sp)
lw $v0, 0x2C($sp)
bnez $v0, .L1
addu $v0, $zero, $zero
lw $v0, 0x18($sp)
.L1:
lw $ra, 0x30($sp)
jr $ra
addiu $sp, $sp, 0x38
'''
WIDE = '''void fill(s32 *);

s32 f(s32 arg0) {
    union {
        unsigned char bytes[56];
        struct { unsigned char pad[16]; s32 value; } view_sp10;
        struct { unsigned char pad[20]; s32 value; } view_sp14;
        struct { unsigned char pad[24]; s32 value; } view_sp18;
        struct { unsigned char pad[36]; s32 value; } view_sp24;
        struct { unsigned char pad[44]; s32 value; } view_sp2C;
    } m2c_initial_frame;
    s32 var_v0;

    m2c_initial_frame.view_sp14.value = arg0;
    m2c_initial_frame.view_sp24.value = 1;
    fill(&m2c_initial_frame.view_sp10.value);
    if (m2c_initial_frame.view_sp2C.value != 0) { var_v0 = 0; } else {
        var_v0 = m2c_initial_frame.view_sp18.value;
    }
    return var_v0;
}
'''


def test_the_frame_union_spans_its_views_up_to_the_saved_registers():
    assert mips_drafts.read(CALL_ASM)['saved'] == 0x30
    (family, out), = repairs.frame_variants(WIDE, 'f', CALL_ASM, 'mips')
    assert family == 'initial_frame_narrowed'
    assert 'unsigned char bytes[32];' in out and 'struct { s32 value; } view_sp10;' in out
    assert 'struct { unsigned char pad[28]; s32 value; } view_sp2C;' in out
    assert out.count('m2c_initial_frame.view_') == WIDE.count('m2c_initial_frame.view_')
    # already narrow, no saved-register evidence, or a view reaching into the saved area: nothing
    assert repairs.frame_variants(out, 'f', CALL_ASM, 'mips') == []
    assert repairs.frame_variants(WIDE, 'f', CALL_ASM.replace('sw $ra, 0x30($sp)', 'nop'), 'mips') == []
    assert repairs.frame_variants(WIDE.replace('pad[44]', 'pad[48]'), 'f', CALL_ASM, 'mips') == []
    labels = {}
    texts = draft_repairs.variants(WIDE, 'f', 'mips', CALL_ASM, ['-O2'], labels, limit=None, header='typedef int s32;')
    assert any(labels.get(t) == 'initial_frame_narrowed' for t in texts)


def test_new_frame_unions_are_narrow_too():
    source = '''void fill(s32 *);

s32 f(s32 arg0) {
    s32 sp10;
    fill(&sp10);
    return sp18;
}
'''
    asm = CALL_ASM.replace('lw $v0, 0x2C($sp)', 'lw $v0, 0x18($sp)')
    out = repairs.stack_frame(source, 'f', asm, 'mips')
    assert out and 'unsigned char bytes[32];' in out and 'struct { unsigned char pad[8]; s32 value; } view_sp18;' in out
    # both are offered: the judge keeps whichever compiles closer
    families = [family for family, _ in repairs.variants(source, 'f', asm, 'mips')]
    assert families == ['initial_stack_frame', 'initial_stack_frame_whole']
    assert 'unsigned char bytes[56];' in repairs.variants(source, 'f', asm, 'mips')[1][1]


def test_kmc_places_the_narrow_object_where_the_game_has_it(tmp_path):
    import io

    import capstone
    from elftools.elf.elffile import ELFFile

    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('Pinned KMC compiler is not installed; never download it here.')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                                                  'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})

    def first(text):
        result = compile_source(cfg, 'typedef int s32;\n', text)
        assert result.ok, result.log
        code = ELFFile(io.BytesIO(result.obj)).get_section_by_name('.text').data()
        md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS64 + capstone.CS_MODE_BIG_ENDIAN)
        return next(f'{i.mnemonic} {i.op_str}' for i in md.disasm(code, 0))
    (_, narrow), = repairs.frame_variants(WIDE, 'f', CALL_ASM, 'mips')
    # the whole frame as one object grew the frame to 0x50; spanning its views it is the game's 0x38
    assert first(WIDE) == 'addiu $sp, $sp, -0x50' and first(narrow) == 'addiu $sp, $sp, -0x38'


def test_valid_kmc_draft_recovers_the_witnessed_copy(monkeypatch):
    raw = 'void f(void) { M2C_UNALIGNED32(input); }'
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args: raw)
    monkeypatch.setattr(seeds, 'intrinsic_leaf', lambda *args: None)
    monkeypatch.setattr(seeds, 'm2c_target', lambda *args: 'mips-gcc-c')
    from decomprepair import unaligned_stack_drafts

    calls = []

    def repair(*args):
        calls.append(args)
        return 'void f(void) { }'

    monkeypatch.setattr(unaligned_stack_drafts, 'rewrite', repair)
    resolve = {'global': 0x80000000}.get
    opts = dict(resolve=resolve)
    assert seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', 'header', valid=True, **opts) == 'void f(void) { }'
    assert seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', 'header', **opts) == raw
    assert seeds.generate_from_asm('asm', 'f', 'ido-5.3', 'header', valid=True, **opts) == raw
    assert calls == [(raw, 'f', 'asm', 'header', resolve)]


def test_copied_word_index_repair_runs_during_valid_kmc_draft_generation(monkeypatch):
    from decomprepair import measured_stack_drafts

    raw = 's32 f(s32 arg) { return *((arg * 4) + sp); }'
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args: raw)
    monkeypatch.setattr(seeds, 'intrinsic_leaf', lambda *args: None)
    monkeypatch.setattr(seeds, 'm2c_target', lambda *args: 'mips-gcc-c')
    calls = []

    def repair(*args):
        calls.append(args)
        return 's32 f(s32 arg) { return table[arg]; }'

    monkeypatch.setattr(measured_stack_drafts, 'rewrite', repair)
    assert 'table[arg]' in seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', 'header', valid=True)
    assert seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', 'header') == raw
    assert calls == [(raw, 'f', 'asm', 'header')]


def test_larger_stack_recovery_is_used_by_valid_kmc_drafts(monkeypatch):
    from decomprepair import large_stack_drafts

    raw = 'void f(void) { cursor = sp; }'
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args: raw)
    monkeypatch.setattr(seeds, 'intrinsic_leaf', lambda *args: None)
    monkeypatch.setattr(seeds, 'm2c_target', lambda *args: 'mips-gcc-c')
    monkeypatch.setattr(large_stack_drafts, 'rewrite', lambda *args: 'void f(void) { u8 buffer[48]; }')
    assert 'buffer[48]' in seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', valid=True)
    assert seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc') == raw
    assert seeds.generate_from_asm('asm', 'f', 'ido-5.3', valid=True) == raw


def test_shared_default_draft_recovery_receives_the_original_table(monkeypatch):
    from decomprepair import shared_default_drafts

    raw = 'void f(void) { case 1: return; }'
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args: raw)
    monkeypatch.setattr(seeds, 'intrinsic_leaf', lambda *args: None)
    monkeypatch.setattr(seeds, 'm2c_target', lambda *args: 'mips-gcc-c')
    calls = []

    def repair(*args):
        calls.append(args)
        return 'void f(void) { switch (1) { case 1: return; } }'

    monkeypatch.setattr(shared_default_drafts, 'rewrite', repair)
    assert 'switch' in seeds.generate_from_asm('asm', 'f', 'gcc-2.7.2-kmc', 'header', 'table', valid=True)
    assert seeds.generate_from_asm('asm', 'f', 'ido-5.3', 'header', 'table', valid=True) == raw
    assert calls == [(raw, 'f', 'asm', 'table', 'header')]


def test_valid_kmc_draft_expands_shared_cursor_branch_and_repairs_source(monkeypatch):
    from decompnet import seeds
    from decomprepair import likely_branch_cursor

    seen = []
    monkeypatch.setattr(seeds, 'intrinsic_leaf', lambda *args: None)
    monkeypatch.setattr(seeds, 'm2c_target', lambda *args: 'mips')
    monkeypatch.setattr(seeds, 'run_m2c', lambda asm, *args: seen.append(asm) or 'void f(void) {}')
    monkeypatch.setattr(likely_branch_cursor, 'draft_asm', lambda asm: 'expanded ' + asm)

    def rewrite(source, name, asm, header):
        assert (source, name, asm, header) == ('void f(void) {}', 'f', 'original', 'header')
        return 'repaired draft'

    monkeypatch.setattr(likely_branch_cursor, 'rewrite', rewrite)
    assert seeds.generate_from_asm('original', 'f', 'gcc-2.7.2-kmc', 'header', valid=True) == 'repaired draft'
    assert seeds.generate_from_asm('original', 'f', 'ido-5.3', valid=True) == 'void f(void) {}'
    assert seen == ['expanded original', 'original']


def test_kmc_repairs_indirect_table_calls_in_fresh_and_recovery_drafts(monkeypatch):
    asm = """
sll $v0, $a1, 2
lui $at, %hi(handlers)
addu $at, $at, $v0
lw $v0, %lo(handlers)($at)
jalr $v0
move $a0, $s0
"""
    raw = """
extern M2C_UNK handlers;
void f(void *arg, s32 i) {
    *(&handlers + (i * 4))(arg);
}
"""
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args, **kwargs: raw)
    expected = '(*(void (**)())((char *)&handlers + (i * 4)))(arg);'
    assert expected in seeds.generate_from_asm(asm, 'f', 'gcc-2.7.2-kmc', valid=True)
    choices = repairs.recovery_drafts(raw, 'f', asm, 'mips', 'gcc-2.7.2-kmc', '', None, {'failures': []})
    family, draft = next(choices)
    assert family == 'initial_typed_indirect_call'
    assert expected in draft
    assert expected not in seeds.generate_from_asm(asm.replace('jalr $v0', 'jalr $v1'),
                                                   'f', 'gcc-2.7.2-kmc', valid=True)


def test_kmc_recovers_stack_base_for_a_word_clear_in_fresh_and_recovery_drafts(monkeypatch):
    asm = '''
addiu $sp, $sp, -32
move $v1, $sp
addiu $v0, $zero, 4
.Lfill:
sw $zero, ($v1)
addiu $v0, $v0, -1
bnez $v0, .Lfill
addiu $v1, $v1, 4
addiu $v0, $zero, 1
.Lidle:
bnez $v0, .Lidle
nop
jr $ra
addiu $sp, $sp, 32
'''
    raw = '''
void f(void) {
    s32 *ptr;
    s32 count;
    ptr = sp;
    count = 4;
    do {
        *ptr = 0;
        count -= 1;
        ptr += 4;
    } while (count != 0);
    do {} while (1 != 0);
}
'''
    monkeypatch.setattr(seeds, 'run_m2c', lambda *args, **kwargs: raw)
    expected = 's32 __repair_clear_words[8];'
    assert expected in seeds.generate_from_asm(asm, 'f', 'gcc-2.7.2-kmc', valid=True)
    choices = repairs.recovery_drafts(raw, 'f', asm, 'mips', 'gcc-2.7.2-kmc', '', None, {'failures': []})
    family, draft = next(choices)
    assert family == 'initial_stack_word_clear'
    assert expected in draft
