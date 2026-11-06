"""An overlay's strings must come from its own original data, including when RAM addresses overlap."""
import hashlib
import json

import pytest

from decompnet.app.engine.core import Core
from decompnet.network import NetworkError
from decompnet.rom.memory import overlay_reader
from decompnet.rom.split import Segment


def manifest(tmp_path):
    data = b'first\0second\0'
    (tmp_path / 'overlay-data.bin').write_bytes(data)
    spec = {'format': 1, 'image_sha1': 'original', 'file': 'overlay-data.bin',
            'sha256': hashlib.sha256(data).hexdigest(), 'regions': [
                {'owner': 'a', 'offset': 0, 'size': 6, 'vram': 0x80010000},
                {'owner': 'b', 'offset': 6, 'size': 7, 'vram': 0x80010000}]}
    (tmp_path / 'memory_regions.json').write_text(json.dumps(spec))
    return spec


def test_owner_selects_original_data(tmp_path):
    manifest(tmp_path)
    assert overlay_reader(tmp_path, 'original', 'a')(0x80010000, 6) == b'first\0'
    assert overlay_reader(tmp_path, 'original', 'b')(0x80010000, 7) == b'second\0'
    assert overlay_reader(tmp_path, 'original', 'b')(0x80010007, 1) is None


def test_changed_input_is_rejected(tmp_path):
    manifest(tmp_path)
    with pytest.raises(NetworkError, match='another analysis image'):
        overlay_reader(tmp_path, 'other', 'a')
    (tmp_path / 'overlay-data.bin').write_bytes(b'altered')
    with pytest.raises(NetworkError, match='checksum changed'):
        overlay_reader(tmp_path, 'original', 'a')


def test_owning_code_segment_precedes_other_overlays(tmp_path):
    class Fake:
        _rom = lambda self, proj: (b'AAAABBBB', [Segment('a', 0, 4, 100), Segment('b', 4, 8, 100)])
        cfg = lambda self, proj: {'rom': {'sha1': 'original'}}
    obj = Fake()
    proj = {'root': str(tmp_path)}
    assert Core.reader(obj, proj, {'rom_offset': 4})(100, 4) == b'BBBB'
    assert Core.reader(obj, proj)(100, 4) == b'AAAA'


def test_thin_function_row_recovers_its_physical_offset(tmp_path):
    class Store:
        def one(self, sql, pid, name):
            assert pid == 1 and name == 'function_b'
            return {'rom_offset': 4}
    class Fake:
        store = Store()
        _rom = lambda self, proj: (b'AAAABBBB', [Segment('a', 0, 4, 100), Segment('b', 4, 8, 100)])
        cfg = lambda self, proj: {'rom': {'sha1': 'original'}}
    assert Core.reader(Fake(), {'id': 1, 'root': str(tmp_path)}, {'name': 'function_b'})(100, 4) == b'BBBB'
