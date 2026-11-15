"""Pointer bank fixtures contain homemade predictor books and waveform frames."""
import base64
import hashlib
import io
import json
import struct
import wave

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles import get
from decompnet.consoles.n64 import ptr_audio
from decompnet.rom.asset_formats import AssetError

from .test_rom_explorer import Log, project  # noqa: F401


def fixture(*,frames=2,pcm=False,loop=False):
    control=bytearray(256);control[:16]=ptr_audio.MAGIC;control[20:32]=b'synthetic\0\0\0'
    struct.pack_into('>4I',control,32,2,232,236,224)
    struct.pack_into('>2I',control,224,48,112);control[232:234]=bytes([48,244])
    raw=(b'\0\1\xff\xfe'*4) if pcm else bytes([0,0x12,0x34,0x56,0x78,0x9a,0xbc,0xde,0xf0])*frames
    second=(16+len(raw)+15)//16*16
    for at,base,book in [(48,16,72),(112,second,136)]:
        struct.pack_into('>II',control,at,base,len(raw));control[at+8]=1 if pcm else 0
        struct.pack_into('>II',control,at+12,176 if loop else 0,0 if pcm else book)
        if not pcm:struct.pack_into('>II',control,book,2,1)
    if loop:struct.pack_into('>III',control,176,0,8 if pcm else frames*16,0xffffffff)
    table=ptr_audio.WAVES+raw+bytes(second-16-len(raw))+raw
    return bytes(64)+control+table


def wav(result):
    with wave.open(io.BytesIO(base64.b64decode(result['data_url'].split(',')[1])),'rb') as stream:return stream.getframerate(),stream.getnframes(),stream.readframes(stream.getnframes())


@pytest.mark.parametrize('pcm',[False,True])
def test_pointer_graph_and_unique_marked_table_supply_playable_samples(pcm):
    image=fixture(pcm=pcm,loop=True);assets=ptr_audio.regions(image)
    assert len(assets)==3 and assets[0]['confidence']=='validated'
    assert assets[0]['table_offset']==320 and len(assets[0]['samples'])==2
    assert all(a['confidence']=='tentative' for a in assets[1:])
    assert assets[0]['samples'][1]['base_note_byte']==244
    result=get('n64').asset_preview(image,assets[0],{'sample':1,'rate':11025})
    rate,count,raw=wav(result)
    assert rate==11025 and count==assets[0]['samples'][1]['samples'] and raw
    assert result['settings']['sample']==1 and result['settings']['rate']==11025
    assert 'not stored' in result['details']['rate_basis']
    if pcm:assert raw[:4]==b'\1\0\xfe\xff'
    else:assert struct.unpack_from('<h',raw)[0]==1


def test_ambiguous_missing_and_invalid_wave_tables_remain_unplayable():
    image=fixture();table=image[320:]
    for data in [image[:320],image+bytes(16)+table,image[:336]+b'\x0f'+image[337:]]:
        assets=ptr_audio.regions(data)
        assert len(assets)==1 and assets[0]['samples']==[] and assets[0]['table_confidence']=='unresolved'
        with pytest.raises(AssetError,match='no uniquely fitting'):ptr_audio.preview(data,assets[0],{})


@pytest.mark.parametrize('offset,value',[(16,0x80000000),(32,999999),(36,0xfffffffc),(44,0xfffffffc),(72,3),(76,9),(48+16,0xfffffffc)])
def test_invalid_relocated_graphs_and_books_are_rejected(offset,value):
    data=bytearray(fixture());struct.pack_into('>I',data,64+offset,value)
    assert ptr_audio.regions(data)==[]


def test_bad_flags_loops_tuning_and_sample_bounds_are_not_classified():
    for offset,value in [(64+48+9,1),(64+236+1,1)]:
        data=bytearray(fixture());data[offset]=value;assert ptr_audio.regions(data)==[]
    data=bytearray(fixture(loop=True));struct.pack_into('>I',data,64+176+4,99999)
    assert ptr_audio.regions(data)==[]
    data=bytearray(fixture());struct.pack_into('>I',data,64+48,0xfffffff0)
    assets=ptr_audio.regions(data);assert len(assets)==1 and not assets[0]['samples']


def test_budget_cancellation_and_preview_limits(monkeypatch):
    def stop():raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError,match='Stopped'):ptr_audio.regions(fixture(),check=stop)
    monkeypatch.setattr(ptr_audio,'MAX_FRAME_CHECKS',1)
    assert not ptr_audio.regions(fixture())[0]['samples']
    monkeypatch.setattr(ptr_audio,'MAX_FRAME_CHECKS',100000)
    image=fixture(frames=2000);asset=ptr_audio.regions(image)[0]
    result=ptr_audio.preview(image,asset,{'rate':1000})
    assert result['details']['truncated'] and wav(result)[1]==30000
    for options in [{'sample':2},{'rate':999},{'rate':192001}]:
        with pytest.raises(AssetError):ptr_audio.preview(image,asset,options)


def test_shared_saved_map_preview_settings_and_matching_are_preserved(project):  # noqa: F811
    net,pid,root=project;image=fixture();(root/'game.rom').write_bytes(image)
    with net.progress.view():cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))))
    cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid))
    before=[tuple(r) for r in net.store.all('SELECT * FROM functions')]
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if (r.get('detected_asset') or {}).get('audio_kind')=='libmus sample bank' and len(r['detected_asset']['samples'])==2)
    assert sum(r['size'] for r in report['regions'])==len(image)
    result=rom_explorer.asset_preview(net,'game',dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',sample=1,rate=16000)))
    assert wav(result)[0]==16000
    rom_explorer.annotate(net,'game',dict(region=row['id'],image_hash=report['image_hash'],name='Synthetic bank',preview_settings=result['settings']))
    assert rom_explorer.detail(net,'game',row['id'],report['image_hash'])['annotation']['preview_settings']['rate']==16000
    assert before==[tuple(r) for r in net.store.all('SELECT * FROM functions')] and not net.store.all('SELECT * FROM attempts')


def sequence():
    # Two channel-data pointers, two volume pointers, two bend pointers and one envelope byte.
    return struct.pack('>6I',2,24,32,40,48,48)+struct.pack('>6I',49,0,0,0,0,0)+b'\0\x80'


def test_archived_sequence_candidates_are_not_presented_as_waveforms():
    from .test_archived_textures import archive
    image=archive(sequence(),name='audio/example.bin');assets=ptr_audio.sequences(image)
    assert len(assets)==1 and assets[0]['confidence']=='tentative'
    assert assets[0]['sequence_channels']==2 and assets[0]['active_channels']==1 and not assets[0]['samples']
    with pytest.raises(AssetError,match='not a waveform'):get('n64').asset_preview(image,assets[0],{})
    assert ptr_audio.sequence_layout(sequence()[:40]) is None
    bad=bytearray(sequence());struct.pack_into('>I',bad,24,999999)
    assert ptr_audio.sequence_layout(bad) is None


@pytest.mark.parametrize('platform',['n64','psx','ps2','psp'])
def test_generic_wave_metadata_and_preview_reach_each_rom_console(project,platform):  # noqa: F811
    from decompnet.rom.asset_formats import pcm
    net,pid,root=project
    payload,_=pcm(bytes(320),dict(format='pcm-s16le',channels=1,rate=16000,length=320))
    image=bytes(64)+payload+bytes(64);(root/'game.rom').write_bytes(image)
    with net.progress.view():cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))))
    cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['platform']=platform;cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:db.execute('UPDATE projects SET config=?,big_endian=? WHERE id=?',(json.dumps(cfg),int(get(platform).big_endian),pid))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if any(e.get('label')=='RIFF WAVE' for e in r['evidence']))
    clue=next(e for e in row['evidence'] if e.get('label')=='RIFF WAVE')
    assert clue['rate']==16000 and clue['seconds']==.01
    preview=rom_explorer.asset_preview(net,'game',dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',offset=64)))
    assert preview['media']=='audio' and wav(preview)[0]==16000
