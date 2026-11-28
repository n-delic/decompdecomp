"""Review called code hidden by another overlay at the same runtime addresses."""

import struct
import tempfile
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from ...arch.mips import asm_reason, jal_targets
from ...arch.mips_code_recovery import MAX_BYTES, called_extent, leaf_extent
from ...rom.split import Segment


def observe(root, cfg, image, segments, functions, log, *, candidates=(), checkpoint=None):
    """Sample runtime locations and scoped callers without changing the project."""
    from . import emulator, loaded_code

    scoped = any(s.exclusive_ram_id for s in segments)
    if not scoped and not any(c.get('extent_bytes', 0) >= 64 for c in candidates):
        return []
    if not emulator.installed():
        log('Overlay runtime evidence was skipped because the reversing emulator is not installed.')
        return []
    source = Path(root) / (cfg['rom'].get('program') or cfg['rom']['file'])
    if not source.is_file():
        log('Overlay runtime evidence was skipped because the original game image is unavailable.')
        return []
    check = getattr(log, 'check_cancel', lambda: None)
    cancel = getattr(log, 'cancel_event', None)
    log('Checking whether candidate bytes and scoped overlay callers appear while the game runs.')
    with tempfile.TemporaryDirectory(prefix='decomp-scoped-audit-') as tmp:
        try:
            run = emulator.run(source, 1800, inputs=loaded_code.inputs(1800),
                               dumps={v: Path(tmp) / f'{v}.bin' for v in (60, 120, 300, 590, 1200, 1790)},
                               timeout=300, stop=cancel.is_set if cancel is not None else lambda: False)
        except emulator.EmulatorError as error:
            check()
            log(f'The game could not be sampled for overlay evidence: {error}')
            return []
        check()
        dumps = [path.read_bytes() for _, path in sorted(run.dumps.items()) if path.is_file()]
        if run.timed_out:
            log('The overlay sample stopped early; later game states were not checked.')
        if len(dumps) < 2:
            log('The game produced fewer than two RAM snapshots; no overlay candidate was corroborated.')
        progress = getattr(log, 'progress', lambda _: None)
        result = located(image, candidates, dumps, check=check, progress=progress)
        if result:
            log('Checking each observed entry for execution and matching bytes at the breakpoint.')
        pending = defaultdict(list)
        completed = 0
        for item in result:
            saved = checkpoint(item) if checkpoint else None
            if saved is None:
                pending[item['vram']].append(item)
            else:
                item.update(saved)
                completed += 1
        addresses = list(pending)
        # Each recorded address writes a complete RAM image. Bound scratch disk use while
        # checking every pending address in this run; completed pages survive cancellation.
        for index in range(0, len(addresses), 16):
            check()
            progress({'phase': 'Checking observed entries for execution', 'done': completed, 'total': len(result)})
            page = addresses[index:index + 16]
            capture = Path(tmp) / f'entries-{index}'
            capture.mkdir()
            try:
                trace = emulator.run(source, 1800, inputs=loaded_code.inputs(1800), breaks=page,
                                     record=(capture, 1), timeout=300,
                                     stop=cancel.is_set if cancel is not None else lambda: False)
                check()
                hits = {hit.pc: hit for hit in trace.hits if hit.pc in pending}
                for address in page:
                    hit = hits.get(address)
                    memory = capture / f'{address:08X}_0.rdram'
                    ram = memory.read_bytes() if hit and memory.is_file() else None
                    for item in pending[address]:
                        if ram is not None and _execution_matches(image, item, ram):
                            item['executed'] = True
                            item['execution_retrace'] = hit.vi
                            if call_site := _return_call_site(ram, getattr(hit, 'ra', 0), address):
                                item['direct_call_site'] = call_site
                        # An incomplete run or missing capture cannot establish a negative.
                        if checkpoint and (ram is not None or (not hit and not trace.timed_out
                                                              and trace.vis >= 1800)):
                            checkpoint(item, save=True)
                        completed += 1
                for memory in capture.glob('*.rdram'):
                    memory.unlink()
            except emulator.EmulatorError as error:
                check()
                log(f"Could not check execution at {page[0]:#x} and nearby observed entries: {error}")
        if result:
            progress({'phase': 'Checking observed entries for execution', 'done': len(result), 'total': len(result)})
        if scoped:
            result.extend(called(image, segments, [(f['rom'], f['rom'] + f['size'], f['vram']) for f in functions
                                                   if f['vram'] is not None], dumps, check=check))
    return result


def _execution_matches(rom, item, ram):
    """The captured breakpoint must contain the complete body at the observed address."""
    start, end, address = item['rom_start'], item['rom_end'], item['vram']
    offset = address & 0x1fffffff
    return (0 <= start < end <= len(rom) and offset + end - start <= len(ram)
            and ram[offset:offset + end - start] == rom[start:end])


def _return_call_site(ram, return_address, target):
    """A saved return address may identify a direct call; it does not prove the last edge taken."""
    if type(return_address) is not int or return_address < 8 or return_address % 4:
        return None
    address = (return_address - 8) & 0xffffffff
    offset = address & 0x1fffffff
    if offset + 4 > len(ram):
        return None
    word = int.from_bytes(ram[offset:offset + 4], 'big')
    destination = ((address + 4) & 0xf0000000) | ((word & 0x03ffffff) << 2)
    return address if word >> 26 == 3 and destination == target else None


def located(rom, candidates, dumps, *, check=lambda: None, progress=lambda _: None):
    """Locate full candidate bytes in RAM; a matching copy is not execution evidence."""
    from ...arch.mips_gaps import extent

    found = defaultdict(set)
    seen = set()
    for index, row in enumerate(candidates):
        check()
        if not index % 32:
            progress({'phase': 'Comparing candidates with sampled RAM', 'done': index, 'total': len(candidates)})
        offset, size = row.get('rom_offset'), row.get('extent_bytes')
        if (type(offset) is not int or type(size) is not int or size < 64 or size % 4
                or offset < 0 or offset + size > len(rom) or offset % 4 or (offset, size) in seen):
            continue
        seen.add((offset, size))
        body = rom[offset:offset + size]
        prefix = body[:64]
        if rom.find(prefix) != offset or rom.find(prefix, offset + 1) >= 0:
            continue  # Identical source bytes cannot select one ROM placement.
        for index, ram in enumerate(dumps):
            at = ram.find(prefix)
            if at < 0 or at % 4 or ram.find(prefix, at + 1) >= 0 or ram[at:at + size] != body:
                continue
            address = 0x80000000 + at
            flow = extent(rom, offset, offset + size, address, 'big', 'vr4300')
            if flow['direct_flow_closed'] and flow['extent_bytes'] == size:
                found[(offset, size, address)].add(index)
    progress({'phase': 'Comparing candidates with sampled RAM', 'done': len(candidates), 'total': len(candidates)})
    return [{'kind': 'runtime_location', 'rom_start': offset, 'rom_end': offset + size,
             'vram': address, 'snapshots': len(indices)}
            for (offset, size, address), indices in sorted(found.items())]


def called(rom, segments, functions, dumps, *, check=lambda: None):
    """Find uniquely placed, closed bodies whose caller and bytes coexist in two RAM snapshots.

    A candidate lies outside all existing ROM segments, but its RAM address is occupied by other segments in
    the caller's declared exclusive overlay scope. This is evidence for a later staged recovery, not a change
    to the project or proof that the function executed during the snapshots.
    """
    if len(dumps) < 2:
        return []
    segments = list(segments)
    occupied = sorted((lo, hi) for lo, hi, _ in functions)
    starts = [lo for lo, _ in occupied]
    max_ends = []
    for _, end in occupied:
        max_ends.append(max(end, max_ends[-1] if max_ends else 0))
    segment_starts = sorted(s.rom_start for s in segments)
    proposed = defaultdict(set)
    for lo, hi, address in functions:
        check()
        owners = [s for s in segments if s.rom_start <= lo < hi <= s.rom_end
                  and s.vram + lo - s.rom_start == address and s.exclusive_ram_id]
        if len(owners) != 1 or lo < 0 or hi > len(rom) or (lo | hi) % 4:
            continue
        scope = owners[0].exclusive_ram_id
        source = rom[lo:hi]
        source_ram = address & 0x1fffffff
        views = {i for i, ram in enumerate(dumps) if source_ram + len(source) <= len(ram)
                 and ram[source_ram:source_ram + len(source)] == source}
        if len(views) < 2 or asm_reason(source, True, cpu='vr4300'):
            continue
        words = struct.unpack(f'>{len(source)//4}I', source)
        for target in jal_targets(words, address):
            offset = target - (address - lo)
            if offset < 0 or offset + 8 > len(rom) or offset % 4:
                continue
            if any(s.rom_start <= offset < s.rom_end for s in segments):
                continue
            other = [s for s in segments if s.contains(target, 4)]
            if not other or any(s.exclusive_ram_id != scope for s in other):
                continue
            index = bisect_right(starts, offset) - 1
            if index >= 0 and max_ends[index] > offset:
                continue
            proposed[(offset, target, scope)].update(views)
    result = []
    for (offset, target, scope), views in proposed.items():
        check()
        later = bisect_right(segment_starts, offset)
        limit = min(len(rom), offset + MAX_BYTES,
                    segment_starts[later] if later < len(segment_starts) else len(rom))
        index = bisect_right(starts, offset)
        if index < len(starts):
            limit = min(limit, starts[index])
        size = (called_extent(rom, offset, limit, target, cpu='vr4300')
                or leaf_extent(rom, offset, limit, target, cpu='vr4300'))
        if not size:
            continue
        # A conflicting overlay can begin inside the body, after its entry point.
        if any(s.vram < target + size and target < s.vram + s.rom_end - s.rom_start
               and s.exclusive_ram_id != scope for s in segments):
            continue
        body = rom[offset:offset + size]
        if rom.find(body) != offset or rom.find(body, offset + 1) >= 0:
            continue
        target_ram = target & 0x1fffffff
        matching = sum(target_ram + size <= len(dumps[i]) and dumps[i][target_ram:target_ram + size] == body for i in views)
        if matching >= 2:
            result.append({'rom_start': offset, 'rom_end': offset + size, 'vram': target,
                           'scope': scope, 'co_resident_snapshots': matching})
    # Competing entry points are not automatically split or merged.
    return sorted((item for item in result if not any(item is not other and (
                   (item['rom_start'] < other['rom_end'] and other['rom_start'] < item['rom_end']) or
                   (item['vram'] < other['vram'] + other['rom_end'] - other['rom_start'] and
                    other['vram'] < item['vram'] + item['rom_end'] - item['rom_start'])) for other in result)),
                  key=lambda item: item['rom_start'])


def closure(rom, segments, functions, samples, *, reserved=(), check=lambda: None):
    """Follow new same-bank callers until no sampled overlay yields another unambiguous body."""
    segments = list(segments)
    functions = [f for f in functions if f[2] is not None]
    recovered = []
    while True:
        check()
        proposals = {}
        for dumps in samples:
            for item in called(rom, segments, functions, dumps, check=check):
                key = (item['rom_start'], item['rom_end'], item['vram'], item['scope'])
                proposals[key] = item
        keys = list(proposals)
        accepted = []
        for key in keys:
            lo, hi, vram, scope = key
            if any(a < hi and lo < b for a, b, _ in reserved):
                continue
            if any(item['vram'] < vram + hi - lo and vram < item['vram'] + item['rom_end'] - item['rom_start']
                   for item in recovered):
                continue
            if any(other != key and ((other[0] < hi and lo < other[1]) or
                   (other[2] < vram + hi - lo and vram < other[2] + other[1] - other[0])) for other in keys):
                continue
            accepted.append(proposals[key])
        if not accepted:
            return recovered
        for item in accepted:
            lo, hi, vram, scope = item['rom_start'], item['rom_end'], item['vram'], item['scope']
            segments.append(Segment(f'scoped_{lo:X}', lo, hi, vram, scope))
            functions.append((lo, hi, vram))
        recovered.extend(accepted)


def inherited_scope(body, segments):
    """A split bank's other discovered bodies use its scope when its mapping selects one scope."""
    lo, hi, vram = body
    delta = vram - lo
    same = [s for s in segments if s.vram - s.rom_start == delta]
    scopes = {s.exclusive_ram_id for s in same}
    if len(scopes) != 1 or not next(iter(scopes)):
        return ''
    scope = next(iter(scopes))
    if any(s.vram < vram + hi - lo and vram < s.vram + s.rom_end - s.rom_start
           and s.exclusive_ram_id != scope for s in segments):
        return ''
    return scope


def separate_runtime_scope(body, segments, runtime_banks, config):
    """Keep an observed bank out of Splat's global span when an overlay lies between them."""
    lo, hi, address = body
    if not any(a <= lo < hi <= b and address - lo == vram - a for a, b, vram in runtime_banks):
        return ''
    raw = config.get('segments', [])
    global_ranges = []
    for index, segment in enumerate(raw):
        if not isinstance(segment, dict) or segment.get('type') != 'code' or segment.get('exclusive_ram_id'):
            continue
        start, vram = segment.get('start'), segment.get('vram')
        if type(start) is not int or type(vram) is not int:
            return ''
        ends = [s[0] if isinstance(s, list) and s else s.get('start') if isinstance(s, dict) else None
                for s in raw[index + 1:]]
        end = next((value for value in ends if type(value) is int), None)
        if end is None:
            return ''
        global_ranges.append((vram, vram + end - start + int(segment.get('bss_size') or 0)))
    if not global_ranges:
        return ''
    low, high = min(a for a, _ in global_ranges), max(b for _, b in global_ranges)
    body_end = address + hi - lo
    if address < high and low < body_end:
        return ''
    if any(s.vram < body_end and address < s.vram + s.rom_end - s.rom_start for s in segments):
        return ''
    expanded_low, expanded_high = min(low, address), max(high, body_end)
    if not any(s.exclusive_ram_id and s.vram < expanded_high and expanded_low < s.vram + s.rom_end - s.rom_start
               for s in segments):
        return ''
    scope = f'runtime_bank_{(address - lo) & 0xFFFFFFFF:08X}'
    if any(s.exclusive_ram_id == scope for s in segments):
        return ''
    return scope
