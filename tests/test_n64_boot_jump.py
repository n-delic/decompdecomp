"""Only a directly jumped bootstrap with closed helpers replaces skipped metadata."""
import struct

import yaml

from decompnet.consoles.n64 import boot_jump


def fixture(tmp_path):
    image = bytearray(0x4000)
    vram = 0x80000400
    jump = lambda target, op=2: op << 26 | ((target >> 2) & 0x3ffffff)
    struct.pack_into('>4I', image, 0x1000, jump(vram+0x30), 0, 0, 0)
    # A short setup calls its closed helper, then transfers to a register.
    setup = [0x24040001]*4 + [jump(vram+0x50, 3), 0, 0x00800008, 0]
    struct.pack_into('>8I', image, 0x1030, *setup)
    struct.pack_into('>2I', image, 0x1050, 0x03e00008, 0)
    config = tmp_path/'splat.yaml'
    config.write_text(yaml.safe_dump(dict(segments=[dict(name='entry', type='code', start=0x1000, vram=vram,
                                                        subsegments=[[0x1000, 'hasm']]),
                                                 dict(name='main', type='code', start=0x1010, follows_vram='entry',
                                                      subsegments=[[0x1010, 'c', 'main']]),
                                                 dict(type='bin', start=0x1020)]), sort_keys=False))
    return config, image


def test_jump_recovers_closed_boot_and_keeps_skipped_bytes(tmp_path):
    config, image = fixture(tmp_path)
    assert boot_jump.recover(config, bytes(image))
    rows = yaml.safe_load(config.read_text())['segments']
    assert [(r['start'], r['type']) for r in rows] == [
        (0x1000, 'code'), (0x1010, 'bin'), (0x1030, 'code'), (0x1050, 'code'), (0x1058, 'bin')]
    assert rows[2]['vram'] == 0x80000430 and rows[3]['vram'] == 0x80000450
    assert 'boot_80000430 = 0x80000430; // type:func' in (tmp_path/'symbol_addrs.txt').read_text()


def test_ambiguous_skipped_entry_or_unclosed_helper_is_left_alone(tmp_path):
    config, image = fixture(tmp_path)
    struct.pack_into('>I', image, 0x2000, 0x80000414)
    assert not boot_jump.recover(config, bytes(image))
    config, image = fixture(tmp_path)
    struct.pack_into('>I', image, 0x1050, 0x00800008)
    assert not boot_jump.recover(config, bytes(image))
