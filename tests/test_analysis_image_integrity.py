"""Original media and derived analysis have independent recorded identities."""
import hashlib
from types import SimpleNamespace

import pytest
import yaml

from decompnet.app import analysis_image, rom_gap_audit
from decompnet.network import NetworkError


def sha(data):
    return hashlib.sha1(data).hexdigest()


def project(tmp_path, *, derived=True, pinned=True):
    original, image = b'original program', bytes(64)
    (tmp_path/'original.bin').write_bytes(original)
    name = 'analysis.bin' if derived else 'original.bin'
    if derived:(tmp_path/name).write_bytes(image)
    else:image = original
    cfg = {'platform':'n64','big_endian':True,'rom':{
        'program':'original.bin','file':name,'sha1':sha(original)},'segments':[]}
    if pinned:cfg['rom']['analysis_sha1'] = sha(image)
    return {'root':str(tmp_path),'id':1,'arch':'mips','big_endian':True}, cfg, image


def test_pinned_derived_analysis_is_not_compared_with_original_hash(tmp_path):
    proj,cfg,image = project(tmp_path)
    assert cfg['rom']['sha1'] != cfg['rom']['analysis_sha1']
    analysis_image.verify(image,proj,cfg)
    with pytest.raises(NetworkError,match='differs'):
        analysis_image.verify(image+b'changed',proj,cfg)
    # A recorded analysis identity must never fall back to a matching original.
    with pytest.raises(NetworkError,match='differs'):
        analysis_image.verify(b'original program',proj,cfg)


def test_legacy_original_image_keeps_original_hash_check(tmp_path):
    proj,cfg,image = project(tmp_path,derived=False,pinned=False)
    analysis_image.verify(image,proj,cfg)
    with pytest.raises(NetworkError,match='differs'):
        analysis_image.verify(image+b'changed',proj,cfg)


def split_config(tmp_path, image):
    (tmp_path/'split.yaml').write_text(yaml.safe_dump({'sha1':sha(image),'options':{
        'base_path':'.','target_path':'analysis.bin'}}))


def test_legacy_derived_image_uses_path_bound_split_hash_and_verified_original(tmp_path):
    proj,cfg,image = project(tmp_path,pinned=False)
    split_config(tmp_path,image)
    analysis_image.verify(image,proj,cfg)
    with pytest.raises(NetworkError,match='differs'):
        analysis_image.verify(image+b'changed',proj,cfg)
    (tmp_path/'original.bin').write_bytes(b'changed original')
    with pytest.raises(NetworkError,match='original program differs'):
        analysis_image.verify(image,proj,cfg)


@pytest.mark.parametrize('case',['missing','two','target','symlink','outside','invalid','malformed','large'])
def test_legacy_derived_image_cannot_adopt_unproven_current_bytes(tmp_path,case):
    proj,cfg,image = project(tmp_path,pinned=False)
    split_config(tmp_path,image)
    path = tmp_path/'split.yaml'
    if case=='missing':path.unlink()
    if case=='two':(tmp_path/'other.yaml').write_text(path.read_text())
    if case=='target':path.write_text(yaml.safe_dump({'sha1':sha(image),'options':{'target_path':'another.bin'}}))
    if case=='invalid':path.write_text(yaml.safe_dump({'sha1':'not a digest','options':{'target_path':'analysis.bin'}}))
    if case=='malformed':path.write_text('[broken')
    if case=='large':path.write_text('x'* (1024*1024+1))
    if case in ('symlink','outside'):
        outside=tmp_path.parent/(tmp_path.name+'-original.bin');outside.write_bytes(b'original program')
        if case=='symlink':(tmp_path/'original.bin').unlink();(tmp_path/'original.bin').symlink_to(outside)
        else:cfg['rom']['program']=str(outside)
    with pytest.raises(NetworkError,match='No recorded hash'):
        analysis_image.verify(image,proj,cfg)


def test_gap_audit_uses_analysis_identity_before_reading_function_evidence(tmp_path,monkeypatch):
    from decompnet.app import code_audit_reviews
    from decompnet.consoles import get
    proj,cfg,image = project(tmp_path)
    read=[]
    store=SimpleNamespace(all=lambda *a:read.append(a) or [])
    net=SimpleNamespace(project_row=lambda _:proj,is_rom=lambda _:True,_rom=lambda _:(image,[]),
                        cfg=lambda _:cfg,console=lambda _:get('n64'),project_store=lambda _:store)
    monkeypatch.setattr(code_audit_reviews,'revision',lambda *a:'revision')
    monkeypatch.setattr(code_audit_reviews,'record',lambda n,p,r,i:dict(r,image_sha1=sha(i)))
    assert rom_gap_audit.run(net,'game',log=lambda *_:None)['image_sha1'] == sha(image)
    assert len(read)==1
    cfg['rom']['analysis_sha1']=sha(b'wrong')
    with pytest.raises(NetworkError,match='differs'):
        rom_gap_audit.run(net,'game',log=lambda *_:None)
    assert len(read)==1


def test_legacy_original_verification_can_be_cancelled_between_bounded_reads(tmp_path):
    proj,cfg,image = project(tmp_path,pinned=False)
    original=b'x'*(2*1024*1024+1)
    (tmp_path/'original.bin').write_bytes(original)
    cfg['rom']['sha1']=sha(original)
    split_config(tmp_path,image)
    calls=[]
    def stop():
        calls.append(1)
        if len(calls)==3:raise RuntimeError('Stopped by user')
    with pytest.raises(RuntimeError,match='Stopped by user'):
        analysis_image.verify(image,proj,cfg,check=stop)
    assert len(calls)==3
    assert 'analysis_sha1' not in cfg['rom']
    assert (tmp_path/'original.bin').read_bytes()==original


def test_no_recorded_identity_is_not_silently_accepted(tmp_path):
    proj,cfg,image = project(tmp_path,derived=False,pinned=False)
    cfg['rom'].pop('sha1')
    with pytest.raises(NetworkError,match='No recorded hash'):
        analysis_image.verify(image,proj,cfg)
