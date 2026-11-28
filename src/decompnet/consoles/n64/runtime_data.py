"""Find independently called code loaded from an otherwise unowned data tail."""
import tempfile
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import yaml

from ...arch.mips import is_frame
from ...arch.mips_references import references
from ...rom import RomError, split
from .loader_code import called_extent


def _data_tails(cfg, image_size):
    segments = sorted((s for s in cfg.get('segments', []) if isinstance(s, (dict, list))),
                      key=lambda s: s[0] if isinstance(s, list) else s.get('start', -1))
    ranges = []
    for parent, following in zip(segments, segments[1:]):
        if not isinstance(parent, dict) or parent.get('type') != 'code' or not isinstance(parent.get('vram'), int):
            continue
        end = following[0] if isinstance(following, list) else following.get('start')
        subs = sorted((s for s in parent.get('subsegments', []) if isinstance(s, list) and len(s) >= 2),
                      key=lambda s: s[0])
        if not subs or subs[-1][1] not in ('data', '.data', 'rodata', '.rodata') or not isinstance(end, int):
            continue
        lo = subs[-1][0]
        if 0 <= lo < end <= image_size:
            ranges.append((lo, end, parent['vram'] - parent['start']))
    return ranges


def discover(image, cfg, known, dumps, check=lambda: None):
    """Require two observed copies and a closed caller of two distinct closed callees on one mapping."""
    if len(dumps) < 2:
        return []
    found = defaultdict(dict)
    for lo, hi, old_shift in _data_tails(cfg, len(image)):
        for at in range((lo + 3) & ~3, hi - 127, 4):
            if not at % 4096:
                check()
            if not is_frame(int.from_bytes(image[at:at + 4], 'big')):
                continue
            head = image[at:at + 128]
            if image.find(head) != at or image.find(head, at + 1) >= 0:
                continue
            locations = Counter()
            for ram in dumps:
                where = ram.find(head)
                if where >= 0 and not where % 4 and ram.find(head, where + 1) < 0:
                    locations[where] += 1
            for where, count in locations.items():
                address = 0x80000000 + where
                shift = address - at
                if count < 2 or shift == old_shift:
                    continue
                size = called_extent(image, at, min(hi, at + 0x10000), address)
                views = {i for i, ram in enumerate(dumps) if ram[where:where + size] == image[at:at + size]} if size else set()
                if len(views) < 2:
                    continue
                # Cutting this tail must not discard a later existing function.
                if any(a < hi and at < b for a, b, _ in known):
                    continue
                if any(v is not None and v < address + size and address < v + b - a for a, b, v in known):
                    continue
                found[shift, lo, hi][(at, at + size, address)] = views
    accepted = []
    for bodies in found.values():
        entries = {v: (a, b, v) for a, b, v in bodies}
        edges = defaultdict(set)
        for a, b, address in bodies:
            for target, _, kind in references(image, a, b, address):
                if kind == 'call' and target in entries and target != address:
                    edges[address].add(target)
        selected = set()
        for caller, targets in edges.items():
            for first, second in combinations(sorted(targets), 2):
                group = (entries[caller], entries[first], entries[second])
                if len(bodies[group[0]] & bodies[group[1]] & bodies[group[2]]) >= 2:
                    selected.update(group)
        for body in sorted(selected):
            # A second location for these bytes or an overlapping candidate cannot establish a unique bank.
            if any(other != body and (other[0] < body[1] and body[0] < other[1]
                   or other[2] < body[2] + body[1] - body[0] and body[2] < other[2] + other[1] - other[0])
                   for group in found.values() for other in group):
                continue
            accepted.append(body)
    return sorted(set(accepted))


def recover_import(config, analysis_image, cartridge, log=print):
    """Stage observed data-tail code before the normal import's final split."""
    from . import code, emulator, loaded_code, runtime_staging

    if not emulator.installed() or not Path(cartridge).is_file():
        return []
    check = getattr(log, 'check_cancel', lambda: None)
    check()
    original = config.read_text(encoding='utf-8')
    cfg = yaml.safe_load(original)
    image = Path(analysis_image).read_bytes()
    if not _data_tails(cfg, len(image)):
        return []
    log('Checking early runtime snapshots for relocated code in data tails.')
    cancel = getattr(log, 'cancel_event', None)
    with tempfile.TemporaryDirectory(prefix='decomp-import-runtime-') as folder:
        root = Path(folder)
        try:
            dumps = {vi: root / f'{vi}.bin' for vi in loaded_code.DUMPS}
            run, _ = emulator.run_for_observation(Path(cartridge), loaded_code.VIS,
                inputs=loaded_code.inputs(), dumps=dumps, timeout=300,
                stop=cancel.is_set if cancel is not None else lambda: False, log=log)
            samples = [path.read_bytes() for _, path in sorted(run.dumps.items()) if path.is_file()]
            if not discover(image, cfg, (), samples, check):
                return []
            check()
            # The import has not run its final split yet. Obtain its actual functions in a private tree
            # before deciding whether shortening a data tail would discard any of them.
            initial = root / 'before'
            initial.mkdir()
            baseline = yaml.safe_load(original)
            runtime_staging._inputs(baseline, config.parent, initial)
            (initial / 'rom.z64').write_bytes(image)
            (initial / 'game.yaml').write_text(yaml.safe_dump(baseline, sort_keys=False), encoding='utf-8')
            split._splat(['split', 'game.yaml'], initial, log, timeout=300)
            parsed = runtime_staging._functions(initial)
            known = [(rom, rom + size, vram) for (rom, vram), (size, _) in parsed.items()]
            bodies = discover(image, cfg, known, samples, check)
            if not bodies:
                return []
            staged = root / 'proposed.yaml'
            code.insert_segments(staged, cfg, bodies, len(image), runtime_data=set(bodies))
            proposed = yaml.safe_load(staged.read_text(encoding='utf-8'))
            candidates = [dict(rom_start=a, rom_end=b, vram=v) for a, b, v in bodies]
            if not runtime_staging.verify(config, image, proposed, candidates, log):
                return []
            check()
            if config.read_text(encoding='utf-8') != original:
                raise RomError('The split configuration changed during import. Retry with the current project.')
            split.write_config_atomic(config, staged.read_text(encoding='utf-8'))
            log(f'Recovered {len(bodies)} independently called bodies from a runtime-observed data tail.')
            return bodies
        except (OSError, ValueError, RomError, emulator.EmulatorError) as error:
            check()
            log(f'Runtime-observed data-tail code remains for review: {error}')
            return []
