import struct

import yaml

from decompnet.consoles.n64 import copied_overlays


def _image(*, connects=True):
    image = bytearray(0x500)
    # Copy an array of rows to scratch before using its cartridge and RAM bounds.
    words = [0x3c0e8000, 0x25ce0180, 0x25d90054, 0x8dc10000, 0x25ce000c,
             0x15d9fffd if connects else 0x15d8fffd]
    words += [0]*12 + [0x8e000000 | at for at in (4,8,12,16,20,24)]
    struct.pack_into(f'>{len(words)}I', image, 0, *words)
    base = 0x80100000
    struct.pack_into('>14I', image, 0x180, 0,base,base+0xa0,0x240,0x2c0,base,base+0x60,
                     0,base,base+0xc0,0x2c0,0x360,base,base+0x80)
    return bytes(image)


def test_copied_overlay_tables_supply_scoped_text_and_data(tmp_path):
    image = _image()
    config = tmp_path/'splat.yaml'
    config.write_text(yaml.safe_dump({'segments': [
        dict(name='main',type='code',start=0,vram=0x80000000,subsegments=[[0,'c','main']]),
        dict(type='bin',start=0x100),[len(image)],
    ]}))
    banks = copied_overlays.read(image, [(0,0x100,0x80000000)])
    assert [(b.start,b.text_end,b.vram) for b in banks] == [(0x240,0x2a0,0x80100000),
                                                          (0x2c0,0x340,0x80100000)]
    copied_overlays.add(config,image,[(0x240,0x3a0,0x80100000)],banks,lambda *_: None)
    rows = yaml.safe_load(config.read_text())['segments']
    segments = {r['start']:r for r in rows if isinstance(r,dict)}
    assert segments[0x240]['exclusive_ram_id'] == segments[0x2c0]['exclusive_ram_id']
    assert segments[0x240]['subsegments'][-1][:2] == [0x2a0,'data']
    assert segments[0x360]['vram'] == 0x80100120


def test_disconnected_directory_copy_stays_raw():
    assert not copied_overlays.read(_image(connects=False), [(0,0x100,0x80000000)])
