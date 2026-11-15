"""An operator can repair split ROM functions without altering original bytes or losing internal names."""
import json

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def project(tmp_path, overlap=False):
    net = RomNetwork(tmp_path / 'test.db', tmp_path)
    image = bytes(range(16))
    (tmp_path / 'game.bin').write_bytes(image)
    config = {'kind': 'rom', 'platform': 'ps', 'rom': {'file': 'game.bin', 'sha1': 'toy'},
              'segments': [{'name': 'main', 'rom_start': 0, 'rom_end': 16, 'vram': 0x80010000}]}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(1,?,?,?,?)',
                   ('toy', 'toy', str(tmp_path), json.dumps(config)))
        for index, (offset, size) in enumerate([(0, 12 if overlap else 8), (8, 8)]):
            name = ['head', 'tail'][index]
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,status,unit,vram,rom_offset,meta) '
                       'VALUES(1,?,?,?,?,?,?,?,?,?,?,?)',
                       (name, size, size // 4, image[offset:offset + size], '[]', f'glabel {name}\nendlabel {name}',
                        'asm', 'main', 0x80010000 + offset, offset, '{"asm_code":"fragment"}'))
            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,?,?,?,?)',
                       (name, 0x80010000 + offset, size, 'func'))
    return net


@pytest.mark.parametrize('overlap', [False, True])
def test_join_preserves_original_bytes_and_internal_symbol(tmp_path, overlap):
    net = project(tmp_path, overlap)
    net.join_rom_functions('toy', ['head', 'tail'], 'verified complete object span')
    row = net._function('head', 'toy')
    assert row['target'] == bytes(range(16))
    assert row['size'] == 16
    assert row['status'] == 'open'
    assert net.symbols(1).resolve('tail') == 0x80010008
    assert len(json.loads(row['meta'])['joined_from']) == 2
    assert len(net.store.all('SELECT * FROM functions')) == 1


def test_join_refuses_matched_piece(tmp_path):
    net = project(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='tail'")
    with pytest.raises(NetworkError, match='matched function'):
        net.join_rom_functions('toy', ['head', 'tail'], 'test')
    assert len(net.store.all('SELECT * FROM functions')) == 2


def test_join_refuses_changed_original_bytes(tmp_path):
    net = project(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE name='tail'", (b'wrong!!!',))
    with pytest.raises(NetworkError, match='original image'):
        net.join_rom_functions('toy', ['head', 'tail'], 'test')
