"""Check scoped overlay additions in a throwaway split before publishing them."""

import copy
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from ...rom import RomError, split


def _local(value):
    path = Path(value)
    return not path.is_absolute() and '..' not in path.parts


def _inputs(cfg, source, dest):
    """Copy only Splat's declared input files; never let staged paths escape the temporary tree."""
    options = cfg.get('options', {})
    for key, value in options.items():
        if key.endswith('_path'):
            paths = value if isinstance(value, list) else [value]
            if any(isinstance(path, str) and not _local(path) for path in paths):
                raise ValueError(f'{key} points outside the project')
    basename = options.get('basename', '')
    if basename:
        if not _local(basename):
            raise ValueError('the split basename points outside the project')
        # Some imported titles contain a slash. The staged basename is only an output
        # name; flatten it so Splat does not need extra directories in the scratch tree.
        options['basename'] = '_'.join(Path(basename).parts)
    for segment in cfg.get('segments', []):
        if not isinstance(segment, dict):
            continue
        for key in ('name', 'dir', 'path'):
            if isinstance(segment.get(key), str) and not _local(segment[key]):
                raise ValueError(f'the segment {key} points outside the project')
        for sub in segment.get('subsegments', []) or []:
            if isinstance(sub, list) and len(sub) > 2 and isinstance(sub[2], str) and not _local(sub[2]):
                raise ValueError('a source unit points outside the project')
            if isinstance(sub, dict):
                for key in ('name', 'dir', 'path'):
                    if isinstance(sub.get(key), str) and not _local(sub[key]):
                        raise ValueError(f'the source unit {key} points outside the project')
    extension = options.get('extensions_path')
    if extension and (source / extension).exists():
        raise ValueError('the split uses custom extensions which need a separate review')
    for key in ('symbol_addrs_path', 'reloc_addrs_path'):
        paths = options.get(key, [])
        for name in ([paths] if isinstance(paths, str) else paths or []):
            src = source / name
            if src.exists():
                if not src.resolve().is_relative_to(source.resolve()) or not src.is_file():
                    raise ValueError(f'{key} is outside the project')
                dst = dest / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
    options['base_path'] = '.'
    options['target_path'] = 'rom.z64'


def _functions(root):
    result = {}
    for path in (root / 'asm' / 'nonmatchings').rglob('*.s'):
        fn = split.parse_function(path, path.parent.name)
        if fn is None:
            continue  # Splat also writes inline data as assembly files.
        key = (fn.rom, fn.vram)
        value = (fn.size, b''.join(line.word.to_bytes(4, 'big') for line in fn.lines))
        if key in result and result[key] != value:
            raise ValueError('the split produced competing functions at one ROM address')
        result[key] = value
    return result


def _delay_slot_entries(root, known, image, functions):
    """Recognize a called entry that Splat shares with the preceding branch's delay slot.

    Splat emits the entry as an alabel at the end of one file and its remaining body as a
    function starting four bytes later. Require both pieces and the complete expected bytes;
    an arbitrary internal label or a different overlapping body is not enough.
    """
    wanted = {row['name']: row for row in known if row['size'] > 4}
    if not wanted:
        return
    label = re.compile(r'^\s*alabel\s+(\S+)\s*$')

    def branches(word):
        opcode = word >> 26
        return (opcode in (2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
                or opcode == 1 and (word >> 16) & 31 in (0, 1, 2, 3, 16, 17, 18, 19)
                or opcode in (16, 17, 18, 19) and (word >> 21) & 31 == 8
                or opcode == 0 and word & 63 in (8, 9))

    for path in (root / 'asm' / 'nonmatchings').rglob('*.s'):
        pending = None
        previous = None
        for raw in path.read_text(encoding='utf-8').splitlines():
            match = label.match(raw)
            if match:
                pending = wanted.get(match.group(1))
                continue
            insn = split._INSN.match(raw)
            if pending is None:
                if insn:
                    previous = tuple(int(insn.group(i), 16) for i in (1, 2, 3))
                continue
            if insn is None:
                if raw.strip():
                    pending = None
                continue
            lo, address, word = (int(insn.group(i), 16) for i in (1, 2, 3))
            row = pending
            pending = None
            size = row['size']
            if ((lo, address) != (row['rom_offset'], row['vram']) or previous is None
                    or previous[:2] != (lo - 4, address - 4)
                    or not branches(previous[2])):
                continue
            body = image[lo:lo + size]
            tail = functions.get((lo + 4, address + 4))
            if (len(body) == size and word.to_bytes(4, 'big') == body[:4]
                    and tail == (size - 4, body[4:])):
                functions[(lo, address)] = (size, body)


def _overlapping_entries(root, known, image, functions):
    """Prove existing internal entries from named labels and contiguous emitted instructions.

    Some independently called entries share a suffix with a larger function, or span more
    than one Splat output file. Only existing named records qualify, and every instruction
    in their recorded extent must be present at the same ROM and RAM address with its exact
    image bytes. Data directives and gaps cannot satisfy this check.
    """
    wanted = {row['name']: row for row in known
              if row.get('name') and (row['rom_offset'], row['vram']) not in functions and row['size'] > 0}
    if not wanted:
        return
    label = re.compile(r'^\s*alabel\s+(\S+)\s*$')
    for path in (root / 'asm' / 'nonmatchings').rglob('*.s'):
        pending = None
        for raw in path.read_text(encoding='utf-8').splitlines():
            match = label.match(raw)
            if match:
                pending = wanted.get(match.group(1))
                continue
            if pending is None:
                continue
            insn = split._INSN.match(raw)
            if insn is None:
                if raw.strip():
                    pending = None
                continue
            lo, address, word = (int(insn.group(i), 16) for i in (1, 2, 3))
            row, pending = pending, None
            if (lo, address) != (row['rom_offset'], row['vram']):
                continue
            size = row['size']
            body = image[lo:lo + size]
            if len(body) != size or size < 4 or word.to_bytes(4, 'big') != body[:4]:
                continue
            at = lo
            while at < lo + size:
                pieces = [(start, data) for (start, vram), (_, data) in functions.items()
                          if start <= at < start + len(data) and vram - start == address - lo]
                if not pieces:
                    break
                start, data = max(pieces, key=lambda piece: piece[0] + len(piece[1]))
                end = min(lo + size, start + len(data))
                if data[at - start:end - start] != body[at - lo:end - lo]:
                    break
                at = end
            if at == lo + size:
                functions[(lo, address)] = (size, body)
                wanted.pop(row['name'], None)


def verify(config, image, proposed, candidates, log, *, prepare=None, quiet=False, on_failure=None, cache=None,
           on_conflict=None, on_error=None):
    """Check that Splat keeps existing entries and gives each proposed body its exact bytes.

    Both splits run on copies. A failure leaves the proposed overlay as a review lead; cancellation propagates.
    """
    check = getattr(log, 'check_cancel', lambda: None)
    baseline = yaml.safe_load(config.read_text(encoding='utf-8'))
    phase = 'before'
    try:
        with tempfile.TemporaryDirectory(prefix='decomp-overlay-split-') as tmp:
            results = []
            for name, cfg in (('before', baseline), ('after', proposed)):
                phase = name
                check()
                root = Path(tmp) / name
                root.mkdir()
                cfg = copy.deepcopy(cfg)
                lines, known = None, None
                if prepare is not None:
                    cfg, lines, known = prepare(cfg, candidates if name == 'after' else [])
                    cfg.setdefault('options', {})['symbol_addrs_path'] = ['symbol_addrs.txt']
                _inputs(cfg, config.parent, root)
                if lines is not None:
                    (root / 'symbol_addrs.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                (root / 'game.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
                signature = hashlib.sha256()
                if name == 'before' and cache is not None:
                    # Input files are copied, so compare their bytes on every check: a rename or relocation
                    # edit must invalidate the baseline even during a long, resumable search.
                    for path in sorted(p for p in root.rglob('*') if p.is_file()):
                        signature.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
                        signature.update(hashlib.sha256(path.read_bytes()).digest())
                    key = signature.digest()
                    if cache.get('key') == key:
                        results.append(cache['before'])
                        continue
                (root / 'rom.z64').write_bytes(image)
                split._splat(['split', 'game.yaml'], root, log, timeout=300)
                result = _functions(root)
                if known:
                    _delay_slot_entries(root, known, image, result)
                    _overlapping_entries(root, known, image, result)
                if name == 'before' and not result:
                    raise ValueError('the baseline split produced no functions')
                if name == 'before' and cache is not None:
                    cache.update(key=key, before=result)
                results.append(result)
            before, after = results
            if prepare is None:
                changed = any(after.get(key) != body for key, body in before.items())
            else:
                changed = [row for row in known if
                           (before.get((row['rom_offset'], row['vram'])) is None or
                            after.get((row['rom_offset'], row['vram'])) != before[(row['rom_offset'], row['vram'])])
                           and after.get((row['rom_offset'], row['vram'])) !=
                           (row['size'], image[row['rom_offset']:row['rom_offset'] + row['size']])]
            if changed:
                if prepare is not None and on_conflict is not None:
                    on_conflict(changed)
                raise ValueError('the staged split changed or removed an existing function')
            for item in candidates:
                lo, hi, address = item['rom_start'], item['rom_end'], item['vram']
                if after.get((lo, address)) != (hi - lo, image[lo:hi]):
                    raise ValueError(f'the staged split could not reproduce the body at ROM {lo:#x}')
            check()
            return True
    except (RomError, ValueError, OSError) as error:
        check()
        if on_failure is not None:
            on_failure(phase == 'before')
        if on_error is not None:
            on_error(str(error))
        if not quiet:
            log(f'Recovered candidates remain for review: {error}.')
        return False


def isolate(groups, test, log, *, initial=(), checkpoint=None):
    """Check every group, splitting failed groups until all safe bodies or individual conflicts are known."""
    check = getattr(log, 'check_cancel', lambda: None)
    progress = getattr(log, 'progress', lambda _value: None)
    accepted, deferred = list(initial), []
    total = len(accepted) + sum(len(group) for group in groups)

    def visit(batch):
        check()
        trial = [item for group in batch for item in group]
        progress({'phase': f'Checking a group of {len(trial)} recovered bodies',
                  'done': len(accepted) + len(deferred), 'total': total})
        if test(accepted + trial):
            accepted.extend(trial)
            if checkpoint is not None:
                checkpoint(accepted)
        elif len(batch) > 1:
            middle = len(batch) // 2
            visit(batch[:middle])
            visit(batch[middle:])
        elif len(trial) > 1:
            middle = len(trial) // 2
            visit([trial[:middle]])
            visit([trial[middle:]])
        else:
            deferred.extend(trial)
        check()
        progress({'phase': 'Checking recovered bodies', 'done': len(accepted) + len(deferred), 'total': total})

    # The caller already checked the complete set, so only test its halves here.
    if len(groups) > 1:
        middle = len(groups) // 2
        visit(groups[:middle])
        visit(groups[middle:])
    elif groups and len(groups[0]) > 1:
        middle = len(groups[0]) // 2
        visit([groups[0][:middle]])
        visit([groups[0][middle:]])
    elif groups:
        deferred.extend(groups[0])
    return accepted, deferred
