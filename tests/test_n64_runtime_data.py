"""A runtime-observed code bank can live inside a split's unowned data tail."""
import copy

import pytest

from decompnet.consoles.n64 import code, runtime_data
from decompnet.rom import RomError


def fixture():
    image = bytearray(0x1500)

    def put(offset, words):
        for index, word in enumerate(words):
            image[offset + index * 4:offset + index * 4 + 4] = word.to_bytes(4, 'big')

    def leaf(value):
        return [0x27bdfff0, 0x24020000 | value, 0x03e00008, 0x27bd0010]

    def jal(address):
        return 0x0c000000 | ((address >> 2) & 0x3ffffff)

    put(0x1100, leaf(1))
    put(0x1180, leaf(2))
    put(0x1200, [0x27bdffe0, 0xafbf001c, jal(0x80020100), 0, jal(0x80020180), 0,
                 0x8fbf001c, 0x03e00008, 0x27bd0020])
    ram = bytearray(0x30000)
    ram[0x20000:0x20500] = image[0x1000:0x1500]
    cfg = {'segments': [{'type': 'code', 'name': 'main', 'start': 0x1000, 'vram': 0x80001000,
                         'subsegments': [[0x1000, 'c', 'main'], [0x1080, 'data']]}, [0x1500]]}
    return bytes(image), cfg, bytes(ram)


def test_runtime_data_requires_two_coexisting_closed_callees_and_two_snapshots():
    image, cfg, ram = fixture()
    known = [(0x1000, 0x1080, 0x80001000)]
    expected = [(0x1100, 0x1110, 0x80020100), (0x1180, 0x1190, 0x80020180),
                (0x1200, 0x1224, 0x80020200)]
    assert runtime_data.discover(image, cfg, known, [ram, ram]) == expected
    assert not runtime_data.discover(image, cfg, known, [ram])
    corrupt = bytearray(ram)
    corrupt[0x20180] ^= 1
    assert not runtime_data.discover(image, cfg, known, [ram, bytes(corrupt)])
    other = bytearray(ram)
    other[0x20100] ^= 1
    # Each body appears twice, but the three never coexist in two snapshots.
    assert not runtime_data.discover(image, cfg, known, [bytes(corrupt), ram, bytes(other)])
    assert not runtime_data.discover(image, cfg, known + [(0x1400, 0x1410, 0x80001400)], [ram, ram])
    assert not runtime_data.discover(image, cfg, known + [(0x300, 0x310, 0x80020100)], [ram, ram])


def test_only_verified_runtime_bodies_can_split_a_mapped_data_tail(tmp_path):
    image, cfg, ram = fixture()
    bodies = runtime_data.discover(image, cfg, [(0x1000, 0x1080, 0x80001000)], [ram, ram])
    with pytest.raises(RomError):
        code.insert_segments(tmp_path / 'game.yaml', copy.deepcopy(cfg), bodies, len(image))
    result = copy.deepcopy(cfg)
    code.insert_segments(tmp_path / 'game.yaml', result, bodies, len(image), runtime_data=set(bodies))
    segments = result['segments']
    assert [(s['start'], s['vram']) for s in segments if isinstance(s, dict) and s.get('name', '').startswith('code_')] == [
        (0x1100, 0x80020100), (0x1180, 0x80020180), (0x1200, 0x80020200)]
    assert any(s.get('type') == 'bin' and s.get('start') == 0x1224 for s in segments if isinstance(s, dict))
