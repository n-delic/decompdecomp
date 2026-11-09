"""Reviewed extensions preserve frozen prefixes, matching gates, and raw export coverage."""
import hashlib
import json

import pytest

from decompnet.app import reviewed_code
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from decompnet.rom.export import ExportError, _export_segments
from decompnet.rom.reviewed import extend_config


def config():return {'segments':[{'type':'code','name':'text','start':16,'vram':0x80000000,'subsegments':[[16,'c','text']]},{'type':'bin','start':24},[48]]}


def test_reviewed_extension_reduces_raw_tail_and_keeps_parent_mapping():
    before=config();after=extend_config(before,[{'vram':0x80000000,'size':24}])
    assert before['segments'][1]['start']==24
    assert after['segments'][1]['start']==40
    assert _export_segments(before,[{'vram':0x80000000,'size':24}])==after
    assert extend_config(before,[{'vram':0x80000000,'size':32}])['segments'][-1]==[48]


def test_reviewed_extension_refuses_to_cross_another_parent_or_consume_data():
    for kind,end in [('bin',52),('data',32),('code',32)]:
        c=config();c['segments'][1]['type']=kind
        with pytest.raises(ExportError):extend_config(c,[{'vram':0x80000000,'size':end-16}])


def fixture(tmp_path):
    net=RomNetwork(tmp_path/'review.db',tmp_path)
    image=b'\x00'*16+bytes.fromhex('27bdffe803e000080000000000000000')+b'\x00'*32
    cfg={'kind':'rom','platform':'n64','rom':{'sha1':hashlib.sha1(image).hexdigest()},'segments':[]}
    with net.store.tx() as db:
        pid=db.execute("INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES('game','game','mips',1,?,?)",(str(tmp_path),json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status,unit) VALUES(?,'cut',?,16,4,?,'asm','text')",(pid,0x80000000,image[16:20]))
    return net,net.project_row('game'),image


def test_plan_needs_evidence_preserves_prefix_and_rejects_matches_overlaps_claims(tmp_path):
    net,proj,image=fixture(tmp_path);change={'function':'cut','end':0x8000000c,'reason':'Reviewed original branch flow and delay slot.'}
    assert reviewed_code._plans(net,proj,[change],image)[0]['size']==12
    for bad in [{**change,'reason':''},{**change,'end':0x80000001},{**change,'end':0x80020000}]:
        with pytest.raises(NetworkError):reviewed_code._plans(net,proj,[bad],image)
    with pytest.raises(NetworkError,match='prefix'):reviewed_code._plans(net,proj,[change],bytes(len(image)))
    with net.store.tx() as db:db.execute("UPDATE functions SET status='matched'")
    with pytest.raises(NetworkError,match='matched'):reviewed_code._plans(net,proj,[change],image)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm'")
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,target,status) VALUES(?,'next',?,24,4,?,'matched')",(proj['id'],0x80000008,image[24:28]))
    with pytest.raises(NetworkError,match='overlaps'):reviewed_code._plans(net,proj,[change],image)


def test_staging_failure_does_not_change_any_live_rows(tmp_path,monkeypatch):
    import yaml

    from decompnet.rom.split import Segment
    net,proj,image=fixture(tmp_path)
    (tmp_path/'reviewed.yaml').write_text(yaml.safe_dump(config()))
    net._rom=lambda p:(image,[Segment('text',16,24,0x80000000)])
    def fail(*args):raise NetworkError('staged extent was not retained')
    monkeypatch.setattr(reviewed_code,'_stage',fail)
    before=dict(net._function('cut','game'))
    with pytest.raises(NetworkError,match='staged extent'):
        reviewed_code.run(net,'game',[{'function':'cut','end':0x8000000c,'reason':'Original complete return reviewed.'}])
    assert dict(net._function('cut','game'))==before


def test_review_ranges_do_not_silently_expand_the_audit_scope():
    from decompnet.arch.mips_gaps import audit
    image=bytearray(0x1100);image[0x1000:0x1008]=bytes.fromhex('03e0000800000000')
    functions=[dict(name='known',rom=0x1000,size=8,vram=0x80001000)]
    result=audit(bytes(image),[dict(rom_start=0x1000,rom_end=0x1010,vram=0x80001000)],functions,review_ranges=[(0x1040,0x1080)])
    assert result['partial'] and result['review_ranges']==[(0x1040,0x1080)]
    assert all(0x1040<=r['rom_offset']<0x1080 for r in result['candidates'])
    with pytest.raises(ValueError):audit(bytes(image),[],[],review_ranges=[(0,3)])


def test_hidden_leaf_in_bounded_interval_is_review_only_and_requires_a_real_return():
    import struct

    from decompnet.arch.mips_gaps import audit
    image=bytearray(0x1100);image[0x1000:0x1020]=struct.pack('>8I',0x03e00008,0,0x00851021,0x03e00008,0x24420001,0,0x03e00008,0)
    fs=[dict(name='before',rom=0x1000,size=8,vram=0x80001000),dict(name='after',rom=0x1018,size=8,vram=0x80001018)]
    result=audit(bytes(image),[dict(rom_start=0x1000,rom_end=0x1020,vram=0x80001000)],fs)
    leaf=next(r for r in result['candidates'] if r['rom_offset']==0x1008)
    assert leaf['review_only'] and leaf['evidence']=='closed leaf in a bounded unowned interval'
    image[0x100c:0x1010]=bytes(4)
    assert not [r for r in audit(bytes(image),[dict(rom_start=0x1000,rom_end=0x1020,vram=0x80001000)],fs)['candidates'] if r['rom_offset']==0x1008]


def test_success_reopens_full_original_target_without_claiming_a_match(tmp_path,monkeypatch):
    from contextlib import contextmanager

    import yaml

    from decompnet.rom.split import AsmLine, RomFunction, Segment
    net,proj,image=fixture(tmp_path);(tmp_path/'reviewed.yaml').write_text(yaml.safe_dump(config()))
    net._rom=lambda p:(image,[Segment('text',16,24,0x80000000)])
    def stage(net,proj,plans,image,config,log):
        for p in plans:
            p['split']=RomFunction(p['name'],p['vram'],p['rom'],'text',[
                AsmLine(p['rom']+i,p['vram']+i,int.from_bytes(image[p['rom']+i:p['rom']+i+4],'big'),'nop','') for i in range(0,p['size'],4)],tmp_path/'target.s')
        return {}
    monkeypatch.setattr(reviewed_code,'_stage',stage)
    calls=[]
    @contextmanager
    def rebuilding(slug):
        calls.append(slug);yield
    monkeypatch.setattr(net.progress,'rebuilding',rebuilding)
    result=reviewed_code.run(net,'game',[{'function':'cut','end':0x8000000c,'reason':'Original extent and return reviewed.'}])
    fn=net._function('cut','game')
    assert calls==['game'] and result['recovered'][0]['size']==12
    assert fn['target']==image[16:28] and fn['status']=='open' and fn['best_score'] is None
    assert json.loads(fn['meta'])['reviewed_extents'][0]['old_size']==4
    assert net.cfg(net.project_row('game'))['segments'][0]['rom_end']==28


def test_partial_scan_cannot_replace_completion_evidence(tmp_path):
    from decompnet.app.code_audit_reviews import record
    net,proj,image=fixture(tmp_path)
    with pytest.raises(NetworkError,match='partial range'):
        record(net,proj['id'],{'partial':True,'candidates':[]},image)


def test_expanded_image_and_original_cartridge_have_separate_hashes(tmp_path,monkeypatch):
    import yaml

    net,proj,image=fixture(tmp_path)
    original=b'original cartridge'
    (tmp_path/'rom.z64').write_bytes(original)
    cfg=json.loads(proj['config'])
    cfg['rom'].update(program='rom.z64',sha1=hashlib.sha1(original).hexdigest())
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    split_config=config();split_config['sha1']=hashlib.sha1(image).hexdigest()
    (tmp_path/'reviewed.yaml').write_text(yaml.safe_dump(split_config))
    net._rom=lambda p:(image,[])
    def staged(*args):raise NetworkError('reached staging')
    monkeypatch.setattr(reviewed_code,'_stage',staged)
    change=[{'function':'cut','end':0x8000000c,'reason':'Reviewed complete original return.'}]
    with pytest.raises(NetworkError,match='reached staging'):reviewed_code.run(net,'game',change)
    (tmp_path/'rom.z64').write_bytes(b'changed')
    with pytest.raises(NetworkError,match='original cartridge'):reviewed_code.run(net,'game',change)
    split_config['sha1']='0'*40
    (tmp_path/'reviewed.yaml').write_text(yaml.safe_dump(split_config))
    with pytest.raises(NetworkError,match='analysis image'):reviewed_code.run(net,'game',change)
