"""Use a verified retained original, never a flattened analysis image or outside file."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from decompnet.app.reimport_source import stored
from decompnet.network import NetworkError


def project(tmp_path,monkeypatch,*,fork=False):
    image=tmp_path/'original.bin';image.write_bytes(b'owned original')
    sha=hashlib.sha1(image.read_bytes()).hexdigest()
    cfg={'kind':'fork' if fork else 'rom','platform':'n64',
         'rom':{'program':image.name,'file':'analysis.bin','sha1':sha}}
    (tmp_path/'analysis.bin').write_bytes(b'flattened analysis')
    net=SimpleNamespace(project_row=lambda _: {'root':str(tmp_path),'config':json.dumps(cfg)})
    net.forks=SimpleNamespace(get=lambda _: (None,SimpleNamespace(manifest={'game':{'path':image.name,'sha1':sha}}),{}))
    monkeypatch.setattr('decompnet.app.reimport_source.consoles.identify',lambda _:SimpleNamespace(id='n64'))
    return net,cfg,image


@pytest.mark.parametrize('fork',[False,True])
def test_the_retained_original_is_reused_in_both_project_modes(tmp_path,monkeypatch,fork):
    net,_,image=project(tmp_path,monkeypatch,fork=fork)
    assert stored(net,'game')==image


@pytest.mark.parametrize('case',['missing','changed','escape','symlink','format','no-identity'])
def test_invalid_originals_require_selection_without_using_the_analysis_image(tmp_path,monkeypatch,case):
    net,cfg,image=project(tmp_path,monkeypatch)
    if case=='missing':image.unlink()
    if case=='changed':image.write_bytes(b'changed')
    if case in ('escape','symlink'):
        outside=tmp_path.parent/(tmp_path.name+'-outside.bin');outside.write_bytes(image.read_bytes())
        if case=='escape':cfg['rom']['program']=str(outside)
        else:image.unlink();image.symlink_to(outside)
    if case=='format':monkeypatch.setattr('decompnet.app.reimport_source.consoles.identify',lambda _:SimpleNamespace(id='different'))
    if case=='no-identity':cfg['rom'].pop('sha1')
    with pytest.raises(NetworkError,match='[Cc]hoose'):stored(net,'game')


def test_older_unflattened_projects_can_use_the_recorded_rom_file(tmp_path,monkeypatch):
    net,cfg,image=project(tmp_path,monkeypatch)
    cfg['rom'].pop('program');cfg['rom']['file']=image.name
    assert stored(net,'game')==image
