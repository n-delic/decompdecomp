"""Audited inline data and padding that disassemble as MIPS instructions."""

import hashlib
import math
import re
import struct

import yaml

from ...rom import split

# Start with eight bytes of context, then verify the entire surrounding span.
# The three call-shaped words occur after closed returns and before actual
# entries; their apparent callees fall inside other routines' frames.
_LITERALS = (
    ('4197d7840000000030000000', 88, 48, '778cc5a82acec0da1640bdc70de6d2629fbe79293c0cabfc198226cf6f809726'),
    ('03e0000827bd00200c025eb0', 48, 4, '71c5574eea070a79edce099d3b8bc36e9a8d5b98596e590310236ad65cf43de7'),
    ('03e0000827bd00300c02755c', 48, 4, '13ec5d51f99bd16db8707a6998bed12b30068088386b351723a223853d3dc3df'),
    ('03e0000827bd00403c028017', 52, 12, '34b121cd140788b5b23116223e23dd20bcb5524bb5558bb35b865c7ea7afbdac'),
    ('03e00008000000002c420001', 48, 4, '49830624758d30c49e92e004a0853afc50fde493869335ccd655082f6acab850'),
    # The task's verified boot and text pointers address RSP code in this DMA bank.
    # Keep the microcode's tail out of the VR4300 function inventory.
    ('000000000000000009000419', 5736, 5728, '4a6a97f4bc7f9fd0382c92e810f62763d18bc0674515ea23f03ef2abe90df621'),
)


# A complete Q14 sine cycle is data even when its negative values decode as
# load-linked and store-conditional instructions.
_SINE = struct.pack('>4096h', *(round(16384 * math.sin(i * math.tau / 4096)) for i in range(4096)))


def spans(image):
    """Find verified non-CPU segments and unchanged inline data."""
    from .rsp_prefix import audio_spans, graphics_spans

    yield from audio_spans(image)
    yield from graphics_spans(image)
    # Long runs of zero and all-one words after a closed return are padding or
    # data, even when an all-one word happens to decode as an unaligned store.
    pattern = rb'\x03\xe0\x00\x08....((?:\x00\x00\x00\x00|\xff\xff\xff\xff){8,1024})'
    for match in re.finditer(pattern, image, re.S):
        start, end = match.span(1)
        if start % 4 == 0 and b'\xff\xff\xff\xff' in match[1]:
            yield start, end
    # Two all-one alignment words can also precede a scheduled address load
    # and stack frame. Their unaligned store decoding is not executable code.
    from ...arch.mips import is_frame

    for match in re.finditer(rb'\x03\xe0\x00\x08....(\xff{8})', image, re.S):
        start, end = match.span(1)
        if start % 4 or end + 12 > len(image):
            continue
        lui, load, frame = struct.unpack_from('>3I', image, end)
        register = (lui >> 16) & 31
        if (lui >> 26 == 15 and register and load >> 26 == 35
                and (load >> 21) & 31 == register and (load >> 16) & 31 == register
                and is_frame(frame)):
            yield start, end
    # The linker uses repeated marker records between executable blocks.
    # A closed return and the exact record layout bound these bytes.
    marker = rb'\x03\xe0\x00\x08....((?:\xff\xff\xff\xff)?(?:\x12\x34\x56\x78\x00{12}){1,8})'
    for match in re.finditer(marker, image, re.S):
        if match.start(1) % 4 == 0:
            yield match.span(1)
    # Verified halfword tables, bounded by the loader's resident image.
    tables = (
        ('0000000200050007000a000c000f0011', 16384,
         '2b4b0e420eda178e6dd07b52e107f1ba73fed47a2ba607fa8f9705fdbfc99aed'),
        ('017c0197000001000000000e001c002a', 524,
         '07250f6a5d08c4d43e1cf09803c23870b6e99792cf53f033a798054dc076a5bf'),
    )
    for prefix, size, digest in tables:
        for match in re.finditer(re.escape(bytes.fromhex(prefix)), image):
            start = match.start()
            if start % 4 == 0 and hashlib.sha256(image[start:start+size]).hexdigest() == digest:
                yield start, start+size
    for match in re.finditer(re.escape(_SINE[:24]), image):
        start = match.start()
        if start % 4 == 0 and image[start:start+len(_SINE)] == _SINE:
            yield start, start+len(_SINE)
    for prefix, context, size, digest in _LITERALS:
        for match in re.finditer(re.escape(bytes.fromhex(prefix)), image):
            start = match.start()
            if start % 4 == 0 and hashlib.sha256(image[start:start + context]).hexdigest() == digest:
                yield start + 8, start + 8 + size


def _startup_padding(image, ranges):
    """Find unentered zero alignment after a boot call and its terminal break."""
    from ...arch.mips import is_frame

    def mapping(offset):
        addresses = {base+offset-start for start, end, base in ranges if start <= offset < end}
        return next(iter(addresses)) if len(addresses) == 1 else None

    for start, end, base in ranges:
        if start < 12 or start+16 > end or image[start:start+12] != bytes(12):
            continue
        call, delay, stop, frame = struct.unpack_from('>4I', image, start-12)[0:3] + (struct.unpack_from('>I', image, start+12)[0],)
        caller = mapping(start-12)
        target = mapping(start+12)
        if (caller is None or target is None or call >> 26 != 3 or delay
                or stop & 0xfc00003f != 0x0d or not is_frame(frame)
                or ((caller+4) & 0xf0000000) | ((call & 0x3ffffff) << 2) != target):
            continue
        addresses = {base+offset-start for offset in range(start, start+12, 4)}
        if any(struct.pack('>I', address) in image for address in addresses):
            continue
        entered = False
        for lo, hi, vram in ranges:
            for offset in range(lo, hi-3, 4):
                word, = struct.unpack_from('>I', image, offset)
                pc = vram+offset-lo
                opcode = word >> 26
                if opcode in (2, 3):
                    dest = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                elif opcode in (1, 4, 5, 6, 7, 20, 21, 22, 23):
                    imm = word & 0xffff
                    dest = pc+4+(imm-0x10000 if imm & 0x8000 else imm)*4
                else:
                    continue
                if dest in addresses:
                    entered = True
                    break
            if entered:
                break
        if not entered:
            yield start, start+12


def apply(config, image, log=print):
    """Split mapped text subsegments at verified inline data boundaries."""
    code = split.code_ranges(config)
    regions = sorted([*spans(image), *_startup_padding(image, code)])
    if not regions:
        return
    ranges = {start: end for start, end, _ in code}
    config_data = yaml.safe_load(config.read_text(encoding='utf-8'))
    changed = 0
    for segment in config_data['segments']:
        if not isinstance(segment, dict) or segment.get('type') != 'code':
            continue
        subsegments = []
        for sub in segment.get('subsegments', []):
            if not isinstance(sub, list) or len(sub) < 3 or sub[1] != 'c' or sub[0] not in ranges:
                subsegments.append(sub)
                continue
            cursor, limit, name = sub[0], ranges[sub[0]], sub[2]
            pending = sub
            for start, end in regions:
                start, end = max(start, cursor), min(end, limit)
                if start >= end:
                    continue
                if start > cursor:
                    subsegments.append(pending)
                subsegments.append([start, 'rodata', f'{name}_data_{start:X}'])
                pending = [end, 'c', f'{name}_text_{end:X}']
                cursor = end
                changed += 1
            if cursor < limit:
                subsegments.append(pending)
        segment['subsegments'] = subsegments
    if changed:
        config.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding='utf-8')
        log(f'Mapped {changed} verified inline data spans outside executable text.')
