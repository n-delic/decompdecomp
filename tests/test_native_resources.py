"""Invented directory trees and record streams; no game bytes or fixed addresses."""
import copy
import hashlib
import json
import struct

import pytest

from decompnet.app import rom_explorer
from decompnet.consoles import get
from decompnet.consoles.n64 import resources, textures
from decompnet.network import NetworkError
from decompnet.rom import explorer

from .test_native_models import container, mesh
from .test_native_textures import bank, indexed_set, literal2
from .test_rnc import literal_block
from .test_rom_explorer import Log, project  # noqa: F401


def stream(width,count=0,payload=None):
    data=struct.pack('>II',width,count)+(bytes(width*count) if payload is None else payload)
    return data.ljust((len(data)+7)//8*8,b'\0')


def animation():
    return indexed_set([stream(2,2),stream(4,1,struct.pack('>hh',-1,-1)),stream(20,1),
                        stream(0),stream(0),indexed_set([]),stream(20)])


def fixture(pack=lambda x:x):
    def leaf(raw):
        data=pack(raw);return data.ljust((len(data)+7)//8*8,b'\0')
    model,_=mesh();geometry=container(model)
    obj=indexed_set([leaf(indexed_set([bytes(8),stream(4)])),leaf(geometry),indexed_set([leaf(animation())]),leaf(stream(20))])
    collision=indexed_set([stream(n,1) for n in (72,16,16,20)])
    grids=indexed_set([leaf(indexed_set([])),leaf(indexed_set([stream(24,1),stream(48),stream(28)]))])
    level=indexed_set([bytes(24),leaf(collision),leaf(stream(36,1)),leaf(indexed_set([stream(n,1) for n in (4,4,4,4,16)])),grids,leaf(indexed_set([]))])
    music=struct.pack('>17I',68,*([0]*15),96)+bytes(4)
    demo=struct.pack('>H5h',88,120,96,104,112,120)+bytes(4)
    return indexed_set([indexed_set([obj]),leaf(stream(80,1)),stream(2,1,struct.pack('>H',7)),indexed_set([leaf(bank())]),
                        leaf(indexed_set([stream(4),stream(104),stream(140)])),leaf(indexed_set([stream(4),indexed_set([])])),
                        indexed_set([level]),indexed_set([stream(4),stream(4),bytes(8)]),indexed_set([stream(4),stream(20)]),
                        indexed_set([leaf(music),leaf(demo)]),stream(4,2,struct.pack('>II',17,800))])


@pytest.mark.parametrize('pack',[lambda x:x,literal_block,literal2])
def test_relocated_directory_roles_and_metadata_preview(pack):
    image=bytes(37)+fixture(pack)+bytes(11);found=get('n64').asset_regions(image)
    roles={a.get('resource_type') for a in found}
    assert {'animation','collision','object placements','grid bounds','grid section','music sequence','demo recording','directory'}<=roles
    assert next(a for a in found if a.get('resource_type')=='directory')['offset']==37
    for asset in found:
        if asset.get('media')!='metadata':continue
        raw=textures.decode(image[asset['offset']:])[0] if asset.get('compression') else image
        result=get('n64').asset_preview(raw,asset,{})
        assert result['media']=='metadata' and result['title']
    animation_asset=next(a for a in found if a.get('resource_type')=='animation')
    assert animation_asset['resource_fields']['frames']==2
    assert animation_asset['resource_fields']['nodes']==1
    assert next(a for a in found if a.get('resource_type')=='music sequence')['resource_fields']['resource_id']==17
    assert next(a for a in found if a.get('resource_type')=='demo recording')['resource_fields']['resource_id']==800
    assert next(a for a in found if a.get('resource_type')=='geometry')['media']=='model'
    assert next(a for a in found if a.get('resource_type')=='texture bank')['kind']=='image'
    report=explorer.analyse(image,[],[],[],assets=found)
    assert {'animation','collision','object placements','world grid','music sequence','demo'}<={r['kind'] for r in report['regions']}


def test_root_lookalikes_and_cross_count_mismatches_do_not_supply_roles():
    raw=fixture();root=resources.blocks(raw,(0,len(raw)),11)
    for at in (4,48,root[2][0]+4,root[10][0]+4):
        broken=bytearray(raw);struct.pack_into('>I',broken,at,999999)
        found=resources.regions(broken)
        assert not any(a['resource_path'] for a in found)
    assert not resources.regions(bytes.fromhex('0000000b00000038')+bytes(80))


def test_bad_leaf_streams_are_not_semantically_classified():
    raw=bytearray(fixture());found=resources.regions(raw)
    asset=next(a for a in found if a['resource_type']=='animation')
    parts=resources.blocks(raw,(asset['offset'],asset['offset']+asset['size']),7)
    struct.pack_into('>h',raw,parts[1][0]+8,44)
    assert not any(a['resource_type']=='animation' for a in resources.regions(raw))
    for role in ('collision','grid bounds','grid section'):
        asset=next(a for a in found if a['resource_type']==role and not a['resource_fields'].get('empty'))
        data=bytearray(fixture());part=resources.blocks(data,(asset['offset'],asset['offset']+asset['size']))[0]
        struct.pack_into('>I',data,part[0],999)
        assert not any(a['offset']==asset['offset'] and a['resource_type']==role for a in resources.regions(data))


def test_animation_missing_initial_orientation_is_visible_without_inventing_a_pose():
    raw=animation();parts=resources.blocks(raw,(0,len(raw)),7)
    modified=indexed_set([raw[a:b] if i!=2 else stream(20) for i,(a,b) in enumerate(parts)])
    role,info=resources.fields(modified,'animation')
    assert role=='animation' and info['initial_orientation_gap'] is True


def test_standalone_rnc_checksums_and_expanded_code_match():
    stub=bytes.fromhex('27bdfff0afbf000c8fbf000c27bd001003e0000800000000')*32
    packed=literal_block(stub);image=bytes(19)+packed+bytes(8)+stub
    found=resources.regions(image);assert len(found)==1
    assert found[0]['resource_type']=='packed program image'
    assert found[0]['resource_fields']['expanded_image_offset']==19+len(packed)+8
    broken=bytearray(packed);broken[-1]^=1;assert not resources.regions(broken)
    assert resources.regions(literal2(b'opaque bytes'))[0]['resource_type']=='compressed data'


def test_stop_and_decoding_limits(monkeypatch):
    def stop():raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):resources.regions(fixture(),check=stop)
    monkeypatch.setattr(resources,'MAX_EXPANDED',0)
    found=resources.regions(fixture(literal2));assert not any(a.get('compression') for a in found)
    monkeypatch.setattr(resources,'MAX_ROOTS',0)
    assert not any(a['resource_path'] for a in resources.regions(fixture()))


def test_guarded_metadata_api_and_original_image_integrity(project):  # noqa: F811
    net,pid,root=project;image=bytes(128)+fixture(literal2);(root/'game.rom').write_bytes(image)
    cfg=copy.deepcopy(net.cfg(net.project_row('game')));cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid));db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());report=rom_explorer.listing(net,'game')
    row=next(r for r in report['regions'] if r['kind']=='animation')
    request=dict(region=row['id'],image_hash=report['image_hash'],options=dict(format='auto',offset=row['start']))
    result=rom_explorer.asset_preview(net,'game',request)
    assert result['media']=='metadata' and result['fields']['frames']==2 and result['decoded']['codec']=='RNC method 2'
    assert (root/'game.rom').read_bytes()==image
    (root/'game.rom').write_bytes(image[:-1]+b'!')
    with pytest.raises(NetworkError,match='changed'):rom_explorer.asset_preview(net,'game',request)


def test_binary_header_checks_do_not_guess_music_or_demos_from_directory_slot():
    data=struct.pack('>17I',68,*([0]*15),0)+bytes(4)
    assert resources.fields(data,'binary resource')[0]=='binary resource'
    data=struct.pack('>H5h',88,120,96,112,104,120)+bytes(4)
    assert resources.fields(data,'binary resource')[0]=='binary resource'
    raw=fixture();root=resources.blocks(raw,(0,len(raw)),11);broken=bytearray(raw)
    struct.pack_into('>I',broken,root[10][0]+12,17)
    assert not any(a['resource_path'] for a in resources.regions(broken))
