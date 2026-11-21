import struct

import yaml

from decompnet.consoles.n64 import raw_slots


def cartridge(*, reference=True, incompatible=False):
    image = bytearray(1024)
    if reference:
        words = (0x3c028000,0x245e0180,0x00101880,0x00701821,0x00031880,
                 0x8c740000,0x8c650008,0x8c46000c,0x8c720004)
        struct.pack_into('>9I',image,0,*words)
    slot = 0x80100000
    struct.pack_into('>10I',image,0x180,slot,slot+0x200,0xb0000200,0xb0000280,0xb0000240,
                     slot+4*incompatible,slot+0x200,0xb0000280,0xb0000300,0xb0000300)
    for offset in (0x204,0x284):
        struct.pack_into('>I',image,offset,0x03e00008)
    return bytes(image)


def test_resident_code_establishes_shared_raw_bank_directory():
    mapping = [(0,128,0x80000000)]
    assert raw_slots.read(cartridge(),mapping) == [
        raw_slots.Slot(0x200,0x280,0x80100000),
        raw_slots.Slot(0x280,0x300,0x80100000),
    ]
    assert raw_slots.read(cartridge(reference=False),mapping) == []
    assert raw_slots.read(cartridge(incompatible=True),mapping) == []


def test_scoped_bank_parts_keep_their_physical_ranges(tmp_path):
    image = cartridge()
    config = tmp_path/'layout.yaml'
    config.write_text(yaml.safe_dump({'segments': [
        dict(name='main',type='code',start=0,vram=0x80000000,subsegments=[[0,'c','main']]),
        dict(type='bin',start=128),[len(image)],
    ]}))
    slots = raw_slots.read(image,[(0,128,0x80000000)])
    seen = [(0x200,0x220,0x80200000),(0x228,0x238,0x80200028),
            (0x280,0x2a0,0x80300000),(0x2f8,0x310,0x80300078)]
    assert raw_slots.add(config,image,seen,slots,lambda _: None)
    rows = yaml.safe_load(config.read_text())['segments']
    banks = [row for row in rows if isinstance(row,dict) and row.get('name','').startswith('raw_slot_')]
    assert len(banks) == 2 and banks[0]['vram'] == banks[1]['vram'] == 0x80100000
    assert banks[0]['exclusive_ram_id'] == banks[1]['exclusive_ram_id']
    assert banks[0]['symbol_name_format'] != banks[1]['symbol_name_format']
    assert banks[0]['subsegments'][1] == [0x220,'bin','raw_slot_00_gap_1']
    high = next(row for row in rows if isinstance(row,dict) and row['start'] == 0x300)
    assert high['vram'] == 0x80300080 and high['exclusive_ram_id'] != banks[0]['exclusive_ram_id']


def test_only_native_graphics_fields_count_as_non_pointer_words():
    from decompnet.consoles.n64.packed_pointers import is_data

    # The word at 16 is a token mask whose first bit emits one literal.
    yay = struct.pack('>4s4I',b'Yay0',1,20,20,0x8000100c)+b'A'
    assert is_data(yay,16)
    assert not is_data(yay[:-1],16)
    assert not is_data(yay,12)

    gbi = struct.pack('>10I',0x01001002,0x80101000,0x01001004,0x80101010,
                      0x01001006,0x80101020,0x05000204,0,0xdf000000,0)
    assert is_data(gbi,12)
    damaged = gbi[:24]+bytes(16)
    assert not is_data(damaged,12)


def test_current_thread_dispatch_checks_the_target_and_the_same_global():
    from decompnet.rom.automatic_boundaries import calls_current_thread_dispatch
    from decompnet.rom.split import Segment

    words = [0]*54
    words[0],words[2],words[6] = 0x27bdffe0,0x00808021,0x16000004
    words[8],words[10],words[41],words[42] = 0x3c108007,0x8e10c620,0x3c028007,0x8c42c620
    words[43] = 0x16020003
    words[45] = 0x0c000400
    words[-2:] = [0x03e00008,0x27bd0020]
    start = 0x80002000
    segments = [Segment('caller',0,216,start)]
    assert calls_current_thread_dispatch(struct.pack('>54I',*words),start,0x80001000,segments,'big')
    assert not calls_current_thread_dispatch(struct.pack('>54I',*words),start,0x80001004,segments,'big')
    words[42] ^= 4
    assert not calls_current_thread_dispatch(struct.pack('>54I',*words),start,0x80001000,segments,'big')
