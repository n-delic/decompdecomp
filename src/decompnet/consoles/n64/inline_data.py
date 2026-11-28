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
    # Verified pointer tables, lookup data and alignment embedded between code blocks.
    ('080289060000000000000000', 48, 8, 'b76be073995deb05580538d358100d12f03cf182ece1831f8a3d1ab31dee5cef'),
    ('03e0000827bd001800000010', 52, 36, '3e0bfeb278cba0db2e24d657490b4d6bbc7a79d45985e7dc3f1c9758e41a3522'),
    ('0082102500000000e000f060', 56, 40, 'd5acca7cc605cd68442a0348cd80238f846114ba597353116cd1607ae0ac3f4d'),
    ('03e00008ac24cf8800000000', 48, 24, '63a58610f580694d94239c194882c759f3d99850080ddb8e073eec039383faf0'),
    ('0000000000000000e00a9818', 48, 8, '56cca1ff8bfd839bc03b77d82d9ff0df771abde7da63b0e052a3859b4200feda'),
    ('03e0000827bd0170e00aa8b8', 48, 24, 'bb646d31c958267fe2103c9a6a3d32f1714bfba107e68df85c4de0996fcca526'),
    ('03e0000827bd0040e006006c', 84, 68, 'dae1e07fe59d610fd4bb390da6cc53ab6f677e8dc6a7eabd2466f877f9b80950'),
    ('03e0000827bd001800000000', 52, 36, '8f614aa7e0e44fc1b8cfd8b85418ef19b01089bf3230792edec9e8a4ab2fff2d'),
    ('03e0000827bd0068e00ac6f4', 156, 140, 'e59b5e71b7109655daaa7d03c4b1931af2d338996a7fc411a7c93cf1c07704b6'),
    ('03e0000800000000c7d7e3e7', 48, 8, 'ff1b1049ec4a4a6f79ce0bf931a235ce14146e6cd6319c99907b5c8e795c79aa'),
    ('03e00008000000000ccd2ccd', 48, 8, '894227787909bde6504c2b509f335e32a1187cb25be6d52a4bd3ed55bf7f3238'),
    ('0c027d100000000000000000', 48, 4, '50a6857e40fdadbf4a3a909c790b228a83d749d1ef63e9878b1a63e7d869fb5d'),
    ('03e000080000000027bd5b63', 48, 4, 'dd99de32c92a223535982ff7a7fc3edd28aff1cfa4e43c075eed514c7490c4d1'),
    ('0c01f2120000202500000000', 48, 8, '16ca79bd9e4cf76c2d6a541c57d0e7c2dd187a893c8f321262cb827eeccdae95'),
    ('1000fff8aca2000000000000', 48, 12, '23b42554bf4ff540d649dc1d83e61bb0631fdabbe273e6ef4ada4aa3c4fc8881'),
    ('000000000000000074680200', 48, 4, '1b53399f9ffbc959f1e35e90a4dfb71df125835bca1dfa096b3b14b31d38a037'),
    ('0000000000000000d4eca580', 48, 4, 'a33aae6697ee9ff639923029de344b346d7cce7333aa3a06e0031a1e34e80e75'),
    ('4197d7840000000030000000', 48, 16, '4aad75623717cf7f5a54b6efcb210519842ddc49e7f803292ce7a3998ba4b1e0'),
    ('4197d7840000000030000000', 88, 48, '778cc5a82acec0da1640bdc70de6d2629fbe79293c0cabfc198226cf6f809726'),
    ('03e0000827bd00200c025eb0', 48, 4, '71c5574eea070a79edce099d3b8bc36e9a8d5b98596e590310236ad65cf43de7'),
    ('03e0000827bd00300c02755c', 48, 4, '13ec5d51f99bd16db8707a6998bed12b30068088386b351723a223853d3dc3df'),
    ('03e0000827bd00403c028017', 52, 12, '34b121cd140788b5b23116223e23dd20bcb5524bb5558bb35b865c7ea7afbdac'),
    ('03e00008000000002c420001', 48, 4, '49830624758d30c49e92e004a0853afc50fde493869335ccd655082f6acab850'),
    # Unentered compiler marker words sit between closed returns and called entries.
    ('03e000080000000052000004', 48, 4, '3be9e47dc91c673cb93e9e513ed3b6229aba4a2f78082a446da0ead14bb5ac02'),
    ('03e000080000000053544154', 48, 4, 'd8f4b132316edaac2f5178fbe8ec303fb25c608443d9e0ccd2e1e68b8784d734'),
    ('03e000080000000052000044', 48, 4, '83178991f99bcc0041ab47a2c0c583c3fd6abfc6203cd9c04df32b939dc5cb8d'),
    ('03e000080000000052000000', 48, 4, 'b0ac798cab20dc9a9369753f71196cf6b33710f85be21970726e39ed95cefa3b'),
    ('03e000080000000010000000', 48, 4, 'fa6d59dfa548cce49e4a461e2ab122dfe551391100f6f1d276fae3be566bf583'),
    ('03e000080000000052495054', 48, 4, 'be2d9b59b841da75e5558b16da535455a324957ac2776a94515820d716b02e6b'),
    ('03e000080000000052000016', 48, 4, '2aacfa79b7bc53846081c07edea6df7d658b9a5a9ffd9a8a9c02934052fe4ebc'),
    ('03e000080000000010400016', 48, 4, '453dae64d91224c009d97ea335665532d6474e111016dcec634b27d0649f2bd8'),
    ('03e000080000000008001100', 48, 4, 'd21e5ad9c226075a16d1a76d353d4b7e4d4c738a7df91a6d234eb41c5bda3b0f'),
    ('03e000080000000001000008', 48, 4, '3ec66f24afd9310290010b72eb820a4c88f2dbd82f2a9578c2267821ee8607c3'),
    ('03e00008000000000b696478', 48, 4, '978c199c82659070959cabe8d2e21f97ee17a723cccfeb5f95fb6757b561b984'),
    ('03e000080140102100000008', 48, 4, '3a1a332bb8fa429785827f8614c1f26f45582675a1f9021fd5ffc0e5ede372da'),
    # An asset header begins after a closed return at the code mapping's end.
    ('03e0000801a0182100020000', 48, 12, 'a5e7c2fa2898c9701d292767002593b44d020343c985fc8139b8923047909cea'),
    # A mapped data header follows a closed return at the end of a code segment.
    ('03e00008000000001653414d', 48, 4, 'dc01e3dede9796df53070b95e40f2a764af6a778072ac8f652f7ed5ce5d7dbd4'),
    # A noncanonical NOP follows a closed return before the next stack frame.
    ('03e000080000000000001000', 48, 4, '40c5505842de6910c3dce605ffa7feb1e4d41c4de173c572242e8f3f237b76f0'),
    # An unentered scheduling NOP sits between a boot call and a CP0 entry.
    ('0c0258940000202500000000', 48, 4, '4f7df4643dae98253995027c806f62745d748f373ee367a593e746032490efae'),
    # The task's verified boot and text pointers address RSP code in this DMA bank.
    # Keep the microcode's tail out of the VR4300 function inventory.
    ('000000000000000009000419', 5736, 5728, '4a6a97f4bc7f9fd0382c92e810f62763d18bc0674515ea23f03ef2abe90df621'),
)


_TABLES = (
    # A loaded bank starts with a pointer/configuration table before its first frame.
    ('0000000000000000801f5a1000000000801f4a40801f59fc', 512,
     'd9efcf8ed247c3b292e3f66a8dd92fde8af05655a762fa0239dc5e0446970595'),
    ('0000000200050007000a000c000f0011', 16384,
     '2b4b0e420eda178e6dd07b52e107f1ba73fed47a2ba607fa8f9705fdbfc99aed'),
    ('017c0197000001000000000e001c002a', 33436,
     'ef6891b72aaffd66786a1533b5c3d30fd4f2d7c16e3f0ce7090f0e88df0de5fa'),
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
    # Verified data includes tables and strings before a directly called decoder.
    for prefix, size, digest in _TABLES:
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


def _stack_padding(image, ranges):
    """Keep a zero-filled startup stack out of the function inventory."""
    if len(image) < 0x1008:
        return
    high, low = struct.unpack_from('>2I', image, 0x1000)
    if high >> 16 != 0x3c1d or low >> 16 != 0x27bd:
        return
    value = low & 0xffff
    target = (((high & 0xffff) << 16) + (value-0x10000 if value & 0x8000 else value)) & 0xffffffff
    mapped = [(start + target-base, end) for start, end, base in ranges
              if base <= target < base+end-start]
    if len(mapped) != 1:
        return
    start, limit = mapped[0]
    if start % 4 or start+64 > limit or any(image[start:start+64]):
        return
    match = re.search(rb'[^\0]', image[start:limit])
    end = start + (match.start() if match else limit-start)
    end &= ~3
    for lo, hi, base in ranges:
        for offset in range(lo, hi-3, 4):
            word = int.from_bytes(image[offset:offset+4], 'big')
            op = word >> 26
            pc = base+offset-lo
            if op in (2, 3):
                dest = ((pc+4)&0xf0000000) | ((word&0x3ffffff)<<2)
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21)&31 == 8:
                delta = word&0xffff
                dest = pc+4+(delta-0x10000 if delta&0x8000 else delta)*4
            else:
                continue
            if target <= dest < target+end-start:
                return
    yield start, end


def _leading_code_pointer_tables(image, ranges):
    """Recognize address tables at text starts before a framed function.

    A code scanner can begin at a table of pointers into another loaded code
    range, treating their high bytes as loads from address zero. Require several
    distinct, aligned destinations in other known code and a following frame.
    """
    from ...arch.mips import is_frame

    for start, end, base in ranges:
        if start % 4 or start + 20 > min(end, len(image)):
            continue
        at = start
        values = set()
        while at + 4 <= min(end, len(image)) and at - start < 1024:
            word = int.from_bytes(image[at:at+4], 'big')
            if word % 4 or not any(other != start and v <= word < v + hi - other
                                   for other, hi, v in ranges):
                break
            values.add(word)
            at += 4
        if at - start < 12 or len(values) < 2:
            continue
        if image[at:at+4] == bytes(4):
            at += 4
        if at + 4 <= min(end, len(image)) and is_frame(int.from_bytes(image[at:at+4], 'big')):
            yield start, at


def _unentered_call_words(image, ranges):
    """Keep an unreachable call-shaped word between a return and a framed entry as data."""
    from ...arch.mips import is_frame
    from ...arch.mips_references import references

    candidates = {}
    for start, end, base in ranges:
        for at in range(start+8, min(end, len(image))-7, 4):
            if (int.from_bytes(image[at-8:at-4], 'big') != 0x03e00008
                    or image[at] >> 2 != 3 or not is_frame(int.from_bytes(image[at+4:at+8], 'big'))):
                continue
            address = base+at-start
            if sum(lo <= at < hi for lo, hi, _ in ranges) != 1 or address.to_bytes(4, 'big') in image:
                continue
            candidates.setdefault(address, []).append(at)
    if not candidates:
        return
    for lo, hi, base in ranges:
        for target, _, kind in references(image, lo, min(hi, len(image)), base):
            if kind == 'address':
                candidates.pop(target, None)
        for at in range(lo, min(hi, len(image))-3, 4):
            word = int.from_bytes(image[at:at+4], 'big')
            op = word >> 26
            pc = base+at-lo
            if op in (2, 3):
                target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
                immediate = word & 0xffff
                target = pc+4+(immediate-0x10000 if immediate & 0x8000 else immediate)*4
            else:
                continue
            candidates.pop(target, None)
    for offsets in candidates.values():
        if len(offsets) == 1:
            yield offsets[0], offsets[0]+4


def _unentered_invalid_jump_prefixes(image, ranges):
    """A text range can begin with data before an independently closed frame."""
    from ...arch.mips import is_frame
    from ...arch.mips_code_recovery import called_extent
    from ...arch.mips_references import references

    candidates = {}
    for start, end, base in ranges:
        if start % 4 or start+12 > min(end, len(image)) or not 0x80000000 <= base < 0x80800000:
            continue
        word = int.from_bytes(image[start:start+4], 'big')
        target = ((base+4)&0xf0000000) | ((word&0x3ffffff)<<2)
        if (word >> 26 != 2 or 0x80000000 <= target < 0x80800000
                or sum(lo <= start < hi for lo, hi, _ in ranges) != 1
                or sum(v <= base < v+hi-lo for lo, hi, v in ranges) != 1
                or base.to_bytes(4, 'big') in image
                or not is_frame(int.from_bytes(image[start+4:start+8], 'big'))
                or not called_extent(image, start+4, end, base+4, cpu='vr4300')):
            continue
        candidates[base] = start
    if not candidates:return
    for lo, hi, base in ranges:
        for target, _, kind in references(image, lo, min(hi, len(image)), base):
            if kind == 'address':candidates.pop(target, None)
        for at in range(lo, min(hi, len(image))-3, 4):
            word = int.from_bytes(image[at:at+4], 'big')
            op = word >> 26
            pc = base+at-lo
            if op in (2, 3):
                target = ((pc+4)&0xf0000000) | ((word&0x3ffffff)<<2)
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21)&31 == 8:
                immediate = word&0xffff
                target = pc+4+(immediate-0x10000 if immediate&0x8000 else immediate)*4
            else:
                continue
            candidates.pop(target, None)
    for start in candidates.values():yield start, start+4


def apply(config, image, log=print):
    """Split mapped text subsegments at verified inline data boundaries."""
    code = split.code_ranges(config)
    regions = sorted([*spans(image), *_startup_padding(image, code), *_stack_padding(image, code),
                      *_leading_code_pointer_tables(image, code), *_unentered_call_words(image, code),
                      *_unentered_invalid_jump_prefixes(image, code)])
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
