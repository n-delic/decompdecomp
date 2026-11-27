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
    (bytes.fromhex('8005a6848005a6848005a6648005a670'), 64,
     '89861ba83028659d467c7a8de5b65f5f4ec558f35c354c57fb3e6111645bab49'),
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


def _registered_overlays(rom, boot_events, primary, source, limit):
    """Follow the boot-initialized ROM ranges and resident program's destination stores."""
    target, text, _ = primary
    stores = {}
    for event in [*boot_events, *read(target, text)['events']]:
        if event['kind'] != 'store' or event['width'] != 4:
            continue
        place, value = _constant(event.get('place')), _constant(event.get('value'))
        if place is not None and value is not None:
            stores.setdefault(place, set()).add(value)

    def value(place):
        values = stores.get(place, set())
        return next(iter(values)) if len(values) == 1 else None

    anchors = [place for place in stores if value(place) == source and value(place + 4) == limit
               and value(place + 8) == target]
    if len(anchors) != 1:
        return ()
    overlays, seen = [], set()
    for i in range(1, 33):
        place = anchors[0] + i * 24
        lo, hi, destination = value(place), value(place + 4), value(place + 8)
        if (lo is None or hi is None or destination is None or not 0 < lo < hi <= len(rom)
                or not 0x80000000 <= destination < 0x80800000 or destination % 16
                or (lo, hi) in seen or not (pair := _pair(rom, lo, hi))):
            break
        text_data, data = pair
        if destination + len(text_data) + len(data) > 0x80800000:
            break
        seen.add((lo, hi))
        overlays.append((destination, text_data, data))
    # A shared runtime slot and a contiguous table distinguish overlays from
    # unrelated data records that happen to point to compressed members.
    if len(overlays) < 2 or len({destination for destination, _, _ in overlays}) != 1:
        return ()
    primary_end = target + len(primary[1]) + len(primary[2])
    if any(destination < primary_end and target < destination + len(code) + len(data)
           for destination, code, data in overlays):
        return ()
    return tuple(overlays)


def _pointer_find(rom, boot_ranges):
    """Bind a DMA source to the output pointer passed to a paired-member inflater."""
    matches = {}
    for start, end, base in boot_ranges:
        if start >= 0x10000:
            continue
        boot_events = read(base, rom[start:min(end, 0x10000)])['events']
        calls = list(_calls(boot_events))
        for (dma, args), (inflate, _) in pairwise(calls):
            source, limit, scratch = args[:3]
            if (source is None or limit is None or scratch is None or not 0 < source < limit <= len(rom)
                    or not 0x80000000 <= scratch < 0x80800000 or scratch % 16
                    or inflate['block'] != dma['block'] or not 0 < inflate['instruction'] - dma['instruction'] <= 32):
                continue
            pointers = inflate['args'][:2]
            if any(not arg or arg.get('kind') != 'stack' for arg in pointers):
                continue
            memory = {item['place']['offset']: _constant(item.get('value'))
                      for item in dma.get('memory', []) if item.get('place', {}).get('kind') == 'stack'}
            input_addr, output_addr = (memory.get(arg['offset']) for arg in pointers)
            if (input_addr != scratch or output_addr is None or output_addr % 16
                    or not 0x80000000 <= output_addr < 0x80800000 or output_addr == scratch):
                continue
            if not (pair := _pair(rom, source, limit)):
                continue
            text, data = pair
            if output_addr + len(text) + len(data) > 0x80800000:
                continue
            primary = (output_addr, text, data)
            matches[source, limit, output_addr] = (primary, *_registered_overlays(
                rom, boot_events, primary, source, limit))
    return next(iter(matches.values())) if len(matches) == 1 else None


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
    if matches:
        return next(iter(matches.values())) if len(matches) == 1 else None
    pointer = _pointer_find(rom, boot_ranges)
    if pointer:
        return pointer
    from . import boot_brs, boot_lzss, boot_raw_deflate, boot_refpack, boot_rnc2, boot_two_bit
    return (boot_raw_deflate.find(rom, boot_ranges) or boot_brs.find(rom, boot_ranges)
            or boot_lzss.find(rom, boot_ranges) or boot_refpack.find(rom, boot_ranges)
            or boot_two_bit.find(rom, boot_ranges) or boot_rnc2.find(rom, boot_ranges))


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
    from . import boot_brs, boot_lzss, boot_rnc2, boot_two_bit
    from .boot_mapping import extend_startup_trap

    extend_startup_trap(config, rom, log)
    if boot_lzss.find(rom, boot_ranges):
        boot_lzss.extend_boot(config, rom, log)
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = cfg['segments']
    segments.pop()
    expanded = bytearray(rom)
    two_bit = boot_two_bit.find(rom, boot_ranges) or boot_rnc2.find(rom, boot_ranges)
    repeated = {vram for vram, _, _ in programs if sum(vram == other for other, _, _ in programs) > 1}
    replacing = any(boot_brs.replacement_entry(rom[0x1000:0x1010], text)
                    and vram < base + end - start and base < vram + len(text) + len(data)
                    for vram, text, data in programs for start, end, base in boot_ranges)
    if replacing:
        for segment in segments:
            if isinstance(segment, dict) and segment.get('type') == 'code':
                segment.update(exclusive_ram_id='replaced_boot')
    for index, (vram, text, data) in enumerate(programs):
        start = len(expanded)
        name = f'program_{vram:08X}_{index}' if vram in repeated else f'program_{vram:08X}'
        if replacing and boot_brs.replacement_entry(rom[0x1000:0x1010], text):
            # The loader keeps the boot entry's first two words and replaces
            # the rest; these image bytes are not executed at this address.
            segments.append(dict(type='bin', start=start))
            expanded.extend(text[:8])
            start += 8
            vram += 8
            text = text[8:]
        sections = boot_lzss.startup_sections(text, vram) if boot_lzss.find(rom, boot_ranges) else None
        two_bit_start = boot_two_bit.code_start(text) if two_bit and any(vram == p[0] for p in two_bit) else None
        if two_bit_start:
            parts = [[start, 'data', f'{name}_startup_data'], [start + two_bit_start, 'c', name]]
        elif sections:
            entry_end, code_start = sections
            parts = [[start, 'hasm'], [start + entry_end, 'data', f'{name}_startup_data'],
                     [start + code_start, 'c', name]]
        else:
            parts = [[start, 'c', name]]
        for before, after in inline_tables(text):
            parts.extend([[start + before, 'rodata', f'{name}_table_{before:X}'],
                          [start + after, 'c', f'{name}_text_{after:X}']])
        parts.append([start + len(text), 'data', name])
        segment = dict(name=name, type='code', start=start, vram=vram, subsegments=parts)
        if repeated or replacing:
            segment.update(exclusive_ram_id='replaced_boot' if replacing else f'gzip_slot_{vram:08X}', symbol_name_format=f'$VRAM_{name}',
                           symbol_name_format_no_rom=f'$VRAM_{name}')
        segments.append(segment)
        expanded.extend(text)
        expanded.extend(data)
    segments.append([len(expanded)])
    filename = f'{Path(image).stem}.unpacked{Path(image).suffix}'
    (workdir / filename).write_bytes(expanded)
    cfg['options']['target_path'] = filename
    cfg['sha1'] = hashlib.sha1(expanded).hexdigest()
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    log(f'Unpacked {len(programs)} compressed program(s) at the addresses supplied by the boot loader')
    return filename
