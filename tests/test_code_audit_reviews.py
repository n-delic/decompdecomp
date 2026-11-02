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
