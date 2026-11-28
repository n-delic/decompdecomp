"""Recover a callback whose scheduled leading load follows non-code bytes."""

import struct
from collections import defaultdict
from dataclasses import replace

from ..arch.mips import JR_RA, is_frame
from ..arch.mips_assets import read
from ..arch.mips_code_recovery import called_extent
from .split import DataSymbol


def recover(functions, data, image, console, segments, explicit, *, check=lambda: None):
    """Require a shared callback slot and closed sibling functions on one mapping."""
    order = 'big' if console.big_endian else 'little'
    ordered = sorted(functions, key=lambda fn: fn.rom)
    by_start = {(fn.rom, fn.vram): fn for fn in functions}
    candidates = []
    for previous, head, body in zip(ordered, ordered[1:], ordered[2:]):
        if (head.piece or body.piece or head.entry_aliases or body.entry_aliases
                or head.size not in (8, 12, 16) or body.size < 16
                or previous.size < 8 or previous.lines[-2].word != JR_RA
                or previous.rom+previous.size != head.rom or previous.vram+previous.size != head.vram
                or head.rom+head.size != body.rom or head.vram+head.size != body.vram
                or head.vram-head.rom != body.vram-body.rom or not is_frame(body.lines[0].word)):
            continue
        entry = body.vram-4
        load = head.lines[-1].word
        # A scheduled load of an argument from $gp, immediately before a frame.
        if load >> 21 != 0x47c or not 4 <= (load >> 16) & 31 <= 7:
            continue
        if (any(head.vram <= a < entry for a in explicit) or body.vram in explicit
                or any(head.vram <= a <= body.vram for a in head.labels | body.labels)
                or any(d.rom is not None and d.rom < body.rom+body.size and head.rom < d.rom+d.size for d in data)
                or image[head.rom:body.rom+body.size] != b''.join(
                    line.word.to_bytes(4, order) for fn in (head, body) for line in fn.lines)):
            continue
        owners = [s for s in segments if s.rom_start <= head.rom < body.rom+body.size <= s.rom_end
                  and s.vram-s.rom_start == head.vram-head.rom]
        if len(owners) != 1 or sum(s.contains(entry, body.size+4) for s in segments) != 1:
            continue
        size = called_extent(image, body.rom-4, body.rom+body.size, entry,
                             big_endian=console.big_endian, cpu=console.cpu_variant,
                             asm_check=console.asm_reason)
        if size == body.size+4:
            candidates.append((head, body, entry, owners[0]))
    if not candidates:
        return functions

    protected = {value for head, body, entry, _ in candidates
                 for value in (*range(head.vram, entry, 4), body.vram)}
    # Full pointers and actual transfers protect independently entered words.
    fmt = '>I' if console.big_endian else '<I'
    protected_hits = {w for w, in struct.iter_unpack(fmt, image[:len(image)//4*4]) if w in protected}
    for fn in functions:
        for line in fn.lines:
            op = line.word >> 26
            if op in (2, 3):
                target = ((line.vram+4) & 0xf0000000) | ((line.word & 0x3ffffff) << 2)
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (line.word >> 21) & 31 == 8:
                delta = line.word & 0xffff
                target = line.vram+4+(delta-0x10000 if delta & 0x8000 else delta)*4
            else:
                continue
            if target in protected:
                protected_hits.add(target)

    highs = {value >> 16 for _, _, value, _ in candidates}
    highs |= {(value+0x8000) >> 16 for _, _, value, _ in candidates}
    slots = defaultdict(set)
    incomplete = False
    for fn in functions:
        check()
        if not any(line.word >> 26 == 15 and line.word & 0xffff in highs for line in fn.lines):
            continue
        trace = read(fn.vram, image[fn.rom:fn.rom+fn.size], big_endian=console.big_endian,
                     cpu=console.cpu_variant, check=check)
        incomplete |= trace['limited']
        for event in trace['events']:
            value = event.get('value') or {}
            for item in (value, event.get('place') or {}, *(event.get('args') or [])):
                if item and item.get('kind') == 'constant' and item.get('value') in protected:
                    protected_hits.add(item['value'])
            if event.get('kind') != 'store' or value.get('kind') != 'constant':
                continue
            at = fn.rom + event['instruction']-fn.vram
            word = int.from_bytes(image[at:at+4], order)
            if word >> 21 == 0x57c:  # sw through $gp
                slots[word & 0xffff].add(value['value'])

    if incomplete:
        return functions
    replaced, removed = {}, set()
    for head, body, entry, owner in candidates:
        if any(a in protected_hits for a in (*range(head.vram, entry, 4), body.vram)):
            continue
        load = head.lines[-1].word
        peers = set()
        for values in slots.values():
            if entry not in values:
                continue
            for value in values - {entry}:
                peer = by_start.get((value-owner.vram+owner.rom_start, value))
                if (peer is None or peer.size != body.size+4 or len(peer.lines) < 2
                        or peer.lines[0].word & 0xffff0000 != load & 0xffff0000
                        or peer.lines[1].word != body.lines[0].word):
                    continue
                size = called_extent(image, peer.rom, peer.rom+peer.size, peer.vram,
                                     big_endian=console.big_endian, cpu=console.cpu_variant,
                                     asm_check=console.asm_reason)
                if size == peer.size:
                    peers.add(value)
        if len(peers) < 2 or entry in {fn.vram for fn in functions}:
            continue
        data.append(DataSymbol(head.name, head.vram, head.rom, head.size-4, 'data', boundary_proven=True))
        replaced[id(head)] = replace(head, name=f'func_{entry:08X}', vram=entry, rom=body.rom-4,
                                     lines=head.lines[-1:]+body.lines, labels=body.labels)
        removed.add(id(body))
    return [replaced.get(id(fn), fn) for fn in functions if id(fn) not in removed]
