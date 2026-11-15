"""Separate a TLB boot stub from code splat mistakenly placed at the boot address."""
import collections
import struct
from pathlib import Path

import yaml

from ...arch.mips import JR_RA, is_frame, jal_targets
from ...rom.split import segments_from_config_data

MIN_CALLED_FRAMES = 8
MAPPING_ADVANTAGE = 4
MAX_BOOT_BYTES = 0x1000
TLBWI = 0x42000002


def recover(config: Path, rom: bytes, found, log=print) -> bool:
    """Require an established mapping, independent calls and a complete boot return before splitting.

    A cartridge's initial main range can still contain its TLB setup. Treating that entire
    range as boot-address code hid hundreds of compiled functions behind one assembly entry.
    This only revisits splat's simple main-after-entry layout, never an explicit data/BSS layout.
    """
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = segments_from_config_data(cfg)
    entry = next((s for s in segments if s.name == 'entry'), None)
    main = next((s for s in segments if s.name == 'main'), None)
    if entry is None or main is None or not (0x1000 <= entry.rom_start < main.rom_start < 0x2000):
        return False
    raw = next(s for s in cfg['segments'] if isinstance(s, dict) and s.get('name') == 'main')
    old_mapping = main.vram - main.rom_start
    if (raw.get('follows_vram') != 'entry' or entry.vram - entry.rom_start != old_mapping
            or raw.get('bss_size') or raw.get('subsegments') != [[main.rom_start, 'c', 'main']]
            or not (0 <= main.rom_start < main.rom_end <= len(rom)) or (main.rom_end-main.rom_start) % 4):
        return False
    words = struct.unpack(f'>{(main.rom_end-main.rom_start)//4}I', rom[main.rom_start:main.rom_end])
    frames = {main.rom_start+i*4 for i,w in enumerate(words) if is_frame(w)}
    calls = set(jal_targets(words, main.vram))
    mappings = {v-s for s,_,v in found}
    mappings.update(s.vram-s.rom_start for s in segments if s.name not in ('entry','main'))
    scores = collections.Counter({mapping: sum(at+mapping in calls for at in frames)
                                  for mapping in mappings | {old_mapping}})
    ranked = scores.most_common()
    if not ranked:
        return False
    mapping, hits = ranked[0]
    if (mapping == old_mapping or hits < MIN_CALLED_FRAMES
            or hits < MAPPING_ADVANTAGE * max(1, scores[old_mapping])
            or len(ranked) > 1 and hits < MAPPING_ADVANTAGE * max(1, ranked[1][1])):
        return False
    called = sorted(t-mapping for t in calls if main.rom_start <= t-mapping < main.rom_end)
    if not called:
        return False
    start = called[0]
    index = (start-main.rom_start)//4
    # The first new entry must follow the stub's return and delay slot, without
    # unexplained intervening bytes. Earlier frameless entries are not discarded.
    if (start not in frames or start-entry.rom_start > MAX_BOOT_BYTES
            or index < 2 or words[index-2] != JR_RA
            or TLBWI not in words[:index] or any(is_frame(w) for w in words[:index])):
        return False
    vram = start+mapping
    end_vram = main.rom_end+mapping
    other_ranges = [(s.vram,s.vram+s.rom_end-s.rom_start) for s in segments if s.name != 'main']
    other_ranges += [(main.vram,main.vram+start-main.rom_start)]
    other_ranges += [(v,v+en-st) for st,en,v in found]
    if any(lo < end_vram and vram < hi for lo,hi in other_ranges):
        return False
    name = f'code_{vram:08X}'
    new = {'name':name,'type':'code','start':start,'vram':vram,'subsegments':[[start,'c',name]]}
    cfg['segments'].insert(cfg['segments'].index(raw)+1,new)
    config.write_text(yaml.safe_dump(cfg,sort_keys=False,default_flow_style=None),encoding='utf-8')
    log(f'Separated TLB boot code from {main.rom_end-start} bytes at 0x{vram:08X}; '
        f'{hits} distinct calls agree with the later code mapping.')
    return True
