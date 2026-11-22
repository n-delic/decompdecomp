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
    """Find audio RSP programs adjacent to the verified libultra bootstrap."""
    from .port import RSPBOOT

    variant = bytes.fromhex('342100010900041a') + RSPBOOT[4:]
    pattern = re.escape(RSPBOOT) + b'|' + re.escape(variant)
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
                    yield start, stop
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
            yield start, end


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
