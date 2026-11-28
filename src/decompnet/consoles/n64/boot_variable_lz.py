"""Recover a size-prefixed boot program with variable-length LZ commands."""
import struct

from ...arch.mips_assets import read

_LIMIT = 4 * 1024 * 1024


def decode(source, expected):
    """Decode literals and backwards copies, rejecting incomplete or unsafe streams."""
    if not 0 < expected <= _LIMIT:
        return None
    at = distance = length = prefixes = 0
    out = bytearray()
    while len(out) < expected and at < len(source):
        command = source[at]
        at += 1
        if command < 0x80:
            back = (distance << 4 | command & 15)+1
            count = (length << 3)+(command >> 4 & 7)+prefixes+2
            if back > len(out) or count > expected-len(out):
                return None
            for _ in range(count):
                out.append(out[-back])
            distance = length = prefixes = 0
        elif command < 0x90:
            count = (length << 4)+(command & 15)+1
            if count > expected-len(out) or at+count > len(source):
                return None
            out.extend(source[at:at+count])
            at += count
            length = prefixes = 0
        elif command < 0xa0:
            length, distance = command & 3, (command & 12) >> 2
            prefixes += 1
        elif command < 0xc0:
            length = (length << 5)+(command & 31)
            prefixes += 1
        else:
            distance = (distance << 6)+(command & 63)
            prefixes += 1
        # Prefixes can never validly describe a distance or a length beyond
        # the output bound. This also bounds integer growth on malformed input.
        if distance > _LIMIT or length > _LIMIT or prefixes > 32:
            return None
    return (bytes(out), at) if len(out) == expected else None


def find(rom, boot_ranges):
    """Require the decoder's code, size load and entry jump before decoding."""
    from .code import _looks_like_code

    # Distinct command branches in the loader establish the format; bounds and
    # an executable output are checked independently below.
    signatures = (
        bytes.fromhex('91040000000416000440001725080001000519003082000f00432827000618c0'),
        bytes.fromhex('2c8200901040000e000611003083000f004310212446000150c0001900004821'),
        bytes.fromhex('2c8200a0104000043082000c308600030808016b000228822c8200c010400004'),
    )
    found = []
    for lo, hi, base in boot_ranges:
        if lo >= 0x10000 or hi > len(rom) or hi-lo < 128:
            continue
        code = rom[lo:hi]
        positions = [code.find(signature) for signature in signatures]
        if min(positions) < 0 or positions != sorted(positions):
            continue
        events = read(base, code)['events']
        jumps = [event for event in events if event['kind'] == 'call'
                 and isinstance(event.get('target'), int) and 0x80000400 <= event['target'] < 0x80400000
                 and not base <= event['target'] < base+hi-lo]
        loads = [event for event in events if event['kind'] == 'load' and event.get('width') == 4
                 and (event.get('place') or {}).get('kind') == 'constant']
        for jump in jumps:
            target = jump['target']
            for load in loads:
                if not load['instruction'] < base+positions[0] < jump['instruction']:
                    continue
                address = load['place']['value']
                offset = lo+address-base
                if not lo <= offset <= len(rom)-4 or offset % 4:
                    continue
                expected, = struct.unpack_from('>I', rom, offset)
                result = decode(rom[offset+4:offset+4+_LIMIT], expected)
                if not result or target+expected > 0x80800000:
                    continue
                data, _ = result
                if not _looks_like_code(data):
                    continue
                returns = [i for i in range(0, len(data)-7, 4) if data[i:i+4] == b'\x03\xe0\x00\x08']
                if returns:
                    end = returns[-1]+8
                    found.append(((target, data[:end], data[end:]),))
    return found[0] if len(found) == 1 else None


def startup_sections(text, vram):
    """Keep the startup instructions and its early data ahead of called code."""
    from ...arch.mips import is_frame, jal_targets
    from .code import _looks_like_code

    if len(text) < 0x4000 or len(text) % 4:
        return None
    words = struct.unpack(f'>{len(text)//4}I', text)
    # A stack-initialising entry ends in a deliberate trap after its main call.
    if words[0] >> 16 != 0x3c1d:
        return None
    traps = [i for i in range(4, min(len(words), 128)) if words[i] & 0x3f == 0x0d and words[i] >> 26 == 0]
    if not traps:
        return None
    startup_end = (traps[0]+1)*4
    calls = set(jal_targets(words, vram & 0xf0000000))
    for offset in range((startup_end+3) & ~3, len(text)-0x4000, 4):
        if (vram+offset not in calls or not is_frame(words[offset//4])
                or not any(word >> 16 in (0xafbf, 0xafb0, 0xafb1) for word in words[offset//4+1:offset//4+8])
                or 0x03e00008 not in words[offset//4+1:offset//4+128]):
            continue
        if _looks_like_code(text[offset:offset+0x4000]):
            return startup_end, offset
    return None
