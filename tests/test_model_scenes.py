"""Synthetic archives test explicit links, static assembly and split coordinates."""
import io
import json
import struct
import zipfile
from threading import Event
from types import SimpleNamespace

import pytest

from decompnet.app import asset_agents, rom_explorer
from decompnet.consoles.n64 import archived_models, model_links, model_scenery
from decompnet.rom.asset_formats import AssetError

from .test_archived_models import model
from .test_archived_textures import pack
from .test_asset_agents import proposal
from .test_rom_explorer import Log, project  # noqa: F401


def archive(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as stream:
        for name,data in files:stream.writestr(name,data)
    return bytes(64)+output.getvalue()+bytes(512)


def car():
    raw,_=model();raw=bytearray(raw);raw[116:120]=b'body';nodes=int.from_bytes(raw[44:48],'big');node=bytearray(raw[nodes:]);block=bytes(raw[136:nodes]);result=bytearray(raw[:136]);new_nodes=[]
    for index in range(2):
        delta=len(result)-136;copy=bytearray(block)
        table=int.from_bytes(node[104:108],'big')-136
        for at in (table,table+8):struct.pack_into('>I',copy,at,int.from_bytes(copy[at:at+4],'big')+delta)
        result.extend(copy);new=bytearray(node)
        for at in (80,84,104):struct.pack_into('>I',new,at,int.from_bytes(new[at:at+4],'big')+delta)
        matrix=[4096,0,0,0,0,4096,0,0,0,0,4096,0,index*100,0,0,1];struct.pack_into('>16f',new,12,*matrix);new_nodes.append(new)
    struct.pack_into('>IHH',result,44,len(result),2,8);result.extend(b''.join(new_nodes));return bytes(result)


def scenery():
    data=bytearray(136);data[:4]=b'PD3V';struct.pack_into('>I',data,32,10);data[116:120]=b'body'
    for i,t in enumerate((0,14,10,11,9,12,13,4,5,6)):struct.pack_into('>IHH',data,36+i*8,0,0,t)
    struct.pack_into('>IHH',data,36,116,1,0)
    xz=bytearray(struct.pack('>6h',-20,0,20,0,0,20));poly=len(xz);xz.extend(struct.pack('>3H',0,1,2)+bytes([0,0,128,0,0,128])+bytes(2))
    command=len(xz);xz.extend(struct.pack('>IHH',poly,1,26));mesh=len(xz);xz.extend(bytes(24));xz[mesh:mesh+4]=b'Demo';struct.pack_into('>IHHI',xz,mesh+12,0,1,0,command)
    shade=len(xz);xz.extend(bytes([255,128,64]));instance=len(xz);xz.extend(bytes(36));xz[instance:instance+4]=b'Demo';struct.pack_into('>III',xz,instance+16,0,shade,mesh)
    table=len(xz);xz.extend(struct.pack('>IHH',mesh,1,0));instances=len(xz);xz.extend(struct.pack('>IHH',instance,1,0))
    struct.pack_into('>IHH',data,52,table,1,10);struct.pack_into('>IHH',data,60,instances,1,11)
    return bytes(data),bytes(xz),struct.pack('>3h',0,10,20)


def linked_files():
    return [('models/demo_100.v3d',car()),('maps/body.n2d',pack()),('scripts/load.ini','SETDIR models\\\nLOADN3D demo maps\\\n'),('tracks/demo.v3d',scenery()[0]),('tracks/demo.v3s',scenery()[1]),('tracks/demo.v3y',scenery()[2]),('scripts/track.ini','SETDIR tracks\\\nLOADV3D demo.v3d demo.v3s demo.v3y maps\\\n')]


def test_explicit_links_assembly_textures_uv_and_relationships():
    image=archive(linked_files());assets=archived_models.regions(image);assert len(assets)==2
    asset=next(a for a in assets if not a.get('scenery'));assert asset['assembled']
    assert asset['materials'][0]['state']=='linked' and asset['relationships'][0]['role']=='loader script'
    result=archived_models.preview(image,asset,dict(assembly='assembled'))
    assert len(result['parts'])==2 and result['vertices'][4][0]==result['vertices'][0][0]+100
    assert len(result['surfaces'])==6 and result['materials'][0]['image'].startswith('data:image/png;base64,')
    assert result['surfaces'][0]['material']==0 and len(result['surfaces'][0]['uv'])==3
    assert not any('positions' in m or 'records' in m for a in assets for m in a['meshes'])
    with pytest.raises(AssetError,match='Choose one'):archived_models.preview(image,asset,dict(assembly='bad'))


def test_split_geometry_exact_coordinates_and_external_crc():
    image=archive(linked_files());asset=next(a for a in archived_models.regions(image) if a.get('scenery'))
    result=archived_models.preview(image,asset,dict(assembly='assembled'))
    assert result['vertices']==[[-20,0,0],[20,10,0],[0,20,20]]
    assert result['surfaces'][0]['uv']==[[0,0],[1,0],[0,1]]
    assert result['surfaces'][0]['colors']==[[255,255,255,255],[128,128,128,255],[64,64,64,255]]
    broken=bytearray(image);broken[asset['streams'][0]['payload']]^=255
    with pytest.raises(AssetError):archived_models.preview(broken,asset,{})
    assert not any(a.get('scenery') for a in archived_models.regions(broken))


def test_no_basename_guess_ambiguous_paths_and_commands_are_not_executed(tmp_path):
    files=linked_files();image=archive(files[:2]);assert archived_models.regions(image)[0]['materials'][0]['state']=='missing'
    image=archive(files[:3]+[('other/body.n2d',pack()),('scripts/second.ini','SETDIR models\\\nLOADN3D demo other\\\n')]);asset=archived_models.regions(image)[0]
    assert asset['materials'][0]['state']=='ambiguous' and 'image' not in archived_models.preview(image,asset,{})['materials'][0]
    assert model_links.path('../escape')==''
    assert not list(tmp_path.iterdir())
    image=archive(files[:2]+[('scripts/malicious.ini','SETDIR ../models\nLOADN3D demo maps\nEXEC anything')]);assert archived_models.regions(image)[0]['materials'][0]['state']=='missing'


def test_bad_external_tables_indices_and_truncation_are_rejected():
    data,xz,y=scenery()
    for offset,value in ((12,0xffff),(26,0xffffffff),(len(xz)-8,0xffffffff)):
        bad=bytearray(xz);struct.pack_into('>I',bad,offset,value);assert model_scenery.layout(data,bad,y) is None
    assert model_scenery.layout(data,xz,y[:-1]) is None
    assert model_scenery.layout(data,xz[:12],y) is None


def test_malformed_transform_does_not_enable_assembly():
    raw=bytearray(car());nodes=int.from_bytes(raw[44:48],'big');struct.pack_into('>f',raw,nodes+12,float('nan'))
    image=archive([('models/test.v3d',raw)]);asset=archived_models.regions(image)[0];assert not asset['assembled']
    assert archived_models.preview(image,asset,{})['vertices']
    with pytest.raises(AssetError,match='stored assembly'):archived_models.preview(image,asset,dict(assembly='assembled'))


def test_archive_layout_recipe_validated_before_review(project):  # noqa: F811
    import hashlib
    net,pid,root=project;image=archive(linked_files());(root/'game.rom').write_bytes(image)
    with net.progress.view():cfg=json.loads(json.dumps(net.cfg(net.project_row('game'))))
    cfg['rom']['sha1']=hashlib.sha1(image).hexdigest();cfg['segments'][0]['rom_end']=len(image)
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),pid));db.execute('DELETE FROM functions WHERE project_id=?',(pid,))
    rom_explorer.run(net,'game',Log());run=asset_agents.prepare(net,'game',{'mode':'models'});agent,_=net.local_agent('model-parser');session=SimpleNamespace(id='model',label='Model investigator',model='test',state='working',stop=Event(),why='',slug='game',options={'task':'assets','asset_run':run})
    asset_agents.bind(net,session,agent)
    while True:
        packet=json.loads(asset_agents.claim(net,agent,'game'));assert not packet.get('done')
        if packet['region'].get('detected_asset',{}).get('layout')=='archived-pd3v-model':break
        asset_agents.defer(net,agent,'game',packet['region']['id'],packet['claim_token'],'Inspect geometry','No model record here')
    row=packet['region'];raw=car();vertex=int.from_bytes(raw[int.from_bytes(raw[44:48],'big')+80:int.from_bytes(raw[44:48],'big')+84],'big')
    recipe=dict(kind='raw-model',options=dict(source='archive',offset=vertex),layout=dict(vertex_format='s16be',stride=6,count=4,index_format='none',topology='points'))
    p=proposal(packet);p.update(kind='3d model',parser_recipe=recipe)
    result=json.loads(asset_agents.propose(net,agent,'game',row['id'],packet['claim_token'],p));review=asset_agents.review(net,'game',result['proposal'])
    assert review['body']['parser_recipe']==recipe
    assert not rom_explorer.detail(net,'game',row['id'],packet['region']['image_hash'] if 'image_hash' in row else rom_explorer.listing(net,'game')['image_hash'])['annotation'].get('model_settings')

    asset_agents.review(net,'game',result['proposal'],'accept',annotation_version=review['annotation_version'])
    saved=rom_explorer.detail(net,'game',row['id'],review['image_hash'])['annotation']
    assert saved['preview_settings']['source']=='archive' and saved['model_settings']['count']==4
