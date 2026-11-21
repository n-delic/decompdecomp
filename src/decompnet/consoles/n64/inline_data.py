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
    """Find unchanged fingerprints at word-aligned offsets."""
    for match in re.finditer(re.escape(_SINE[:24]), image):
        start = match.start()
        if start % 4 == 0 and image[start:start+len(_SINE)] == _SINE:
            yield start, start+len(_SINE)
    for prefix, context, size, digest in _LITERALS:
        for match in re.finditer(re.escape(bytes.fromhex(prefix)), image):
            start = match.start()
            if start % 4 == 0 and hashlib.sha256(image[start:start + context]).hexdigest() == digest:
                yield start + 8, start + 8 + size


def apply(config, image, log=print):
    """Split mapped text subsegments at verified inline data boundaries."""
    regions = sorted(spans(image))
    if not regions:
        return
    ranges = {start: end for start, end, _ in split.code_ranges(config)}
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
                if not cursor <= start < end <= limit:
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
