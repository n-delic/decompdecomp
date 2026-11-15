"""An explicit operator split preserves original code and respects matches and active claims."""
import json
import struct
import time

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


def project(tmp_path):
    net = RomNetwork(tmp_path / 'toy.db', tmp_path)
    words = [0x24080001, 0x03E00008, 0, 0x24090002, 0x03E00008, 0]
    image = struct.pack('<6I', *words)
    (tmp_path / 'game.bin').write_bytes(image)
    config = {'kind': 'rom', 'platform': 'ps', 'big_endian': False, 'rom': {'file': 'game.bin', 'sha1': 'toy'},
              'segments': [{'name': 'main', 'rom_start': 0, 'rom_end': len(image), 'vram': 0x80010000}]}
    texts = ['addiu $t0, $zero, 1', 'jr $ra', 'nop', 'addiu $t1, $zero, 2', 'jr $ra', 'nop']
    asm = 'glabel combined\n' + '\n'.join(
        f'/* {i*4:06X} {0x80010000+i*4:08X} {word:08X} */ {text}'
        for i, (word, text) in enumerate(zip(words, texts))) + '\nendlabel combined\n'
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config) VALUES(1,?,?,?,?)',
                   ('toy', 'toy', str(tmp_path), json.dumps(config)))
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,asm,status,unit,vram,rom_offset,best_source) '
                   "VALUES(1,'combined',24,6,?,'[]',?,'open','main',2147549184,0,'obsolete whole draft')", (image, asm))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(1,'combined',2147549184,24,'func')")
    return net, image


def test_operator_split_preserves_both_function_bodies(tmp_path):
    net, image = project(tmp_path)
    net.split_rom_by_you('toy', 'combined', '0x8001000C', 'second')
    rows = net.store.all('SELECT * FROM functions ORDER BY rom_offset')
    assert len(rows) == 2
    assert b''.join(r['target'] for r in rows) == image
    assert all(r['status'] == 'open' for r in rows)
    assert rows[0]['best_source'] is None


def test_operator_split_refuses_active_claim(tmp_path):
    net, _ = project(tmp_path)
    agent, _ = net.local_agent('helper')
    with net.store.tx() as db:
        db.execute('INSERT INTO claims(function_id,agent_id,claimed_at,expires_at) '
                   'SELECT id,?,?,? FROM functions', (agent.id, time.time(), time.time() + 60))
    with pytest.raises(NetworkError, match='active claim'):
        net.split_rom_by_you('toy', 'combined', '0x8001000C', 'second')


def test_operator_split_refuses_matched_function(tmp_path):
    net, _ = project(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched'")
    with pytest.raises(NetworkError, match='matched'):
        net.split_rom_by_you('toy', 'combined', '0x8001000C', 'second')
