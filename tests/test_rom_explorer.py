"""Homemade images test byte coverage and evidence without commercial game inputs."""
import hashlib
import json
import struct
import threading
import zlib

import pytest

from decompnet.app import rom_explorer
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs
from decompnet.network import NetworkError
from decompnet.rom.explorer import DECODE_BYTES, PAGE_BYTES, analyse


def mapping(name='main',start=0,end=128,vram=0x80000000):
    return dict(name=name,rom_start=start,rom_end=end,vram=vram)


def function(name='reader',offset=0,vram=0x80000000,relocs=()):
    return dict(name=name,rom_offset=offset,vram=vram,size=8,unit='main',relocs=list(relocs))


def test_every_byte_is_covered_once_even_with_overlaps_and_unmapped_gaps():
    image=bytes(PAGE_BYTES*2+13)
    report=analyse(image,[mapping(end=64),mapping('other',32,96)],
                   [function(),function('overlap',4,0x80000004)],
                   [dict(name='label',address=0x80000020,size=16,kind='data')])
    rows=report['regions']
    assert rows[0]['start']==0 and rows[-1]['end']==len(image)
    assert all(a['end']==b['start'] for a,b in zip(rows,rows[1:]))
    assert sum(r['size'] for r in rows)==len(image)==sum(report['totals'].values())
    assert any(len(r['functions'])==2 for r in rows)
    assert any(not r['mappings'] for r in rows)


def test_references_cover_symbol_interiors_and_preserve_overlay_ambiguity():
    segs=[mapping('a',0,64),mapping('b',64,128),mapping('shared',128,192,0x90000000)]
    funcs=[function('local',0,relocs=[(4,5,0x80000024)]),function('outside',128,0x90000000,[(4,5,0x80000024)])]
    report=analyse(bytes(192),segs,funcs,[dict(name='table',address=0x80000020,size=16,kind='data')])
    refs=[(r,ref) for r in report['regions'] for ref in r['references']]
    local=[(r,v) for r,v in refs if v['function']=='local']
    outside=[v for r,v in refs if v['function']=='outside']
    assert len(local)==1 and local[0][1]['offset']==0x24 and local[0][1]['instruction']==0x80000004
    assert local[0][1]['confidence']=='recorded'
    assert len(outside)==2 and all(v['confidence']=='tentative' for v in outside)
    assert all(s['ambiguous'] for r in report['regions'] for s in r['symbols'])


def test_compressed_stream_crosses_page_boundary_and_only_validated_extent_is_counted():
    stream=zlib.compress(b'hello world'*100)
    offset=PAGE_BYTES-1
    image=b'\0'*offset+stream+b'\0'*123
    result=analyse(image,[],[],[])
    rows=[r for r in result['regions'] if r['confidence']=='validated']
    assert rows and sum(r['size'] for r in rows)==len(stream)
    assert rows[0]['start']==offset
    assert rows[-1]['end']==offset+len(stream)
    assert all(r['evidence'][0]['expanded_size']==1100 for r in rows)
    assert all(r['kind']=='compressed' for r in rows)


def test_bombs_and_broken_headers_never_become_validated_assets():
    image=zlib.compress(bytes(DECODE_BYTES+1))+b'Yaz0'+bytes(30)+b'\x78\x9cgarbage'
    result=analyse(image,[],[],[])
    assert not any(r['confidence']=='validated' for r in result['regions'])
    assert any(e['label']=='Yaz0' for r in result['regions'] for e in r['evidence'])
    assert all(r['confidence']!='recorded' for r in result['regions'])


@pytest.mark.parametrize('big_endian',[True,False])
def test_pointer_table_chains_are_sampled_leads_in_both_byte_orders(big_endian):
    image=struct.pack('>8I' if big_endian else '<8I',*([0x80000030]*8))+bytes(96)
    result=analyse(image,[mapping()],[],[],big_endian=big_endian)
    row=result['regions'][0]
    assert row['pointers'][0]['destinations'][0]['rom']==48
    assert all(e['confidence']=='tentative' for e in row['evidence'])


def test_invalid_mapping_and_cooperative_stop():
    with pytest.raises(ValueError,match='segment'):analyse(b'a',[mapping()],[],[])
    def stopped():raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):analyse(bytes(PAGE_BYTES*2),[],[],[],check=stopped)


class Log:
    def __init__(self):self.updates=[]
    def __call__(self,line):pass
    def progress(self,value):self.updates.append(value)
    def check_cancel(self):pass


@pytest.fixture
def project(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    image=bytes(8)+b'hello world\0'+bytes(108)
    (tmp_path/'game.rom').write_bytes(image)
    cfg=dict(kind='rom',platform='n64',rom=dict(file='game.rom',sha1=hashlib.sha1(image).hexdigest()),segments=[mapping()],compiler={'toolchain':'gcc-2.7.2-kmc','flags':[]})
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?)',('game','game','mips',1,str(tmp_path),json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,vram,rom_offset,unit) VALUES(?,?,?,?,?,?,?,?,?)',
                   (pid,'reader',8,2,bytes(8),json.dumps([[4,5,0x80000008]]),0x80000000,0,'main'))
        db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(pid,'message',0x80000008,12,'string'))
    yield net,pid,tmp_path
    if hasattr(net,'autosave'):net.autosave.close()


def test_saved_map_preview_annotations_staleness_and_no_progress_side_effects(project,monkeypatch):
    net,pid,root=project
    monkeypatch.setattr(net.progress,'sync',lambda *a,**k:pytest.fail('Explorer must not synchronize or judge saved work'))
    before=[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    rom_explorer.run(net,'game',Log())
    with net.progress.view():
        report=rom_explorer.listing(net,'game')
        assert report['available'] and not report['stale']
        row=next(r for r in report['regions'] if r['kind']=='strings')
        assert row['used_by']==1 and row['function_refs']==['reader']
        packet=dict(region=row['id'],image_hash=report['image_hash'],name='Greeting',notes='Read by the reader.',kind='3d model')
        rom_explorer.annotate(net,'game',packet)
        assert 'hello world' in rom_explorer.detail(net,'game',row['id'],report['image_hash'])['preview']
        rom_explorer.run(net,'game',Log())
        assert next(r for r in rom_explorer.listing(net,'game')['regions'] if r['id']==row['id'])['annotation']['name']=='Greeting'
        assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['kind']=='3d model'
        # Bookkeeping and compiler changes do not invalidate the byte map.
        proj=net.project_row('game');cfg=dict(net.cfg(proj));cfg['progress_revision']='new';cfg['compiler']={'toolchain':'other'}
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        assert not rom_explorer.listing(net,'game')['stale']
        (root/'game.rom').write_bytes(bytes(128))
        assert rom_explorer.listing(net,'game')['stale']
        with pytest.raises(NetworkError,match='changed'):rom_explorer.annotate(net,'game',packet)
    assert [tuple(r) for r in net.store.all('SELECT * FROM functions')]==before


def test_cancelled_rescan_retains_complete_map_and_notes(project):
    net,pid,root=project
    rom_explorer.run(net,'game',Log())
    with net.progress.view():before=rom_explorer.listing(net,'game')
    log=Log()
    def stop():raise RuntimeError('stopped')
    log.check_cancel=stop
    with pytest.raises(RuntimeError,match='stopped'):rom_explorer.run(net,'game',log)
    with net.progress.view():assert rom_explorer.listing(net,'game')==before


def test_status_is_scoped_and_never_loads_large_report(project,monkeypatch):
    net,pid,root=project;jobs=Jobs()
    jobs.jobs={'a':dict(id='a',kind='rom-explorer',activity_project='other',state='running'),
               'b':dict(id='b',kind='rom-explorer',activity_project='game',state='running')}
    monkeypatch.setattr(rom_explorer,'_read',lambda *a:pytest.fail('status loaded report'))
    with net.progress.view():assert rom_explorer.status(net,'game',jobs)['job']['id']=='b'


def test_routes_guard_start_deduplicate_and_reattach(project,monkeypatch):
    from starlette.testclient import TestClient

    from decompnet.app.server import build

    from .test_app_server import PORT, SECRET, WINDOW
    net,pid,root=project
    started,release=threading.Event(),threading.Event()
    def scan(*args):started.set();release.wait(5);return {'size':128}
    monkeypatch.setattr(rom_explorer,'run',scan)
    with net.progress.view():mcp,manager,security,_=build(net,root/'.decomp',PORT,SECRET)
    monkeypatch.setattr(net.progress,'sync',lambda *a,**kw:pytest.fail('Explorer route synchronized the project'))
    client=TestClient(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),base_url=f'http://127.0.0.1:{PORT}',headers=WINDOW)
    try:
        assert client.get('/api/rom-explorer?slug=game',headers={'User-Agent':'outside'}).status_code==401
        assert client.post('/api/rom-explorer',json={'slug':'game'}).status_code==403
        first=client.post('/api/rom-explorer',json={'slug':'game'},headers={'X-Decomp':'1'})
        assert first.status_code==200,first.text
        assert started.wait(2)
        again=client.post('/api/rom-explorer',json={'slug':'game'},headers={'X-Decomp':'1'})
        assert again.json()['job']==first.json()['job']
        status=client.get('/api/rom-explorer?slug=game&view=status').json()
        assert status['job']['id']==first.json()['job'] and status['job']['state']=='running'
    finally:release.set()


def test_bss_is_runtime_only_and_never_adds_rom_bytes():
    report=analyse(bytes(128),[mapping()],
                   [function(relocs=[(4,5,0x90000004)])],
                   [dict(name='scratch',address=0x90000000,size=32,kind='bss')])
    assert sum(report['totals'].values())==128
    assert report['runtime_symbols'][0]['references']==[dict(function='reader',instruction=0x80000004,target=0x90000004)]
    assert not any(r['symbols'] for r in report['regions'])


def test_flattened_program_hash_is_not_mistaken_for_analysis_hash(project):
    net,pid,root=project
    with net.progress.view():
        proj=net.project_row('game');cfg=dict(net.cfg(proj))
        cfg['rom']=dict(cfg['rom'],program='program.elf',sha1='program-hash',analysis_sha1=hashlib.sha1((root/'game.rom').read_bytes()).hexdigest())
        with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        rom_explorer.run(net,'game',Log())
        report=rom_explorer.listing(net,'game')
        assert report['derived'] and 'not physical-media offsets' in report['note']
        assert report['image_hash']==hashlib.sha256((root/'game.rom').read_bytes()).hexdigest()


def test_hostile_header_density_is_bounded_but_byte_coverage_remains_complete(monkeypatch):
    from decompnet.rom import explorer
    monkeypatch.setattr(explorer,'MAX_FORMAT_CLUES',8)
    report=analyse(b'Yaz0'*100,[],[],[])
    assert report['format_limit'] and 'limit reached' in report['note']
    assert sum(r['size'] for r in report['regions'])==400
    assert len(report['regions'])<20


def test_rejected_and_unverified_compression_headers_do_not_classify_bytes(monkeypatch):
    import decompnet.rom.explorer as module
    bad=b'\x78\x9cgarbage'+b'\x1f\x8b\x08'+bytes(20)
    report=analyse(bad,[],[],[])
    assert not any(row['kind']=='compressed' for row in report['regions'])
    assert any(e['confidence']=='rejected' for row in report['regions'] for e in row['evidence'])
    monkeypatch.setattr(module,'MAX_DECODE_PROBES',0)
    report=analyse(zlib.compress(b'valid but unprobed'),[],[],[])
    assert not any(row['kind']=='compressed' for row in report['regions'])
    assert any('budget' in e['reason'] for row in report['regions'] for e in row['evidence'])


def test_native_resources_do_not_consume_the_generic_signature_budget(monkeypatch):
    import decompnet.rom.explorer as module
    monkeypatch.setattr(module,'MAX_FORMAT_CLUES',1)
    image=bytes(32)+zlib.compress(b'genuine compressed payload')
    asset=dict(offset=0,size=8,kind='data',label='Native record',confidence='validated')
    report=analyse(image,[],[],[],assets=[asset])
    assert any(r['kind']=='compressed' and r['confidence']=='validated' for r in report['regions'])
