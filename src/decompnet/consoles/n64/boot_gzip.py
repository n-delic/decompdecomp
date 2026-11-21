"""Programs inflated by a boot loader from two padded gzip text/data pairs."""

import hashlib
import zlib
from itertools import pairwise
from pathlib import Path

import yaml

from ...arch.mips_assets import read

MAX_MEMBER = 4 * 1024 * 1024

# This linker layout places material and animation index tables in the gzip
# text member. Their consumers load them as data. Match the full audited spans
# so versions that change a table still go through the normal boundary review.
INLINE_TABLES = (
    (bytes.fromhex('07aa01010003ffffffff00200020000a'), 0x8b0,
     'dfdd210625ae3bc5e40b86cd8e9f8d49191034b39213eb46e833354f006eb4ad'),
    (bytes.fromhex('00000008000000000000000403020100'), 0xf50,
     '7f97f48068f05b0b54db329af8f03767e9254c518364b9a030473b3cbb7d003a'),
)


def _constant(value):
    if value and value['kind'] == 'constant':
        return value['value'] & 0xffffffff
    return None


def _member(rom, start, end):
    if not rom.startswith(b'\x1f\x8b\x08', start, end):
        return None
    source = rom[start:end]
    stream = zlib.decompressobj(31)
    try:
        data = stream.decompress(source, MAX_MEMBER + 1)
    except zlib.error:
        return None
    if stream.eof and 0 < len(data) <= MAX_MEMBER and len(data) % 16 == 0:
        return end - len(stream.unused_data), data
    return None


def _pair(rom, start, end):
    from .code import _looks_like_code

    if not (text := _member(rom, start, end)) or not _looks_like_code(text[1]):
        return None
    if not (data := _member(rom, text[0], end)):
        return None
    padding = rom[data[0]:end]
    if len(padding) < 16 and not any(padding):
        return text[1], data[1]
    return None


def _calls(events):
    for event in events:
        if event['kind'] == 'call':
            yield event, tuple(_constant(arg) for arg in event['args'][:4])


def _bounds(events, after):
    stores = [event for event in events if event['kind'] == 'store' and event['width'] == 4
              and after < event['instruction'] < after + 0x80]
    for first, second in pairwise(stores):
        place = _constant(first['place'])
        start, end = _constant(first.get('value')), _constant(second.get('value'))
        if (place is not None and 0x80000000 <= place < 0x80800000
                and _constant(second['place']) == place + 4 and first['block'] == second['block']
                and start is not None and end is not None):
            yield start, end


def find(rom, boot_ranges):
    """Use the boot DMA, inflater arguments and saved ROM bounds to identify both programs."""
    matches = {}
    for start, end, base in boot_ranges:
        if start >= 0x10000:
            continue
        events = read(base, rom[start:min(end, 0x10000)])['events']
        calls = list(_calls(events))
        for index, (event, (mode, source, scratch, _)) in enumerate(calls[:-2]):
            if mode != 0 or source is None or not 0 < source < len(rom) or scratch is None:
                continue
            if not rom.startswith(b'\x1f\x8b\x08', source):
                continue
            dma = event['instruction']
            for (inflate, args), (again, _) in pairwise(calls[index + 1:]):
                instruction = inflate['instruction']
                if instruction >= dma + 0x80:
                    break
                input_address, primary, secondary, _ = args
                if (input_address != scratch or primary is None or secondary is None
                        or again['instruction'] != instruction + 8 or again['target'] != inflate['target']
                        or not 0x80000000 <= scratch < secondary < primary < 0x80800000):
                    continue
                for midpoint, limit in _bounds(events, instruction):
                    if not source < midpoint < limit <= len(rom):
                        continue
                    first, second = _pair(rom, source, midpoint), _pair(rom, midpoint, limit)
                    if not first or not second:
                        continue
                    ends = primary + sum(map(len, first)), secondary + sum(map(len, second))
                    if ends[0] > 0x80800000 or ends[1] > primary:
                        continue
                    if any(address < boot_base + stopped - begun and boot_base < stop
                           for address, stop in zip((primary, secondary), ends)
                           for begun, stopped, boot_base in boot_ranges):
                        continue
                    matches[primary, secondary, source, midpoint, limit] = (
                        (primary, *first), (secondary, *second))
    return next(iter(matches.values())) if len(matches) == 1 else None


def verify_analysis(rom, expanded, boot_ranges):
    """Verify the original compressed members against the separately linked program bytes."""
    programs = find(rom, boot_ranges)
    if programs is None or expanded[:len(rom)] != rom:
        return False
    cursor = len(rom)
    for _, text, data in programs:
        for payload in (text, data):
            if expanded[cursor:cursor + len(payload)] != payload:
                return False
            cursor += len(payload)
    return cursor == len(expanded)


def inline_tables(text):
    ranges = []
    for prefix, size, digest in INLINE_TABLES:
        at = text.find(prefix)
        while at >= 0:
            end = at + size
            if at % 4 == 0 and end < len(text) and hashlib.sha256(text[at:end]).hexdigest() == digest:
                ranges.append((at, end))
            at = text.find(prefix, at + 4)
    return sorted(ranges)


def add(config, workdir, image, rom, boot_ranges, log):
    if not (programs := find(rom, boot_ranges)):
        return None
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = cfg['segments']
    segments.pop()
    expanded = bytearray(rom)
    for vram, text, data in programs:
        start = len(expanded)
        name = f'program_{vram:08X}'
        parts = [[start, 'c', name]]
        for before, after in inline_tables(text):
            parts.extend([[start + before, 'rodata', f'{name}_table_{before:X}'],
                          [start + after, 'c', f'{name}_text_{after:X}']])
        parts.append([start + len(text), 'data', name])
        segments.append(dict(name=name, type='code', start=start, vram=vram, subsegments=parts))
        expanded.extend(text)
        expanded.extend(data)
    segments.append([len(expanded)])
    filename = f'{Path(image).stem}.unpacked{Path(image).suffix}'
    (workdir / filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    log('Unpacked two gzip programs at the addresses supplied by the boot loader')
    return filename
