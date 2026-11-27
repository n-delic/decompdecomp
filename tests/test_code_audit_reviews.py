"""Saved review decisions are evidence, never a new function status."""
import copy

import pytest

from decompnet.app import code_audit_reviews as reviews
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def fixture(tmp_path):
    net=RomNetwork(tmp_path/'test.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('demo','demo','mips',?,'{}')",(str(tmp_path),)).lastrowid
    report={'candidates':[dict(rom_offset=16,vram=4096,gap_start=16,gap_end=32,extent_bytes=8)],'gaps':1}
    return net,pid,report


def test_reviews_persist_and_refresh_only_for_identical_image_and_extent(tmp_path):
    net,pid,report=fixture(tmp_path)
    first=reviews.record(net,pid,copy.deepcopy(report),bytes(64));key=first['candidates'][0]['review_id']
    reviews.review(net,'demo',key,'confirmed_code','Checked caller and return.')
    compact=reviews.review(net,'demo',key,'confirmed_code','Checked caller and return.',compact=True)
    assert compact['candidate']['review_id']==key and compact['candidate']['review']['state']=='confirmed_code'
    assert 'candidates' not in compact
    again=reviews.record(net,pid,copy.deepcopy(report),bytes(64))
    assert again['candidates'][0]['review']['state']=='confirmed_code'
    assert reviews.decorated(reviews.load(net,pid))==again
    changed=reviews.record(net,pid,copy.deepcopy(report),b'x'*64)
    assert changed['candidates'][0]['review']['state']=='unreviewed'
    with pytest.raises(NetworkError,match='expired'):reviews.review(net,'demo',key,'dismissed','not this image')
    assert not net.store.all('SELECT * FROM functions')


def test_decisions_need_evidence_validate_notes_and_are_project_scoped(tmp_path):
    net,pid,report=fixture(tmp_path);r=reviews.record(net,pid,report,bytes(64));key=r['candidates'][0]['review_id']
    for state,notes in [('bogus','x'),('dismissed',''),('confirmed_code',' '),('unreviewed','x'*4001)]:
        with pytest.raises(NetworkError):reviews.review(net,'demo',key,state,notes)
    reviews.review(net,'demo',key,'needs_investigation','Trace indirect call.')
    with net.store.tx() as db:other=db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('other','other','mips',?,'{}')",(str(tmp_path),)).lastrowid
    assert not reviews.decorated(reviews.load(net,other))['available']
    with pytest.raises(NetworkError):reviews.review(net,'other',key,'dismissed','different project')
    assert reviews.review(net,'demo',key,'unreviewed','')['candidates'][0]['review']['state']=='unreviewed'


def test_layout_changes_expire_saved_results_and_decisions(tmp_path):
    net,pid,report=fixture(tmp_path);first=reviews.record(net,pid,report,bytes(64))
    key=first['candidates'][0]['review_id']
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,vram,size) VALUES(?,'new_target',4096,8)",(pid,))
    assert reviews.decorated(reviews.load(net,pid))['stale']
    with pytest.raises(NetworkError,match='expired'):reviews.review(net,'demo',key,'confirmed_code','old layout')


def test_cancelled_report_does_not_replace_saved_audit(tmp_path):
    net, pid, report = fixture(tmp_path)
    first = reviews.record(net, pid, copy.deepcopy(report), bytes(64))
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Stopped')

    changed = copy.deepcopy(report)
    changed['gaps'] = 2
    with pytest.raises(RuntimeError, match='Stopped'):
        reviews.record(net, pid, changed, bytes(64), check=check)
    saved = reviews.decorated(reviews.load(net, pid))
    assert saved['gaps'] == first['gaps']


def test_runtime_checkpoints_are_scoped_to_image_and_layout(tmp_path):
    net,pid,_=fixture(tmp_path)
    item={'rom_start':16,'rom_end':80,'vram':4096,'executed':True,'execution_retrace':58,'direct_call_site':8192}
    checkpoint=reviews.runtime_checkpoint(net,pid,'layout',bytes(128))
    assert checkpoint(item) is None
    checkpoint(item,save=True)
    assert reviews.runtime_checkpoint(net,pid,'layout',bytes(128))(item)=={
        'executed':True,'execution_retrace':58,'direct_call_site':8192}
    assert reviews.runtime_checkpoint(net,pid,'changed',bytes(128))(item) is None
    assert reviews.runtime_checkpoint(net,pid,'layout',b'x'*128)(item) is None
    assert checkpoint({**item,'vram':4100}) is None


def test_full_indirect_scan_resumes_completed_functions_and_invalidates_inputs(tmp_path, monkeypatch):
    from decompnet.arch import mips_callback_flow, mips_indirect

    net,pid,_=fixture(tmp_path)
    functions=[dict(name=f'f{i}',rom=i*4,size=4,vram=0x80000000+i*4,handwritten=False) for i in range(65)]
    seen=[]
    def trace(image,group,order,max_words,max_calls):
        name=group[0]['name'];seen.append(name)
        return [dict(function=name,address=group[0]['vram'],load_address=(0x80001000,{}),evidence_chain=[])],False
    monkeypatch.setattr(mips_callback_flow,'trace',trace)
    checkpoint=reviews.indirect_checkpoint(net,pid,'layout',bytes(260),functions,'big')
    def stop():
        if len(seen)==33:raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError,match='Stopped'):
        mips_indirect.calls(bytes(260),functions,'big',full_scan=True,check=stop,checkpoint=checkpoint)
    assert checkpoint['load']()[0]==32
    result,limited=mips_indirect.calls(bytes(260),functions,'big',full_scan=True,checkpoint=checkpoint)
    assert not limited and len(result)==65 and seen.count('f0')==1 and seen.count('f32')==2
    assert reviews.indirect_checkpoint(net,pid,'changed',bytes(260),functions,'big')['load']() is None
    assert reviews.indirect_checkpoint(net,pid,'layout',b'x'*260,functions,'big')['load']() is None
    assert reviews.indirect_checkpoint(net,pid,'layout',bytes(260),functions,'little')['load']() is None
