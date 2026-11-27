import hashlib
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.consoles.n64 import buildtools, sgi_frontend


def test_frontend_is_pinned_and_requires_consent():
    tc = toolchains.get('mipspro-7.2.1-cxx')
    assert tc.proprietary
    assert tc.build == 'sgi-inst-frontend'
    assert len(tc.parts) == 2 and all(len(part['sha256']) == 64 for part in tc.parts)


def test_inst_image_rejects_wrong_executable(tmp_path):
    name = 'usr/lib/DCC/edgcpfe'
    (tmp_path / 'c++_fe.idb').write_text(f'f a b c {name} size(4) cmpsize(0)\n')
    (tmp_path / 'c++_fe.sw').write_bytes(b'\0' + bytes([len(name)]) + name.encode() + b'bad!')
    with pytest.raises(ValueError, match='checksum'):
        sgi_frontend.extract(tmp_path)
    assert not (tmp_path / name).exists()


def test_installed_frontend_is_supplied_without_overwriting(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'tools').mkdir(parents=True)
    (project / 'tools/fetch-mipspro721.sh').touch()
    pack = tmp_path / 'pack'
    source = pack / 'usr/lib/DCC/edgcpfe'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'compiler')
    monkeypatch.setattr(buildtools.toolchains, 'installed', lambda tid: {'id': tid})
    monkeypatch.setattr(buildtools.toolchains, 'require', lambda tid: pack)
    buildtools._provide_mipspro_frontend(SimpleNamespace(root=project))
    target = project / 'tools/ido-static-recomp/build/mipspro721/usr/lib/DCC/edgcpfe'
    assert target.is_symlink() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(b'compiler').digest()
    target.unlink()
    target.write_bytes(b'owned')
    buildtools._provide_mipspro_frontend(SimpleNamespace(root=project))
    assert target.read_bytes() == b'owned'
