import json
import struct

import pytest

from decompnet.app import boundary_review, rom_gap_audit
from decompnet.arch import get, mips
from decompnet.arch.mips_gaps import audit
from decompnet.consoles import get as console


def words(*w):return struct.pack('>'+str(len(w))+'I',*w)


def fixture():
    image=bytearray(0x1200)
    # A known CPU function calls a leaf in a raw range, then returns.
    image[0x1000:0x1010]=words(0x0c000408,0,0x03e00008,0)
    image[0x1020:0x1028]=words(0x03e00008,0x24020001)
    return bytes(image),[dict(rom_start=0x1000,rom_end=0x1010,vram=0x80001000)], [dict(name='caller',rom=0x1000,size=16,vram=0x80001000)]


def test_unrecorded_entry_code_exposes_only_reachable_closed_calls_for_review():
    image = bytearray(0x1100)
    image[0x1000:0x1010] = words(0x0c000408, 0, 0x0000000d, 0)  # call, then stop at break
    image[0x1020:0x1030] = words(0x8fa80010, 0x40885000, 0x03e00008, 0)
    segments = [dict(rom_start=0x1000, rom_end=0x1040, vram=0x80001000)]
    result = audit(bytes(image), segments, [], entrypoints=(0x80001000,))
    candidate = next(row for row in result['candidates'] if row['rom_offset'] == 0x1020)
    assert candidate['review_only'] and candidate['direct_flow_closed']
    assert candidate['incoming'][0]['function'] == 'configured entry'
    assert candidate['incoming'][0]['address'] == 0x80001000
    image[0x1000:0x1004] = words(0x0000000d)  # a call after the break is not reachable
    image[0x1004:0x1008] = words(0x0c000408)
    assert not any(row['rom_offset'] == 0x1020 for row in
                   audit(bytes(image), segments, [], entrypoints=(0x80001000,))['candidates'])


def test_entry_call_is_not_lost_when_other_candidates_reach_the_limit(monkeypatch):
    from decompnet.arch import mips_gaps

    monkeypatch.setattr(mips_gaps, 'MAX_CANDIDATES', 1)
    image = bytearray(0x1100)
    image[0x1000:0x1010] = words(0x0c000408, 0, 0x0000000d, 0)
    image[0x1020:0x1030] = words(0x8fa80010, 0x40885000, 0x03e00008, 0)
    image[0x1040:0x1050] = words(0x0c000418, 0, 0x03e00008, 0)
    image[0x1060:0x1070] = words(0x24020001, 0x24030002, 0x03e00008, 0)
    segments = [dict(rom_start=0x1000, rom_end=0x1080, vram=0x80001000)]
    functions = [dict(name='other', rom=0x1040, vram=0x80001040, size=16)]
    result = audit(bytes(image), segments, functions, entrypoints=(0x80001000,))
    assert result['candidate_limit']
    assert [row['rom_offset'] for row in result['candidates']] == [0x1020]


def test_incoming_call_evidence_is_closed_but_never_an_automatic_import():
    image,segments,functions=fixture();result=audit(image,segments,functions)
    row=result['candidates'][0]
    assert row['rom_offset']==0x1020 and row['vram']==0x80001020
    assert row['incoming'][0]['function']=='caller' and row['direct_flow_closed']
    assert row['extent_bytes']==8 and row['review_only']
    assert 'No targets' in result['note'] and image==fixture()[0]


def test_direct_transfer_marks_a_different_rom_mapping_for_review():
    image = bytearray(0x3000)
    image[0x1000:0x1010] = words(0x0c000408, 0, 0x03e00008, 0)
    image[0x1020:0x1028] = words(0x03e00008, 0)
    image[0x2020:0x2028] = words(0x03e00008, 0)
    segments = [dict(rom_start=0x1000, rom_end=0x1010, vram=0x80001000),
                dict(rom_start=0x1100, rom_end=0x1110, vram=0x80000100)]
    functions = [dict(name='caller', rom=0x1000, size=16, vram=0x80001000)]
    rows = {row['rom_offset']: row for row in audit(bytes(image), segments, functions)['candidates']}
    assert rows[0x1020]['incoming'][0]['mapping_relation'] == 'same'
    assert rows[0x2020]['incoming'][0]['mapping_relation'] == 'alternative'
    assert rows[0x2020]['review_only']


def test_full_audit_prioritizes_same_mapping_without_dropping_alternatives():
    image = bytearray(0x3000)
    image[0x2000:0x2010] = words(0x0c000808, 0, 0x03e00008, 0)
    image[0x1020:0x1028] = words(0x03e00008, 0)
    image[0x2020:0x2028] = words(0x03e00008, 0)
    segments = [dict(rom_start=0x1000, rom_end=0x1010, vram=0x80002000),
                dict(rom_start=0x2000, rom_end=0x2010, vram=0x80002000)]
    functions = [dict(name='caller', rom=0x2000, size=16, vram=0x80002000)]
    rows = audit(bytes(image), segments, functions, full_scan=True)['candidates']
    same = next(i for i,row in enumerate(rows) if row['rom_offset'] == 0x2020)
    other = next(i for i,row in enumerate(rows) if row['rom_offset'] == 0x1020)
    assert same < other
    assert rows[same]['incoming'][0]['mapping_relation'] == 'same'
    assert rows[other]['incoming'][0]['mapping_relation'] == 'alternative'


def test_same_mapping_witness_is_retained_after_many_alternative_calls():
    image = bytearray(0x5000)
    functions = []
    for i in range(9):
        at = 0x1000 + 16*i
        image[at:at+16] = words(0x0c001000, 0, 0x03e00008, 0)
        functions.append(dict(name=f'other_{i}',rom=at,size=16,vram=0x80001000+16*i))
    image[0x2000:0x2010] = words(0x0c001000, 0, 0x03e00008, 0)
    functions.append(dict(name='same',rom=0x2000,size=16,vram=0x80003000))
    image[0x3000:0x3008] = words(0x03e00008, 0)
    segments = [dict(rom_start=0x1000,rom_end=0x1090,vram=0x80001000),
                dict(rom_start=0x2000,rom_end=0x2010,vram=0x80003000)]
    result = audit(bytes(image), segments, functions, full_scan=True)
    row = next(r for r in result['candidates'] if r['rom_offset']==0x3000 and r['vram']==0x80004000)
    assert len(row['incoming']) == 8
    assert any(ref['function']=='same' and ref['mapping_relation']=='same' for ref in row['incoming'])


def test_full_audit_processes_all_direct_candidates_and_can_be_cancelled(monkeypatch):
    from decompnet.arch import mips_gaps

    image = bytearray(0x2000)
    targets = [0x80001200 + i*8 for i in range(12)]
    source = words(*(0x0c000000 | (target >> 2 & 0x03ffffff) for target in targets), 0x03e00008, 0)
    image[0x1000:0x1000+len(source)] = source
    for i in range(len(targets)):
        image[0x1200+i*8:0x1208+i*8] = words(0x03e00008, 0)
    segments = [dict(rom_start=0x1000, rom_end=0x1000+len(source), vram=0x80001000)]
    functions = [dict(name='caller', rom=0x1000, size=len(source), vram=0x80001000)]
    monkeypatch.setattr(mips_gaps, 'MAX_CANDIDATES', 8)
    bounded = audit(bytes(image), segments, functions)
    updates = []
    complete = audit(bytes(image), segments, functions, full_scan=True, progress=updates.append)
    assert len(bounded['candidates']) == 8 and bounded['candidate_limit']
    assert len(complete['candidates']) >= len(targets) and not complete['candidate_limit']
    assert all(row['review_only'] for row in complete['candidates'])
    assert any(update.get('step') == 5 and update.get('total', 0) >= len(targets) for update in updates)

    def stopped():
        raise RuntimeError('Stopped')

    with pytest.raises(RuntimeError, match='Stopped'):
        audit(bytes(image), segments, functions, full_scan=True, check=stopped)


def test_full_audit_checks_quiet_hints_under_each_established_mapping():
    image = bytearray(0x3000)
    image[0x1000:0x1008] = words(0x03e00008, 0)
    image[0x1200:0x1210] = words(0x27bdffe8, 0, 0x03e00008, 0x27bd0018)
    segments = [dict(rom_start=0x1000, rom_end=0x1010, vram=0x80001000),
                dict(rom_start=0x1100, rom_end=0x1110, vram=0x80000100)]
    functions = [dict(name='caller', rom=0x1000, size=8, vram=0x80001000)]
    result = audit(bytes(image), segments, functions, full_scan=True)
    hints = {(row['rom_offset'], row['vram']) for row in result['candidates']
             if row['evidence'] == 'frame/return pattern only'}
    assert (0x1200, 0x80001200) in hints
    assert (0x1200, 0x80000200) in hints


def test_project_audit_uses_unscoped_configured_entry_but_not_replaced_boot(tmp_path, monkeypatch):
    from decompnet.app import analysis_image, code_audit_reviews
    from decompnet.app.engine import RomNetwork
    from decompnet.rom.split import Segment

    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    tiny_game(net, functions=1)
    seen = []
    segment = Segment('entry', 0x1000, 0x1010, 0x80000400)
    monkeypatch.setattr(net, '_rom', lambda proj: (bytes(0x2000), [segment]))
    monkeypatch.setattr(net, 'cfg', lambda proj: {'kind': 'rom', 'rom': {'entrypoint': 0x80100400}})
    monkeypatch.setattr(analysis_image, 'verify', lambda *args, **kwargs: None)
    monkeypatch.setattr(code_audit_reviews, 'record', lambda net, pid, result, image, **kwargs: result)

    def scan(*args, **kwargs):
        seen.append(kwargs['entrypoints'])
        return {'gaps': 0, 'candidates': []}

    monkeypatch.setattr(rom_gap_audit, 'audit', scan)
    rom_gap_audit.run(net, 'game', lambda *_: None)
    segment.exclusive_ram_id = 'replaced_boot'
    rom_gap_audit.run(net, 'game', lambda *_: None)
    assert seen == [(0x80100400, 0x80000400), (0x80100400,)]


def test_full_project_audit_passes_cancellation_and_full_scan_to_the_reader(tmp_path, monkeypatch):
    from decompnet.app import analysis_image, code_audit_reviews
    from decompnet.app.engine import RomNetwork

    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    tiny_game(net, functions=1)
    seen = {}

    class Log:
        def __call__(self, *_):
            pass

        def check_cancel(self):
            seen['checked'] = True

    log = Log()

    def scan(*args, **kwargs):
        seen.update(kwargs)
        kwargs['check']()
        return {'gaps': 0, 'candidates': []}

    monkeypatch.setattr(rom_gap_audit, 'audit', scan)
    monkeypatch.setattr(net, '_rom', lambda proj: (bytes(0x2000), []))
    monkeypatch.setattr(analysis_image, 'verify', lambda *args, **kwargs: None)
    monkeypatch.setattr(code_audit_reviews, 'record', lambda net, pid, result, image, **kwargs: result)
    result = rom_gap_audit.run(net, 'game', log, full_scan=True)
    assert result['candidates'] == [] and seen['full_scan'] and seen['checked']


def test_full_project_audit_prioritizes_matching_runtime_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from decompnet.app import analysis_image, code_audit_reviews
    from decompnet.app.engine import RomNetwork

    from .test_loading import tiny_game

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    tiny_game(net, functions=1)
    candidates = [dict(rom_offset=0x1200,vram=0x80001200,extent_bytes=16,incoming=[],direct_flow_closed=True),
                  dict(rom_offset=0x1300,vram=0x80001300,extent_bytes=16,incoming=[],direct_flow_closed=True)]
    evidence = [dict(rom_start=0x1300,rom_end=0x1310,vram=0x80001300,scope='slot',co_resident_snapshots=3),
                dict(kind='runtime_location',rom_start=0x1200,rom_end=0x1210,vram=0x80004000,snapshots=1,
                     executed=True,execution_retrace=58)]
    console = SimpleNamespace(cpu_variant='vr4300',asm_reason=lambda _:None,
                              runtime_audit_candidates=lambda *args:evidence)
    monkeypatch.setattr(net, 'console', lambda proj:console)
    monkeypatch.setattr(net, '_rom', lambda proj:(bytes(0x2000), []))
    monkeypatch.setattr(analysis_image, 'verify', lambda *args,**kwargs:None)
    monkeypatch.setattr(rom_gap_audit, 'audit', lambda *args,**kwargs:{'gaps':1,'candidates':candidates})
    monkeypatch.setattr(code_audit_reviews, 'record', lambda net,pid,result,image,**kwargs:result)
    result = rom_gap_audit.run(net, 'game', lambda *_:None, full_scan=True)
    assert result['candidates'][0]['rom_offset'] == 0x1200
    assert result['candidates'][0]['runtime_locations'] == [
        {'vram':0x80004000,'snapshots':1,'executed':True,'execution_retrace':58}]
    assert result['candidates'][1]['runtime_scope']['co_resident_snapshots'] == 3
    assert 'runtime_scope' not in result['candidates'][0]


def test_unresolved_indirect_branch_and_data_like_frames_are_not_proofs():
    image,segments,functions=fixture();b=bytearray(image);b[0x1020:0x1028]=words(0x00400008,0)
    candidate=audit(bytes(b),segments,functions)['candidates'][0]
    assert not candidate['direct_flow_closed']
    assert candidate['unresolved_transfers']==[{'address':0x80001020,'register':2}]
    b[0x1040:0x1050]=words(0x27bdffe8,0,0x03e00008,0x27bd0018)
    hints=[r for r in audit(bytes(b),segments,functions)['candidates'] if not r['incoming']]
    assert hints and all(r['review_only'] for r in hints)


def test_hardware_or_microcode_source_is_not_cpu_call_evidence_and_bounds_are_checked():
    image,segments,functions=fixture();functions[0]['handwritten']=True
    assert not audit(image,segments,functions)['incoming_references']
    with pytest.raises(ValueError):audit(image,[dict(rom_start=0,rom_end=len(image)+4,vram=0)],functions)


def test_export_hash_and_paths_are_verified(tmp_path):
    manifest=dict(platform='n64',image='rom.z64',sha1='wrong',functions=[],segments=[])
    (tmp_path/'rom.z64').write_bytes(b'rom');(tmp_path/'build.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='hash'):rom_gap_audit.exported(tmp_path)
    manifest['image']='../outside.z64';(tmp_path/'build.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='inside'):rom_gap_audit.exported(tmp_path)


def test_export_audit_uses_only_matching_unscoped_entry(tmp_path, monkeypatch):
    import hashlib

    import yaml

    image = bytes(0x1100)
    (tmp_path / 'rom.z64').write_bytes(image)
    manifest = {'platform': 'n64', 'image': 'rom.z64', 'sha1': hashlib.sha1(image).hexdigest(), 'functions': [],
                'segments': [{'name': 'entry', 'kind': 'code', 'start': 0x1000, 'end': 0x1010,
                              'vram': 0x80000400}]}
    (tmp_path / 'build.json').write_text(json.dumps(manifest))
    entry = {'name': 'entry', 'type': 'code', 'start': 0x1000, 'vram': 0x80000400}
    config = tmp_path / 'splat.yaml'
    seen = []

    def scan(*args, **kwargs):
        seen.append(kwargs['entrypoints'])
        return {}

    monkeypatch.setattr(rom_gap_audit, 'audit', scan)
    config.write_text(yaml.safe_dump({'segments': [entry]}))
    rom_gap_audit.exported(tmp_path)
    entry['exclusive_ram_id'] = 'replaced_boot'
    config.write_text(yaml.safe_dump({'segments': [entry]}))
    rom_gap_audit.exported(tmp_path)
    entry.pop('exclusive_ram_id')
    entry['vram'] += 4
    config.write_text(yaml.safe_dump({'segments': [entry]}))
    rom_gap_audit.exported(tmp_path)
    assert seen == [(0x80000400,), (), ()]


def test_mips_incomplete_end_is_review_not_assembly_for_all_console_abis():
    for platform in ('n64','psx','ps2','psp'):
        c=console(platform);data=words(0x27bdffe8) if c.big_endian else words(0x27bdffe8)[::-1]
        assert c.asm_reason(data) is None
        assert 'may be truncated' in c.boundary_reason(data)
    assert get('mips').boundary_reason is mips.boundary_reason
    assert console('n64').boundary_reason(words(0x40026000)) is None  # actual hardware assembly still wins
    assert mips.boundary_reason(words(0x03e00008,0)) is None


def test_only_retired_automatic_fallthrough_verdict_is_reopened(tmp_path):
    from decompnet.app.engine import RomNetwork
    net=RomNetwork(tmp_path/'audit.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','game','mips',?,'{}')",(str(tmp_path),)).lastrowid
        for name,meta in [('automatic',{'asm_code':boundary_review.LEGACY}),('operator',{'asm_code':'you set it aside: asm','not_c':{'you':{}}})]:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,status,meta) VALUES(?,?,4,1,?,'asm',?)",(pid,name,words(0x27bdffe8),json.dumps(meta)))
    proj={'id':pid};boundary_review.refresh(net,proj,console('n64'))
    a=net.store.one("SELECT * FROM functions WHERE name='automatic'");b=net.store.one("SELECT * FROM functions WHERE name='operator'")
    assert a['status']=='open' and json.loads(a['meta'])['boundary_review']
    assert b['status']=='asm'
    with net.store.tx() as db:db.execute('UPDATE functions SET target=? WHERE id=?',(words(0x03e00008,0),a['id']))
    boundary_review.refresh(net,proj,console('n64'))
    assert 'boundary_review' not in json.loads(net.store.one('SELECT meta FROM functions WHERE id=?',a['id'])['meta'])


def test_audit_ui_has_shared_progress_and_safe_source_links():
    from pathlib import Path
    root=Path(__file__).parents[1]/'src/decompnet/app/static'
    script=(root/'js/project.js').read_text()
    assert 'beginWork(log,"Auditing code coverage"' in script
    assert 'await followJob(session.job,log' in script and "'/api/job/cancel'" in script
    assert 'runCoverageAudit(true)' in (root/'js/steering.js').read_text()
    review=(root/'js/code-audit.js').read_text()
    assert 'data-audit-source' in review and 'esc(i.function)' in review
    assert 'data-review-save' in review and 'esc(r.review?.notes' in review
    assert 'fnBoundaryReview' in (root/'js/function.js').read_text()


def test_agent_cannot_turn_incomplete_target_into_assembly_even_before_refresh(tmp_path):
    from decompnet.app.engine import RomNetwork
    from decompnet.network import Agent
    net=RomNetwork(tmp_path/'votes.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','game','mips',?,?)",(str(tmp_path),json.dumps({'platform':'n64','kind':'rom'}))).lastrowid
        fid=db.execute("INSERT INTO functions(project_id,name,size,n_insns,target,status) VALUES(?,'f',4,1,?,'open')",(pid,words(0x27bdffe8))).lastrowid
        fn=db.execute('SELECT * FROM functions WHERE id=?',(fid,)).fetchone()
        for n in range(3):
            agent=Agent(n+1,f'model-{n}',False)
            result=net._not_c(db,fn,agent,'no C makes it: no return')
            assert 'No assembly verdict recorded' in result
    assert net.store.one('SELECT status FROM functions WHERE id=?',fid)['status']=='open'


def test_pointer_and_unowned_data_interiors_are_review_only():
    image=bytearray(0x1200)
    image[0x1000:0x1010]=words(0x03e00008,0,0,0)
    image[0x1040:0x1050]=words(0x27bdffe8,0,0x03e00008,0x27bd0018)
    image[0x1100:0x1104]=words(0x80001040)
    segments=[dict(rom_start=0x1000,rom_end=0x1080,vram=0x80001000)]
    fs=[dict(name='known',rom=0x1000,size=16,vram=0x80001000)]
    result=audit(bytes(image),segments,fs)
    row=next(r for r in result['candidates'] if r['rom_offset']==0x1040)
    assert row['region']=='unowned segment interior' and row['review_only']
    assert row['pointer_references']==[{'rom_offset':0x1100,'value':0x80001040}]
    assert not row['incoming'] and result['interior_regions']==1
    # An address embedded in known instructions is not a data-pointer reference.
    image[0x1100:0x1104]=words(0);image[0x1000:0x1004]=words(0x80001040)
    assert not audit(bytes(image),segments,fs)['pointer_references']


def test_pointer_audit_respects_little_endian_and_rejects_unclosed_destination():
    image=bytearray(0x1200)
    image[0x1040:0x1050]=struct.pack('<4I',0x27bdffe8,0,0x03e00008,0x27bd0018)
    image[0x1100:0x1104]=struct.pack('<I',0x80001040)
    segments=[dict(rom_start=0x1000,rom_end=0x1010,vram=0x80001000)]
    assert audit(bytes(image),segments,[],big_endian=False)['pointer_references']==1
    image[0x1048:0x104c]=struct.pack('<I',0x00400008)
    assert not audit(bytes(image),segments,[],big_endian=False)['pointer_references']


def test_n64_only_recovers_whole_called_balanced_gaps():
    from decompnet.consoles.n64.code_references import complete
    image=bytearray(0x1100)
    image[0x1000:0x1010]=words(0x0c000404,0,0x03e00008,0)
    image[0x1010:0x1020]=words(0x27bdffe8,0,0x03e00008,0x27bd0018)
    known=[(0x1000,0x1010,0x80001000),(0x1020,0x1030,0x80001020)]
    assert complete(bytes(image),known)==[(0x1010,0x1020,0x80001010)]
    image[0x101c:0x1020]=words(0x27bd0020)
    assert not complete(bytes(image),known)  # mismatched frame restoration
    image[0x101c:0x1020]=words(0x27bd0018);image[0x1000:0x1004]=words(0)
    assert not complete(bytes(image),known)  # a frame/return alone is not enough


def test_n64_recovers_a_gap_only_its_data_points_at():
    """#V-Rally: command handlers called only through a table of pointers have no jal; their stored address
    counts under the mapping the code on both sides shares, never inside code or across differing mappings."""
    from decompnet.consoles.n64.code_references import complete
    image=bytearray(0x1200)
    image[0x1000:0x1010]=words(0x03e00008,0,0,0)
    image[0x1010:0x1020]=words(0x27bdffe8,0,0x03e00008,0x27bd0018)
    known=[(0x1000,0x1010,0x80001000),(0x1020,0x1030,0x80001020)]
    assert not complete(bytes(image),known)
    image[0x1028:0x102c]=words(0x80001010)
    assert not complete(bytes(image),known)  # an instruction equal to the address is no pointer
    image[0x1028:0x102c]=words(0);image[0x1180:0x1184]=words(0x80001010)
    assert complete(bytes(image),known)==[(0x1010,0x1020,0x80001010)]
    assert not complete(bytes(image),[known[0],(0x1020,0x1030,0x80002020)])  # neighbours disagree


def test_n64_gap_with_a_switch_follows_its_table_in_declared_data():
    from decompnet.consoles.n64.code_references import complete
    image=bytearray(0x1200)
    image[0x1000:0x1010]=words(0x03e00008,0,0,0)
    body=[0x27bdffe8,                         # 1010 frame
          (11<<26)|(4<<21)|(9<<16)|2,         # 1014 sltiu t1, a0, 2
          (4<<26)|(9<<21)|9,                  # 1018 beqz t1, 1040
          0,
          (4<<16)|(10<<11)|(2<<6),            # 1020 sll t2, a0, 2
          (15<<26)|(8<<16)|0x8000,            # 1024 lui t0, 0x8000
          (8<<21)|(10<<16)|(8<<11)|33,        # 1028 addu t0, t0, t2
          (35<<26)|(8<<21)|(11<<16)|0x1180,   # 102c lw t3, 0x1180(t0)
          (11<<21)|8,0]                       # 1030 jr t3
    image[0x1010:0x1010+4*len(body)]=words(*body)
    image[0x10f8:0x1100]=words(0x03e00008,0x27bd0018)
    image[0x1180:0x1188]=words(0x80001038,0x80001040)
    image[0x1190:0x1194]=words(0x80001010)
    known=[(0x1000,0x1010,0x80001000),(0x1100,0x1110,0x80001100)]
    assert not complete(bytes(image),known)  # the table's address isn't mapped: its cases can't be followed
    assert complete(bytes(image),known,[(0x1000,0x1200,0x80001000)])==[(0x1010,0x1100,0x80001010)]


def test_boundary_scan_preserves_a_match_saved_during_analysis(tmp_path):
    from types import SimpleNamespace

    from decompnet.app.engine import RomNetwork
    from tests.test_loading import tiny_game

    net = RomNetwork(tmp_path / 'audit.db', tmp_path)
    tiny_game(net, functions=1)
    proj = net.project_row('game', sync=False)

    def analyze(_):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched', meta='{\"saved\":true}' WHERE project_id=?", (proj['id'],))
        return "A warning for the old snapshot"

    assert boundary_review.refresh(net, proj, SimpleNamespace(boundary_reason=analyze)) == 0
    row = net.store.one("SELECT status,meta FROM functions WHERE project_id=?", proj['id'])
    assert row['status'] == 'matched'
    assert json.loads(row['meta']) == {'saved': True}
