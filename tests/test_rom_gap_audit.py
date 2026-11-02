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


def test_incoming_call_evidence_is_closed_but_never_an_automatic_import():
    image,segments,functions=fixture();result=audit(image,segments,functions)
    row=result['candidates'][0]
    assert row['rom_offset']==0x1020 and row['vram']==0x80001020
    assert row['incoming'][0]['function']=='caller' and row['direct_flow_closed']
    assert row['extent_bytes']==8 and row['review_only']
    assert 'No targets' in result['note'] and image==fixture()[0]


def test_unresolved_indirect_branch_and_data_like_frames_are_not_proofs():
    image,segments,functions=fixture();b=bytearray(image);b[0x1020:0x1028]=words(0x00400008,0)
    assert not audit(bytes(b),segments,functions)['candidates'][0]['direct_flow_closed']
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
    script=(root/'js/steering.js').read_text()
    assert 'beginWork(log,"Auditing unanalysed code",[button])' in script
    assert 'await followJob(job,log)' in script and 'finally {finish();' in script
    assert 'data-audit-source' in script and 'esc(i.function)' in script
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
