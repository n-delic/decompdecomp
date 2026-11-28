"""Section directories recover overlapping banks without trusting arbitrary pointer tables."""

import struct

import yaml

from decompnet.consoles.n64 import sectioned_overlays


def directory_image():
    image = bytearray(0x1000)
    base = 0x80200000
    for index in range(4):
        start = 0x400 + index * 0x200
        end = start + 0x200
        struct.pack_into('>9I', image, 0x100 + index * 36, start, end, base, base,
                         base + 0x180, base + 0x180, base + 0x200, base + 0x200, base + 0x300)
        for offset in (0, 0x40):
            struct.pack_into('>5I', image, start + offset, 0x27bdffe0, 0xafbf001c, 0x8fbf001c,
                             0x03e00008, 0x27bd0020)
    return image


def test_directory_proves_independent_overlays_and_sections():
    image = directory_image()
    banks = sectioned_overlays.read(image, [(0, 0x80, 0x80000400)])
    assert [(b.start, b.end, b.text_end-b.base) for b in banks] == [
        (0x400, 0x600, 0x180), (0x600, 0x800, 0x180),
        (0x800, 0xa00, 0x180), (0xa00, 0xc00, 0x180),
    ]
    assert not sectioned_overlays.read(image, [(0x500, 0x510, 0x80000500)])


def test_directory_rejects_inconsistent_descriptors_and_noncode():
    image = directory_image()
    image[0x100 + 36 + 23] ^= 4  # duplicate text/data section boundary disagrees
    assert not sectioned_overlays.read(image, [])
    image = directory_image()
    image[0x800:0x880] = bytes(0x80)
    assert not sectioned_overlays.read(image, [])


def test_insertion_keeps_code_and_data_in_separate_scoped_banks(tmp_path):
    image = directory_image()
    config = tmp_path/'splat.yaml'
    config.write_text(yaml.safe_dump({'segments': [{'start': 0, 'type': 'bin'}, [len(image)]]}))
    banks = sectioned_overlays.read(image, [])
    sectioned_overlays.add(config, tmp_path, image, [(0x3e0, 0xc20, 0x80001000)], banks, lambda _: None)
    segments = yaml.safe_load(config.read_text())['segments']
    found = [s for s in segments if isinstance(s, dict) and s.get('name', '').startswith('sectioned_overlay_')]
    assert [s['start'] for s in found] == [0x400, 0x600, 0x800, 0xa00]
    assert all(s['vram'] == 0x80200000 and s['exclusive_ram_id'] == 'sectioned_80200000' for s in found)
    assert found[0]['subsegments'][1][0:2] == [0x580, 'data']
    before = next(s for s in segments if isinstance(s, dict) and s.get('start') == 0x3e0)
    after = next(s for s in segments if isinstance(s, dict) and s.get('start') == 0xc00)
    assert before['vram'] == 0x80001000
    assert after['vram'] == 0x80001820
