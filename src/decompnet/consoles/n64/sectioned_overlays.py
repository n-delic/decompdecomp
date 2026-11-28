"""Recover raw overlays from repeated ROM/RAM section descriptors."""

import json
import re
import struct
from dataclasses import dataclass
from itertools import pairwise

import yaml

from ...arch.mips import JR_RA, is_frame


@dataclass(frozen=True)
class Bank:
    start: int
    end: int
    base: int
    text_end: int
    bss_end: int


def _record(image, at):
    if at < 0 or at + 36 > len(image) or at % 4:
        return None
    start, end, base, text, text_end, data, data_end, bss, bss_end = struct.unpack_from('>9I', image, at)
    if (start % 16 or end % 16 or base % 16 or end > len(image) or end-start < 256
            or not 0x80000400 <= base < text_end <= data_end <= bss_end <= 0x80800000
            or text != base or data != text_end or bss != data_end or data_end-base != end-start
            or text_end % 4 or text_end-base < 128):
        return None
    return Bank(start, end, base, text_end, bss_end)


def _code(image, bank):
    """Require multiple plausible framed functions and returns in each claimed text section."""
    count = min((bank.text_end-bank.base)//4, 4096)
    words = struct.unpack_from(f'>{count}I', image, bank.start)
    return sum(is_frame(word) for word in words) >= 2 and words.count(JR_RA) >= 2


def read(image, taken):
    """Find one unambiguous set of contiguous banks described by a multi-entry directory."""
    candidates = []
    pattern = re.compile(rb'(?P<base>\x80[\x00-\x7f]..)(?P=base)', re.DOTALL)
    for hit in pattern.finditer(image):
        at = hit.start()-8
        first = _record(image, at)
        if not first or _record(image, at-36):
            continue
        rows = []
        pos = at
        while (row := _record(image, pos)) is not None and len(rows) < 512:
            rows.append(row)
            pos += 36
        ordered = sorted(rows, key=lambda bank: bank.start)
        if (len(rows) < 4 or len({bank.base for bank in rows}) != 1
                or any(a.end != b.start for a, b in pairwise(ordered))
                or any(a < bank.end and bank.start < b for bank in rows for a, b, _ in taken)
                or any(bank.start <= at < bank.end for bank in rows)
                or not all(_code(image, bank) for bank in rows)):
            continue
        candidates.append(ordered)
    # Independent tables may describe different contiguous groups. Refuse any
    # ambiguity instead of assigning two descriptors to the same ROM bytes.
    result = sorted((bank for group in candidates for bank in group), key=lambda bank: bank.start)
    if any(a.end > b.start for a, b in pairwise(result)):
        return []
    return result


def add(config, workdir, rom, resident, records, log):
    """Preserve raw data while assigning each overlay its own address scope."""
    from .code import insert_segments

    # A heuristic range can span both an overlay and genuine resident code.
    # Remove only the described bytes, retaining any evidence on either side.
    remaining = []
    for start, end, base in resident:
        parts = [(start, end)]
        for bank in records:
            pieces = []
            for lo, hi in parts:
                if hi <= bank.start or lo >= bank.end:
                    pieces.append((lo, hi))
                else:
                    if lo < bank.start:
                        pieces.append((lo, bank.start))
                    if bank.end < hi:
                        pieces.append((bank.end, hi))
            parts = pieces
        remaining.extend((lo, hi, base + lo-start) for lo, hi in parts)
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    insert_segments(config, cfg, remaining, len(rom))
    segments = {s['start']: s for s in cfg['segments'] if isinstance(s, dict)}
    for index, bank in enumerate(records):
        name = f'sectioned_overlay_{index:02d}'
        data = bank.start + bank.text_end-bank.base
        parts = [[bank.start, 'c', name]]
        if data < bank.end:
            parts.append([data, 'data', name])
        segments[bank.start] = dict(type='code', name=name, start=bank.start, vram=bank.base,
                                    subsegments=parts, exclusive_ram_id=f'sectioned_{bank.base:08X}',
                                    symbol_name_format=f'$VRAM_{name}',
                                    symbol_name_format_no_rom=f'$VRAM_{name}')
        if bank.end < len(rom):
            segments.setdefault(bank.end, dict(type='bin', start=bank.end))
    cfg['segments'] = [segments[key] for key in sorted(segments)] + [[len(rom)]]
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    (workdir/'analysis-coverage.json').write_text(json.dumps([
        f'Includes {len(records)} overlays from bounded section directory records.'
    ]), encoding='utf-8')
    log(f'Imported {len(records)} sectioned overlays at their declared addresses')
