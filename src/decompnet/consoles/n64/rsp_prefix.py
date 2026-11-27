"""Keep RSP vector microcode out of inferred CPU code segments."""

import re
import struct

from ...arch.mips import is_frame, jal_targets
from ...arch.mips_code_recovery import called_extent, leaf_extent

_MAX_PREFIX = 0x4000
_MIN_VECTORS = 64


def _vector(word):
    return word >> 25 == 0x25  # RSP COP2 vector arithmetic



def audio_spans(image):
    """Find vector-rich audio RSP programs with a known task entry."""
    from .port import RSPBOOT

    variant = bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    pattern = re.escape(RSPBOOT) + b'|' + re.escape(variant)
    # Some games keep the bootstrap elsewhere. The task entry still reads SP
    # status and the two DMEM task fields; a complete vector-rich IMEM image
    # and several IMEM-local jumps distinguish it from CPU code.
    entry = re.compile(rb'\x40[\x00-\x1f]\x58\x00\x8c[\x20-\x3f]\x00\x30\x8c[\x20-\x3f]\x00\x34')
    for match in entry.finditer(image):
        start = match.start()
        end = start + 0x1000
        if start % 16 or end > len(image):
            continue
        if start >= 0xd0 and (image[start-0xd0:start-0xd0+len(RSPBOOT)] in (RSPBOOT, variant)):
            continue
        if re.search(pattern, image[start:end]):
            continue
        words = struct.unpack_from('>1024I', image, start)
        targets = {word & 0x03ffffff for word in words if word >> 26 in (2, 3)}
        if (sum(map(_vector, words)) >= _MIN_VECTORS
                and sum(0x01000400 <= target < 0x01000800 for target in targets) >= 3):
            yield start, end
    for match in re.finditer(pattern, image):
        end = match.start()
        # Some audio tasks place the bootstrap immediately before their
        # 4 KiB text. Its entry initializes DMEM command pointers or reads SP status first.
        start = end + 0xd0
        stop = start + 0x1000
        if stop <= len(image) and start % 16 == 0:
            status, data, size = struct.unpack_from('>3I', image, start)
            pointers_first = image[start:start + 16] == bytes.fromhex('2018036020170f908c3c00308c3b0034')
            status_first = (status & 0xffe0ffff == 0x40005800 and data & 0xffe0ffff == 0x8c200030
                            and size & 0xffe0ffff == 0x8c200034)
            if pointers_first or status_first:
                words = struct.unpack_from('>1024I', image, start)
                branches = {word & 0x03ffffff for word in words if word >> 26 in (2, 3)}
                if (sum(map(_vector, words)) >= _MIN_VECTORS
                        and sum(0x01000400 <= target < 0x01000800 for target in branches) >= 3):
                    # The verified vector program follows libultra's 0xd0-byte RSP bootstrap. The bootstrap is
                    # RSP code too: treating it as CPU text invents a CP0 assembly function before the audio.
                    yield end, stop
        if end % 16:
            continue
        for start in range(max(0, end - 0x1000), end - 0x100, 16):
            status, data, size = struct.unpack_from('>3I', image, start)
            # The first operations read SP_STATUS and the two task DMEM
            # fields for its command list.
            status_first = (status & 0xffe0ffff == 0x40005800 and data & 0xffe0ffff == 0x8c200030
                            and size & 0xffe0ffff == 0x8c200034)
            pointers_first = image[start:start+16] == bytes.fromhex('2018036020170f908c3c00308c3b0034')
            if not (status_first or pointers_first):
                continue
            words = struct.unpack_from(f'>{(end-start)//4}I', image, start)
            if sum(map(_vector, words)) < _MIN_VECTORS:
                continue
            branches = {word & 0x03ffffff for word in words if word >> 26 in (2, 3)}
            if sum(0x01000400 <= target < 0x01000800 for target in branches) >= 3:
                yield start, end
                break


def graphics_spans(image):
    """Find a bootstrap variant and its vector-rich 4 KiB graphics program."""
    from .port import RSPBOOT

    # This bootstrap also precedes graphics text whose first instruction
    # jumps into IMEM; the vector body corroborates the entire task image.
    for match in re.finditer(re.escape(RSPBOOT), image):
        start = match.start()
        code = start + 0xd0
        end = code + 0x1000
        if start % 16 or end > len(image):
            continue
        words = struct.unpack_from('>1024I', image, code)
        first = words[0]
        targets = {word & 0x03ffffff for word in words if word >> 26 in (2, 3)}
        if (first >> 26 == 2 and 0x01000400 <= first & 0x03ffffff < 0x01000800
                and sum(map(_vector, words)) >= _MIN_VECTORS
                and sum(0x01000400 <= target < 0x01000800 for target in targets) >= 3):
            yield start, end

    # Some bootstrapped graphics programs fill the 4 KiB task image with
    # vector code and continue with an IMEM overlay. Its final branch and
    # vector delay slot, zero alignment and a separately framed CPU routine
    # establish where the processor changes without guessing a code boundary.
    entry = bytes.fromhex('4a00002cc81f201bc81e201c')
    for match in re.finditer(re.escape(RSPBOOT), image):
        start = match.start()
        code = start+0xd0
        end = code+0x1000
        if start % 16 or end+0x100 >= len(image) or image[code:code+len(entry)] != entry:
            continue
        words = struct.unpack_from('>1024I', image, code)
        targets = {word & 0x3ffffff for word in words if word >> 26 in (2, 3)}
        if (sum(map(_vector, words)) < _MIN_VECTORS
                or sum(0x400 <= target < 0x800 for target in targets) < 3
                or words[-1] >> 26 != 2 or not 0x400 <= words[-1] & 0x3ffffff < 0x800):
            continue
        for at in range(end+0x100, min(end+0x800, len(image)-40), 4):
            frame = int.from_bytes(image[at:at+4], 'big')
            if not is_frame(frame):
                continue
            following = struct.unpack_from('>8I', image, at+4)
            if not any(word & 0xffff0000 == 0xafbf0000 for word in following):
                continue
            if not called_extent(image, at, min(at+0x400, len(image)), 0x80000000):
                continue
            for padding in range(2, 9):
                jump_at = at-4*(padding+2)
                jump, delay = struct.unpack_from('>2I', image, jump_at)
                if (jump_at < end or jump >> 26 != 2 or not 0x400 <= jump & 0x3ffffff < 0x800
                        or not _vector(delay) or any(image[jump_at+8:at])):
                    continue
                tail = struct.unpack_from(f'>{(jump_at-end)//4}I', image, end)
                if sum(map(_vector, tail)) >= _MIN_VECTORS:
                    yield start, at
                    break
            else:
                continue
            break

    signature = bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    for match in re.finditer(re.escape(signature), image):
        start = match.start()
        code = start + 0xd0
        end = code + 0x1000
        if start % 16 or end > len(image):
            continue
        words = struct.unpack_from('>1024I', image, code)
        branches = {word & 0x03ffffff for word in words if word >> 26 in (2, 3)}
        if (sum(map(_vector, words)) >= _MIN_VECTORS
                and sum(0x01000400 <= target < 0x01000800 for target in branches) >= 3):
            # A four-byte marker can precede the aligned bootstrap after a closed CPU return.
            # It belongs to the task data, not a one-instruction CPU function.
            if start >= 12 and image[start-12:start] == bytes.fromhex('03e00008000000006e736967'):
                start -= 4
            yield start, end

    # A graphics microcode family starts with vector setup. Its linked
    # overlays can extend past the 4 KiB task image, ending at the next
    # vector program or a separately recognizable CPU interrupt routine.
    entry = bytes.fromhex('4a00002cc81f201bc81e201c')
    overlay = bytes.fromhex('080007f6201f108c200c4000400b5800')
    entries = [match.start() for match in re.finditer(re.escape(entry), image)]
    for index, start in enumerate(entries):
        end = start+0x1000
        if start % 16 or end+len(overlay) > len(image) or image[end:end+len(overlay)] != overlay:
            continue
        words = struct.unpack_from('>1024I', image, start)
        targets = {word & 0x3ffffff for word in words if word >> 26 in (2, 3)}
        if (sum(map(_vector, words)) < _MIN_VECTORS
                or sum(0x400 <= target < 0x800 for target in targets) < 3):
            continue
        following = entries[index+1] if index+1 < len(entries) else 0
        if end < following <= end+0x800:
            tail = struct.unpack_from(f'>{(following-end)//4}I', image, end)
            if sum(map(_vector, tail)) >= _MIN_VECTORS:
                yield start, following
                continue
        for at in range(end+0x100, min(end+0x400, len(image)-20), 4):
            a, b, c, d, e = struct.unpack_from('>5I', image, at)
            if (a >> 16 == 0x3c0a and b >> 16 == 0x254a and c == 0x8d4b0000
                    and d == 0x316bff00 and e == 0x40086000
                    and b'\x03\xe0\x00\x08' in image[at:at+256]):
                yield start, at
                break
        else:
            # Some variants end their RSP overlay with a jump within IMEM,
            # its DMEM load delay slot and alignment, followed by a CPU frame.
            # The frame saves the return address and closes within its body.
            for at in range(end+0x100, min(end+0x400, len(image)-40), 4):
                jump, delay, pad0, pad1, frame = struct.unpack_from('>5I', image, at-16)
                if (jump >> 26 != 2 or not 0x400 <= jump & 0x3ffffff < 0x800
                        or delay != 0x8c1800cc or pad0 or pad1 or not is_frame(frame)):
                    continue
                following = struct.unpack_from('>8I', image, at+4)
                if (not any(word & 0xffff0000 == 0xafbf0000 for word in following)
                        or b'\x03\xe0\x00\x08' not in image[at:at+0x400]):
                    continue
                yield start, at
                break


def trim(image, found, known, log=print):
    """Trim a vector-code prefix only where scalar calls establish the CPU boundary."""
    ranges = [*known, *found]
    result = []
    for start, end, vram in found:
        words = struct.unpack_from(f'>{min(end-start, _MAX_PREFIX)//4}I', image, start)
        frame = next((i for i, word in enumerate(words) if is_frame(word)), None)
        if frame is None or sum(map(_vector, words[:frame])) < _MIN_VECTORS:
            result.append((start, end, vram))
            continue
        boundary = start+frame*4
        address = vram+frame*4
        scalar = [(boundary, end, address), *(r for r in ranges if r[0] != start)]
        targets = set()
        for lo, hi, base in scalar:
            targets.update(jal_targets(struct.unpack_from(f'>{(hi-lo)//4}I', image, lo), base))
        cut = _boundary(image, words, frame, start, end, vram, targets)
        if cut > start:
            log(f'Kept {cut-start} bytes of RSP microcode outside the inferred CPU code range.')
        result.append((cut, end, vram+cut-start))
    return result


def _boundary(image, words, frame, start, end, vram, targets):
    at = start+frame*4
    address = vram+frame*4
    if address not in targets or not called_extent(image, at, end, address):
        return start
    # A directly called leaf can be the first CPU function, before the first
    # stack frame. Require it to end exactly at that frame.
    for i in range(max(1, frame-16), frame):
        if _vector(words[i-1]) and vram+4*i in targets:
            candidate = start+4*i
            if leaf_extent(image, candidate, at, vram+4*i) == at-candidate:
                at, address = candidate, vram+4*i
                break
    if not _vector(words[(at-start)//4-1]) or any(vram <= target < address for target in targets):
        return start
    return at
