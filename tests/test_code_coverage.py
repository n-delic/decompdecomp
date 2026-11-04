"""Coverage checks need only original bytes, mappings and function extents, never source."""
import json
import struct

import pytest

from decompnet.app import code_audit_reviews, code_coverage
from decompnet.app.engine import RomNetwork
from decompnet.arch import mips
from decompnet.arch.mips_gaps import audit


def words(*values):return struct.pack('>'+str(len(values))+'I',*values)


@pytest.mark.parametrize('platform', ['n64','psx','ps2','psp'])
def test_truncated_calls_delay_slots_and_conditional_fallthrough(platform):
    from decompnet.consoles import get
    c=get(platform)
    for data in [words(0x27bdffe8), words(0x0c000400,0), words(0x03e00008), words(0x10800002,0)]:
        if not c.big_endian:data=b''.join(data[i:i+4][::-1] for i in range(0,len(data),4))
        assert c.boundary_reason(data)
    for data in [words(0x03e00008,0),words(0x1000ffff,0),words(0x08000400,0),words(0x0c000400,0,0x03e00008,0)]:
        if not c.big_endian:data=b''.join(data[i:i+4][::-1] for i in range(0,len(data),4))
        assert c.boundary_reason(data) is None


def test_reachable_early_return_does_not_hide_another_fallthrough_path():
    assert mips.boundary_reason(words(0x10800003,0,0x03e00008,0,0x27bdffe8))
    assert mips.boundary_reason(words(0x03e00008,0,0x27bdffe8)) is None  # unreachable bytes


def fixture(tmp_path):
    net=RomNetwork(tmp_path/'coverage.db',tmp_path)
    cfg={'kind':'rom','platform':'n64','rom':{},'segments':[]}
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES('game','game','mips',1,?,?)",(str(tmp_path),json.dumps(cfg))).lastrowid
        # Empty metadata is the important legacy case: it cannot rely on a prior warning.
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,status) VALUES(?,'cut',?,4096,4,1,?,'asm')",(pid,0x80001000,words(0x27bdffe8)))
    return net,net.project_row('game')


def test_legacy_assembly_is_visible_as_incomplete_without_mutating_status_or_claims(tmp_path):
    net,proj=fixture(tmp_path);before=dict(net.store.one('SELECT * FROM functions'))
    result=code_coverage.snapshot(net,proj)
    assert result['incomplete_assembly']==1 and result['incomplete_entries'][0]['analysis_state']=='boundary_review'
    assert result['queue_empty'] and result['completion_audit_required']
    assert result['raw_bytes'] is None and result['audit_state']=='required'
    assert not result['whole_game_complete']
    assert dict(net.store.one('SELECT * FROM functions'))==before


def test_dismissed_candidates_do_not_clear_incomplete_targets_and_confirmed_code_is_unresolved(tmp_path):
    net,proj=fixture(tmp_path)
    report={'candidates':[dict(rom_offset=4100,vram=0x80001004,gap_start=4100,gap_end=4200,extent_bytes=12)],'gap_bytes':100,'interior_bytes':20}
    saved=code_audit_reviews.record(net,proj['id'],report,bytes(4200));rid=saved['candidates'][0]['review_id']
    code_audit_reviews.review(net,'game',rid,'confirmed_code','Original calls reach this code.')
    result=code_coverage.snapshot(net,proj)
    assert result['unresolved_candidates']==1 and result['audit_state']=='needs_review'
    code_audit_reviews.review(net,'game',rid,'dismissed','Reviewed as data.')
    result=code_coverage.snapshot(net,proj)
    assert result['unresolved_candidates']==0 and result['audit_state']=='needs_review'
    assert (result['raw_bytes'],result['interior_bytes'])==(100,20)
    # Even an acknowledged, empty audit is not whole-game source completion.
    with net.store.tx() as db:db.execute('UPDATE functions SET target=?',(words(0x03e00008,0),))
    assert not code_coverage.snapshot(net,proj)['whole_game_complete']


def test_changed_layout_requires_completion_audit_again_and_limits_remain_visible(tmp_path):
    net,proj=fixture(tmp_path)
    code_audit_reviews.record(net,proj['id'],{'candidates':[],'hint_scan_limit':True},bytes(4200))
    assert code_coverage.snapshot(net,proj)['search_limited']
    with net.store.tx() as db:db.execute('UPDATE functions SET size=8')
    current=code_coverage.snapshot(net,proj)
    assert current['audit_stale'] and current['completion_audit_required']


def test_source_free_fallthrough_seeds_raw_continuation_not_an_automatic_target():
    image=bytearray(0x1040);image[0x1000:0x1004]=words(0x27bdffe8)
    image[0x1004:0x1010]=words(0,0x03e00008,0x27bd0018)
    functions=[dict(name='cut',rom=0x1000,vram=0x80001000,size=4)]
    result=audit(bytes(image),[dict(rom_start=0x1000,rom_end=0x1004,vram=0x80001000)],functions)
    row=next(r for r in result['candidates'] if r.get('incomplete_function')=='cut')
    assert row['rom_offset']==0x1004 and row['incoming'][0]['kind']=='fallthrough'
    assert row['review_only'] and result['boundary_candidates'][0]['function']=='cut'
    assert functions[0]['size']==4


def test_inside_segment_fallthrough_and_hardware_assembly_are_distinguished():
    image=bytearray(0x1040);image[0x1000:0x1010]=words(0x27bdffe8,0,0x03e00008,0x27bd0018)
    segments=[dict(rom_start=0x1000,rom_end=0x1040,vram=0x80001000)]
    fs=[dict(name='cut',rom=0x1000,vram=0x80001000,size=4)]
    result=audit(bytes(image),segments,fs)
    assert result['interior_bytes']==60
    assert result['candidates'][0]['region']=='unowned segment interior'
    fs[0]['handwritten']=True
    assert not audit(bytes(image),segments,fs)['boundary_candidates']


def test_coverage_gui_escapes_evidence_runs_cpu_once_and_exposes_retry():
    import shutil
    import subprocess
    from pathlib import Path
    node=shutil.which('node')
    if node is None:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_code_coverage.js'))],check=True,capture_output=True,text=True,timeout=60)


def test_coverage_polling_reuses_decoded_boundaries_and_invalidates_target_edits(tmp_path, monkeypatch):
    net, proj = fixture(tmp_path)
    calls = []
    original = code_coverage._boundaries
    def checked(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(code_coverage, '_boundaries', checked)
    assert code_coverage.snapshot(net, proj)['incomplete_entries']
    assert code_coverage.snapshot(net, proj)['incomplete_entries']
    assert len(calls) == 1
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=?', (words(0x03e00008, 0),))
    assert not code_coverage.snapshot(net, proj)['incomplete_entries']
    assert len(calls) == 2
