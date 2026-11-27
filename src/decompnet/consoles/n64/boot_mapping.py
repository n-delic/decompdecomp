"""Separate a TLB boot stub from code splat mistakenly placed at the boot address."""
import collections
import re
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


def extend_startup_trap(config: Path, rom: bytes, log=print) -> bool:
    """Keep a break after the boot stub's final call in handwritten startup."""
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    by_name = {s.get('name'):s for s in cfg['segments'] if isinstance(s, dict)}
    entry, main = by_name.get('entry'), by_name.get('main')
    if not entry or not main or main.get('follows_vram') != 'entry':
        return False
    lo, at = entry['start'], main['start']
    if (not 0x1000 <= lo < at-8 < at+8 < min(0x2000,len(rom))
            or entry.get('subsegments') != [[lo,'hasm']] or main.get('subsegments') != [[at,'c','main']]
            or entry['vram']-lo != main['vram']-at):
        return False
    call, delay, trap, padding = struct.unpack_from('>4I', rom, at-8)
    vram = main['vram']
    entry_words = [word for word, in struct.iter_unpack('>I',rom[lo:at])]
    callers = set(jal_targets(entry_words,entry['vram']))
    if call >> 26 != 3 or delay or trap & 0xfc00003f != 0xd or vram in callers:
        return False
    if not padding and vram + 8 in callers and vram + 4 not in callers:
        skip = 8
    elif (all(32 <= byte < 127 for byte in rom[at+4:at+8])
          and vram+8 in callers and vram+4 not in callers
          and ((vram-4) & 0xf0000000) | ((call & 0x3ffffff) << 2) == vram+8
          and is_frame(struct.unpack_from('>I', rom, at+8)[0])):
        # A printable descriptor follows the terminal trap, before its called frame.
        skip = 8
    elif (padding >> 26 == 15 and (padding >> 16) & 31 and vram + 4 not in callers
          and any(vram + 4 < target < vram + 0x100 for target in callers)):
        # A separate helper immediately follows the trap; keep its first instruction.
        skip = 4
    else:
        return False
    main.update(start=at+skip,vram=vram+skip,subsegments=[[at+skip,'c','main']])
    config.write_text(yaml.safe_dump(cfg,sort_keys=False,default_flow_style=None),encoding='utf-8')
    log('Kept the trap after the startup call in the boot assembly.')
    return True


def extend_startup_data(config: Path, rom: bytes, log=print) -> bool:
    """Keep the terminator of a boot data string on the data side of the split."""
    from ...arch.mips_code_recovery import called_extent

    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    layout = segments_from_config_data(cfg)
    entry = next((segment for segment in layout if segment.name == 'entry'), None)
    main = next((segment for segment in layout if segment.name == 'main'), None)
    if entry is None or main is None or entry.rom_end != main.rom_start:
        return False
    raw_entry, raw_main = (next(row for row in cfg['segments']
                               if isinstance(row, dict) and row.get('name') == name) for name in ('entry', 'main'))
    at, end, address = main.rom_start, main.rom_end, main.vram
    data = raw_entry.get('subsegments', [])[-1:]
    if (not 0x1000 < at < 0x2000 or (address+4)%16 or end > len(rom)
            or raw_main.get('follows_vram') != 'entry' or raw_main.get('subsegments') != [[at, 'c', 'main']]
            or not data or not isinstance(data[0], list) or data[0][1:2] != ['data']
            or not data[0][0] <= at-4 or entry.vram-entry.rom_start != address-at
            or not re.fullmatch(rb'[A-Za-z]{3}\x00', rom[at:at+4])
            or address.to_bytes(4, 'big') in rom):
        return False
    targets = set(jal_targets((word for word, in struct.iter_unpack('>I', rom[at:end])), address))
    if address in targets or address+4 not in targets:
        return False
    if not called_extent(rom, at+4, end, address+4, cpu='vr4300'):
        return False
    raw_main.update(start=at+4, vram=address+4, subsegments=[[at+4, 'c', 'main']])
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding='utf-8')
    log('Kept the boot data string before the first called frame.')
    return True


_VERSION_DATE = re.compile(rb'Version date +[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4} +[0-9]{2}:[0-9]{2}:[0-9]{2}\0')


def mark_boot_version(config: Path, rom: bytes, log=print) -> bool:
    """Keep a version stamp after the startup trap out of the function list."""
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    by_name = {row.get('name'):row for row in cfg['segments'] if isinstance(row, dict)}
    entry, raw_main = by_name.get('entry'), by_name.get('main')
    if not entry or not raw_main or raw_main.get('follows_vram') != 'entry':
        return False
    main = next((segment for segment in segments_from_config_data(cfg) if segment.name == 'main'), None)
    if (main is None or not 0x100c <= main.rom_start < main.rom_end < 0x2000
            or raw_main.get('subsegments') != [[main.rom_start,'c','main']]
            or entry.get('subsegments') != [[entry['start'],'hasm']]):
        return False
    at = main.rom_start
    stamp = _VERSION_DATE.match(rom, at, at+64)
    if not stamp or main.rom_end > stamp.end():
        return False
    call, delay, trap = struct.unpack_from('>3I', rom, at-12)
    if call>>26 != 3 or delay or trap&0xfc00003f != 0xd:
        return False
    target = ((main.vram-8)&0xf0000000) | ((call&0x03ffffff)<<2)
    if main.vram <= target < main.vram+main.rom_end-at:
        return False
    raw_main['subsegments'] = [[at,'rodata','boot_version']]
    config.write_text(yaml.safe_dump(cfg,sort_keys=False,default_flow_style=None),encoding='utf-8')
    log('Kept the version stamp following the boot trap in read-only data.')
    return True
