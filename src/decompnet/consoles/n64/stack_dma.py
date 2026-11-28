"""Proven cartridge-to-RAM ranges from constant, stack-argument PI DMA calls."""
import struct

from ...arch.mips_code_recovery import called_extent
from .raw_slots import _CART, _address


def _request_body(image, address, segments, cpu):
    """Recognize the bounded message builder that forwards the PI transfer arguments."""
    owners = [s for s in segments if s.vram <= address and address+8 <= s.vram+s.rom_end-s.rom_start]
    if len(owners) != 1:
        return False
    owner = owners[0]
    at = owner.rom_start+address-owner.vram
    end = min(at+512, owner.rom_end, len(image))
    size = called_extent(image, at, end, address, cpu=cpu)
    if not size:
        return False
    words = struct.unpack_from(f'>{size//4}I', image, at)
    # This SDK build copies the caller's stack-passed RAM address, length and queue
    # into the PI message, with the cartridge address from a3, then sends it.
    required = (0x8fa40028, 0x8fa8002c, 0x8fa30030,
                0xae030004, 0xae040008, 0xae07000c, 0xae080010)
    positions = [words.index(w) if w in words else -1 for w in required]
    return (-1 not in positions and positions[:3] == sorted(positions[:3])
            and positions[3:] == list(range(positions[3], positions[3]+4))
            and any(w >> 26 == 3 for w in words[positions[-1]+1:]))


def ranges(image, segments, cpu='vr4300'):
    """Find constant PI reads that contain an independently mapped code segment.

    The call's cartridge and RAM bounds must agree with the code segment's own
    mapping; conflicting records remain separate so the table reader rejects them.
    """
    import rabbitizer

    result, checked = set(), {}
    for segment in segments:
        if not 0 <= segment.rom_start < segment.rom_end <= len(image) or segment.rom_start % 4:
            continue
        words = struct.unpack_from(f'>{(segment.rom_end-segment.rom_start)//4}I', image, segment.rom_start)
        for i in range(1, len(words)-10):
            h, l, eh, el, sub = words[i:i+5]
            # Read mode (a2=0), derived from the zero priority in a1.
            if i < 2 or words[i-2:i] != (0x00002821, 0x00a03021):
                continue
            if h >> 16 != 0x3c07 or l >> 16 != 0x24e7 or eh >> 16 != 0x3c02 or el >> 16 != 0x2442 or sub != 0x00471023:
                continue
            source, end = _address(h,l)-_CART, _address(eh,el)-_CART
            if not 0 <= source < end <= len(image) or (source | end) % 4:
                continue
            other_hi, other_lo, store, length, call = words[i+5:i+10]
            register = (store >> 16) & 31
            other = (other_hi >> 16) & 31
            if (other not in range(16,24) or other == register
                    or other_hi >> 16 != 0x3c00 | other or other_lo >> 16 != 0x2400 | other << 5 | other
                    or store != 0xafa00010 | register << 16 or register not in range(16,24)
                    or length != 0xafa20014 or call >> 26 != 3
                    or words[i+10] != 0xafa00018 | other << 16):
                continue
            destination = None
            for j in range(max(0,i-32),i-1):
                if words[j] >> 16 == 0x3c00 | register and words[j+1] >> 16 == 0x2400 | register << 5 | register:
                    destination = _address(words[j], words[j+1])
                    after = words[j+2:i+7]
                    if any(not ins.isValid() or (ins.hasDelaySlot() and not ins.doesLink())
                           or (ins.modifiesRt() and ins.rt.value == register)
                           or (ins.modifiesRd() and ins.rd.value == register)
                           or (ins.modifiesRs() and ins.rs.value == register)
                           for ins in (rabbitizer.Instruction(word) for word in after)):
                        destination = None
            if destination is None or not 0x80000400 <= destination < destination+end-source <= 0x80800000:
                continue
            delta = destination-source
            if not any(source <= s.rom_start < s.rom_end <= end and s.vram-s.rom_start == delta for s in segments):
                continue
            pc = segment.vram+4*(i+9)
            target = ((pc+4)&0xf0000000) | (call&0x03ffffff)<<2
            if target not in checked:
                checked[target] = _request_body(image, target, segments, cpu)
            if checked[target]:
                result.add((source,end,destination))
    return [dict(rom_start=lo,rom_end=hi,vram=base,target_prefix=base & 0xe0000000,
                 code_delta=base-lo,scoped_copy=True) for lo,hi,base in sorted(result)]
