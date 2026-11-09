"""Homemade B1 graphs and predictor vectors; no commercial audio or source fixtures."""
import base64
import copy
import hashlib
import io
import json
import struct
import wave

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles.n64 import audio
from decompnet.network import NetworkError
from decompnet.rom import explorer
from decompnet.rom.asset_formats import AssetError

from .test_rom_assets import addiu, jal, lui, words
from .test_rom_explorer import Log, project  # noqa: F401


def fixture(*, raw=False, predictors=1):
    ctl=bytearray((320+8+32*predictors+15)//16*16)
    ctl[:8]=b'B1'+struct.pack('>HI',1,32)
    struct.pack_into('>HBBIII',ctl,32,1,0,0,22050,0,64)
    struct.pack_into('>BBBB',ctl,64,127,64,0,0)
    struct.pack_into('>HII',ctl,78,2,128,144)
    for sound,envelope,keymap,wavetable in [(128,192,224,256),(144,208,232,280)]:
        struct.pack_into('>IIIBBBx',ctl,sound,envelope,keymap,wavetable,64,127,0)
        struct.pack_into('>iiiBBxx',ctl,envelope,0,-1,16666,127,127)
        ctl[keymap:keymap+6]=bytes([0,127,0,127,60,0])
    length=32 if raw else 18
    for at,base in [(256,0),(280,40)]:struct.pack_into('>IIBBxxII',ctl,at,base,length,int(raw),0,0,0 if raw else 320)
    struct.pack_into('>II'+str(predictors*16)+'h',ctl,320,2,predictors,*([0]*(predictors*16)))
    # The table starts at the next 16-byte boundary after the predictor book.
    table=bytearray(72)
    for offset,value in [(0,1),(40,2)]:
        payload=struct.pack('>16h',*([value*1000,-value*1000]*8)) if raw else bytes([0])+bytes([value*17])*8
        if not raw:payload*=2
        table[offset:offset+len(payload)]=payload
    image=bytearray(128)+ctl+table
    base=128+len(ctl)
    code=words(lui(5,base),addiu(5,5,base),jal(0x80000040),0)
    image[:len(code)]=code
    functions=[dict(name='audio_loader',vram=0x80000000,rom_offset=0,size=len(code),relocs=[]),
               dict(name='alBnkfNew',vram=0x80000040,rom_offset=64,size=4,relocs=[])]
    return bytes(image),dict(functions=functions),base


def wav(result):
    with wave.open(io.BytesIO(base64.b64decode(result['data_url'].split(',')[1])),'rb') as stream:
        return stream.getframerate(),stream.readframes(stream.getnframes())


@pytest.mark.parametrize('raw',[False,True])
def test_named_loader_arguments_and_complete_graphs_locate_samples(raw):
    image,context,base=fixture(raw=raw)
    found=audio.regions(image,context=context)
    assert len(found)==3 and all(asset['kind']=='audio' and asset['confidence']=='validated' for asset in found)
    bank=found[0];assert len(bank['samples'])==2 and bank['table_offset']==base
    assert bank['loader_refs'][0]['function']=='audio_loader'
    for index in range(2):
        result=audio.preview(image,bank,dict(sample=index));rate,pcm=wav(result)
        expected=([1000*(index+1),-1000*(index+1)]*8) if raw else [index+1]*32
        assert rate==22050 and list(struct.unpack('<'+str(len(expected))+'h',pcm))==expected
    report=explorer.analyse(image,[],[],[],assets=found)
    assert any(row['kind']=='audio' and row['detected_asset'] for row in report['regions'])
    assert not any(row['kind']=='image' for row in report['regions'])


def test_adjacency_is_explicitly_tentative_and_raw_pcm_needs_a_named_base():
    image,_,_=fixture();found=audio.regions(image)
    assert found[0]['confidence']=='validated' and found[0]['table_confidence']=='tentative'
    assert all(row['confidence']=='tentative' for row in found[1:])
    image,_,_=fixture(raw=True);found=audio.regions(image)
    assert len(found)==1 and found[0]['audio_kind']=='unresolved bank'
    with pytest.raises(AssetError,match='unresolved'):audio.preview(image,found[0],{})


@pytest.mark.parametrize('offset,payload',[(128+4,b'\xff'*4),(128+34,b'\1'),(128+265,b'\1'),
    (128+320,struct.pack('>I',3)),(128+196,struct.pack('>i',-2)),(128+224,bytes([127,0]))])
def test_bad_pointers_flags_books_envelopes_and_key_maps_are_rejected(offset,payload):
    image,context,_=fixture();broken=bytearray(image);broken[offset:offset+len(payload)]=payload
    assert not audio.regions(broken,context=context)


def test_missing_predictors_and_truncated_sample_tables_never_classify_sample_bytes():
    image,context,base=fixture();broken=bytearray(image);broken[base]=1
    found=audio.regions(broken,context=context)
    assert len(found)==1 and found[0]['audio_kind']=='unresolved bank'
    assert len(audio.regions(image[:-32],context=context))==1
    with pytest.raises(AssetError,match='predictor'):audio.vadpcm(bytes([1])+bytes(8),[0]*16,1,16)


def test_adpcm_vectors_preserve_signed_nibbles_feedback_and_clipping():
    raw=bytes([0])+bytes.fromhex('718f000000000000')
    out=struct.unpack('<16h',audio.vadpcm(raw,[0]*16,1,16))
    assert list(out[:4])==[7,1,-8,-1]
    # A constant Q11 second-history coefficient gives a simple cumulative recurrence.
    book=[0]*8+[2048]*8
    assert struct.unpack('<16h',audio.vadpcm(bytes([0])+bytes([0x11])*8,book,1,16))==tuple(range(1,17))
    assert struct.unpack('<32h',audio.vadpcm((bytes([0])+bytes([0x11])*8)*2,book,1,32))==tuple(range(1,33))
    clipped=struct.unpack('<16h',audio.vadpcm(bytes([0xf0])+bytes([0x78])*8,[0]*16,1,16))
    assert clipped==(32767,-32768)*8
    changed=bytes([0x01])+bytes([0x11])*8
    assert struct.unpack('<16h',audio.vadpcm(changed,[2048]*16+[0]*16,2,16))==(1,)*16


def test_ambiguous_named_tables_are_not_chosen(monkeypatch):
    image,context,base=fixture();image+=bytes(8)+image[base:]
    second=len(image)-72
    monkeypatch.setattr(audio,'loader_tables',lambda *a,**k:{base:[],second:[]})
    found=audio.regions(image,context=context)
    assert len(found)==1 and found[0]['audio_kind']=='unresolved bank'


def test_header_budget_stop_and_false_magic_are_bounded(monkeypatch):
    image,context,_=fixture()
    monkeypatch.setattr(audio,'MAX_HEADER_CHECKS',1)
    assert len(audio.regions(image,context=context))==1
    def stop():raise RuntimeError('stop')
    with pytest.raises(RuntimeError,match='stop'):audio.regions(image,context=context,check=stop)
    monkeypatch.setattr(audio,'MAX_HEADER_CHECKS',100)
    prefix=b'B1random bytes'*200
    # Arbitrary B1 pairs do not consume the structural candidate budget.
    assert audio.regions(prefix+image)


def test_preview_indices_and_original_image_are_guarded(project):  # noqa: F811
    net,pid,root=project;image,context,base=fixture(predictors=3)
    image=bytearray(image);image[base:base+4]=b'RNC\x01';image=bytes(image)
    (root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
        db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
        for f in context['functions']:db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,unit,target,n_insns,relocs) VALUES(?,?,?,?,?,?,?,?,?)',
            (pid,f['name'],f['vram'],f['rom_offset'],f['size'],'main',image[f['rom_offset']:f['rom_offset']+f['size']],f['size']//4,'[]'))
    before=(root/'game.rom').read_bytes()
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if (r['detected_asset'] or {}).get('samples') and len(r['detected_asset']['samples'])==2)
    assert row['kind']=='audio' and 'audio_loader' in row['traced_function_refs']
    detail=rom_explorer.detail(net,'game',row['id'],report['image_hash'])
    assert any(item['function']=='audio_loader' and 'sample-table argument' in item['roles'] for item in detail['usage'])
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',sample=1))
    first=rom_explorer.asset_preview(net,'game',{**request,'options':dict(format='auto',sample=0)})
    assert first['media']=='audio' and 'decoded' not in first
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['media']=='audio' and result['details']['sample']==1 and (root/'game.rom').read_bytes()==before
    with pytest.raises(NetworkError,match='Sample'):rom_explorer.asset_preview(net,'game',{**request,'options':dict(format='auto',sample=2)})
    rom_explorer.annotate(net,'game',dict(region=row['id'],image_hash=report['image_hash'],name='Sample bank',notes='Synthetic data',preview_settings=result['settings']))
    assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['preview_settings']['sample']==1
    (root/'game.rom').write_bytes(bytes(len(image)))
    with pytest.raises(NetworkError):rom_explorer.asset_preview(net,'game',request)


def test_even_byte_padding_is_not_decoded_and_nonzero_padding_is_rejected():
    image,context,base=fixture();image=bytearray(image)
    struct.pack_into('>I',image,128+256+4,10)
    image[base:base+10]=bytes([0])+bytes([0x11])*8+bytes(1)
    found=audio.regions(image,context=context)
    _,pcm=wav(audio.preview(image,found[0],{}))
    assert struct.unpack('<16h',pcm)==(1,)*16
    image[base+9]=1
    assert audio.regions(image,context=context)[0]['audio_kind']=='unresolved bank'


def test_false_bank_headers_do_not_exhaust_the_graph_candidate_budget():
    image,_,_=fixture()
    found=audio.regions((b'B1\x00\x01'+bytes(4))*500+image)
    assert len(found)==3


def test_long_native_samples_preview_a_bounded_prefix_without_looping(monkeypatch):
    image,context,base=fixture();image=bytearray(image[:base])+bytearray(40000)
    raw=(bytes([0])+bytes([0x11])*8)*2757+bytes(1)
    image[base:base+len(raw)]=raw
    struct.pack_into('>I',image,128+256+4,len(raw))
    struct.pack_into('>I',image,128+280,32000)
    image[base+32000:base+32018]=(bytes([0])+bytes([0x22])*8)*2
    bank=audio.regions(image,context=context)[0]
    monkeypatch.setattr(audio,'MAX_SECONDS',1)
    result=audio.preview(image,bank,{})
    rate,pcm=wav(result)
    assert len(pcm)==rate*2 and result['details']['seconds']==1 and result['details']['truncated']
    assert result['details']['original_samples']>rate


def test_changed_global_pointer_invalidates_saved_loader_argument_evidence(project):  # noqa: F811
    net,pid,_=project
    rom_explorer.run(net,'game',Log())
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['gp']=0x80001000
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
    assert rom_explorer.listing(net,'game')['stale']


@pytest.mark.parametrize('raw',[False,True])
def test_loop_endpoints_cannot_exceed_the_declared_sample_count(raw):
    image,_,_=fixture(raw=raw);image=bytearray(image)
    struct.pack_into('>I',image,128+256+12,368)
    struct.pack_into('>III',image,128+368,0,1000,1)
    with pytest.raises(audio.BankError,match='Loop'):audio.bank(image,128)
