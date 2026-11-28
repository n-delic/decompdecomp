"""Re-split dropped text only at independently called, processor-validated framed entries."""
import json
import shutil
from bisect import bisect_right

import yaml

from .. import arch
from ..arch.mips import is_frame
from ..arch.mips_references import references

MAX_PASSES = 3
MAX_CANDIDATES = 512
REFINE_TIMEOUT = 120


def _branch_delay_frame(image, offset, size, start, order):
    """A stack allocation in a branch delay slot is part of its surrounding body."""
    if offset < start + 4:
        return False
    word = int.from_bytes(image[offset-4:offset], order)
    if word >> 26 not in (4, 5, 6, 7, 20, 21, 22, 23):
        return False
    displacement = word & 0xffff
    if displacement & 0x8000:
        displacement -= 0x10000
    target = offset + 4 * displacement
    return offset + 4 <= target < offset + size


def _scheduled_prefix(image, offset, size, row, calls, transfers, validate, console):
    """Keep straight-line argument preparation before a quiet recovered frame."""
    import rabbitizer

    from ..arch.mips import JR_RA

    start, end, segment, _, _ = row
    order = 'big' if console.big_endian else 'little'
    for at in range(offset-8, max(segment.rom_start-4, start-12, offset-76), -4):
        if int.from_bytes(image[at:at+4], order) != JR_RA:
            continue
        entry = at+8
        if not start <= entry < offset or any(entry < other <= offset for other, _, _ in calls):
            continue
        slot = rabbitizer.Instruction(int.from_bytes(image[at+4:at+8], order))
        if not slot.isValid() or slot.hasDelaySlot():
            continue
        mapping = segment.vram-segment.rom_start
        if any(entry+mapping < target <= offset+mapping for target in transfers):
            continue
        full = validate(image, entry, end, entry+mapping, big_endian=console.big_endian,
                        cpu=console.cpu_variant, asm_check=console.asm_reason, max_lead=16)
        if full == size+offset-entry:
            return entry, full
    return offset, size


def refine(config, workdir, image, console, log=print, *, visited=None):
    """Add C subsegment boundaries inside raw .word output, preserving explicit data and known functions."""
    from . import split

    validate=arch.get(console.arch).called_code_extent
    if validate is None:
        return False
    visited=visited if visited is not None else set()
    check_cancel=getattr(log,'check_cancel',lambda:None)
    cfg=yaml.safe_load(config.read_text(encoding='utf-8'))
    segments=split.segments_from_config_data(cfg)
    rom=(workdir/image).read_bytes()
    paths=sorted((workdir/'asm/nonmatchings').glob('**/*.s'))
    functions=[];known=[];dropped=[]
    for path in paths:
        check_cancel()
        name=path.relative_to(workdir/'asm/nonmatchings').parts[0]
        fn=split.parse_function(path,name,console.big_endian)
        if fn is not None:
            split._complete_delay_slot(fn,rom,console.big_endian)
            known.append(fn)
            if (not console.asm_reason(b''.join(line.word.to_bytes(4,'big' if console.big_endian else 'little') for line in fn.lines))
                    and all(not line.mnemonic.startswith('.') for line in fn.lines)):
                functions.append(fn)
            continue
        text=path.read_text(encoding='utf-8')
        if not any(line.startswith('dlabel ') for line in text.splitlines()):continue
        rows=[split._INSN.match(line) for line in text.splitlines()]
        rows=[m for m in rows if m]
        if not rows or any(m[4]!='.word' for m in rows):continue
        start=int(rows[0][1],16);end=int(rows[-1][1],16)+4
        owners=[]
        for segment in segments:
            raw=next(s for s in cfg['segments'] if isinstance(s,dict) and s.get('name')==segment.name)
            names={segment.name}|{sub[2] for sub in raw.get('subsegments',[]) if isinstance(sub,list) and len(sub)>2}
            if name in names and segment.rom_start<=start<end<=segment.rom_end:
                owners.append((segment,raw))
        if len(owners)!=1:continue
        segment,raw=owners[0]
        if end>len(rom) or any(int(m[1],16)!=start+i*4
                or int(m[2],16)!=segment.vram+start+i*4-segment.rom_start for i,m in enumerate(rows)):continue
        # Do not reinterpret explicit data or custom subsegments.
        subs=raw.get('subsegments',[])
        for i,sub in enumerate(subs):
            if not isinstance(sub,list) or len(sub)<3 or sub[1]!='c':continue
            limit=next((s[0] if isinstance(s,list) else s.get('start') for s in subs[i+1:]
                        if (isinstance(s,list) and s) or isinstance(s,dict) and s.get('start') is not None),segment.rom_end)
            if sub[0]<=start<end<=limit:
                dropped.append((start,end,segment,raw,sub));break
    # Raw disassembler chunks can overlap separately emitted known functions.
    # Never use that raw extent as permission to cut an existing target.
    unowned=[]
    for start,end,segment,raw,sub in dropped:
        if any(f.piece and segment.rom_start<=f.rom<segment.rom_end
               and (not raw.get('exclusive_ram_id') or f.segment == sub[2]) for f in known):
            continue  # keep existing switch pieces in their original source unit
        cursor=start
        for fn in sorted(known,key=lambda f:f.rom):
            if fn.rom+fn.size<=cursor or fn.rom>=end:continue
            if fn.rom>cursor:unowned.append((cursor,fn.rom,segment,raw,sub))
            cursor=max(cursor,fn.rom+fn.size)
        if cursor<end:unowned.append((cursor,end,segment,raw,sub))
    dropped=unowned
    if not dropped:return False
    dropped.sort(key=lambda row:row[0]);starts=[r[0] for r in dropped]
    scopes = {raw['start']:raw['exclusive_ram_id'] for raw in cfg['segments']
              if isinstance(raw,dict) and raw.get('exclusive_ram_id')}
    calls=set();transfers=set()
    for fn in functions:
        for line in fn.lines:
            word = line.word
            op = word >> 26
            if op == 2:
                transfers.add(((line.vram+4) & 0xf0000000) | ((word & 0x3ffffff) << 2))
            elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and (word >> 21) & 31 == 8:
                delta = (word & 0xffff) ^ 0x8000
                transfers.add(line.vram+4+(delta-0x8000)*4)
        owners = [s for s in segments if s.rom_start <= fn.rom < s.rom_end
                  and fn.vram-fn.rom == s.vram-s.rom_start]
        owner = owners[0] if len(owners)==1 else None
        # A function reached only through a pointer the code builds (lui/addiu: a callback, a handler table's entry)
        # is as independently referenced as a called one; the raw chunk it hides in was never probed (a tank game's
        # 372 bodies, 62 KB, stayed `.word` lines).
        targets = [((line.vram+4)&0xF0000000)|((line.word&0x3FFFFFF)<<2) for line in fn.lines if line.word>>26==3]
        if fn.rom+fn.size<=len(rom):
            targets += [t for t,_,kind in references(rom,fn.rom,fn.rom+fn.size,fn.vram) if kind=='address']
        for target in targets:
            mappings = [s for s in segments if s.contains(target,4)]
            if len(mappings)==1:
                segment = mappings[0]
            elif owner in mappings and (scope := scopes.get(owner.rom_start)) and all(
                    scopes.get(s.rom_start)==scope for s in mappings):
                segment = owner
            else:
                continue  # a call from outside the shared slot is ambiguous
            calls.add((segment.rom_start+target-segment.vram,target,segment.rom_start))
    probes=[]
    for offset,target,owner_start in sorted(calls):
        index=bisect_right(starts,offset)-1
        if index<0 or offset>=dropped[index][1]:continue
        row=dropped[index]
        if row[2].rom_start!=owner_start:continue
        if (offset,target,row[1]) not in visited:
            probes.append((offset,target,row))
    # Quiet code: a framed body nobody calls or points at, inside a raw chunk, is accepted when it saves and restores
    # $ra and itself calls a function the project already has (loader_code's linked_frame). A data table seldom does.
    # A call outside this bank is useful evidence only when its address identifies
    # one known entry; overlapping banks can otherwise make a caller ambiguous.
    entry_vrams={};all_entries={}
    for fn in functions:
        entry_vrams.setdefault(fn.vram-fn.rom,set()).add(fn.vram)
        all_entries[fn.vram] = all_entries.get(fn.vram, 0) + 1
    unambiguous_entries = {address for address,count in all_entries.items() if count == 1}
    for start,end,segment,raw,_ in dropped:
        mapping=segment.vram-segment.rom_start
        known_entries=entry_vrams.get(mapping,set()) | unambiguous_entries
        if not known_entries:continue
        for at in range(start,end-8,4):
            if not is_frame(int.from_bytes(rom[at:at+4],'big')) or (at,at+mapping,end) in visited:continue
            if any(at==o for o,_,_ in probes):continue
            # Some valid functions restore $ra after the first KiB; the complete
            # extent is still checked by the processor-specific validator below.
            words=[int.from_bytes(rom[x:x+4],'big') for x in range(at,min(at+0x800,end),4)]
            if not ({w&0xffff for w in words if w&0xffff0000==0xafbf0000}&{w&0xffff for w in words if w&0xffff0000==0x8fbf0000}):continue
            if any(w>>26==3 and (((at+mapping)&0xF0000000)|((w&0x3FFFFFF)<<2)) in known_entries for w in words):
                probes.append((at,at+mapping,next(r for r in dropped if r[0]<=at<r[1])))  # visited is set when probed
    accepted=[]
    for offset,target,row in probes[:MAX_CANDIDATES]:
        check_cancel()
        visited.add((offset,target,row[1]))
        size=validate(rom,offset,row[1],target,big_endian=console.big_endian,cpu=console.cpu_variant,
                      asm_check=console.asm_reason)
        if (size and not _branch_delay_frame(rom,offset,size,row[2].rom_start,
                                             'big' if console.big_endian else 'little')
                and not any(offset<other<offset+size for other,_,_ in calls)):
            if console.arch == 'mips' and not any(other == offset for other, _, _ in calls):
                offset, size = _scheduled_prefix(rom, offset, size, row, calls, transfers, validate, console)
            accepted.append((offset,offset+size,row))
    # Competing called frames must not cut through each other.
    accepted=[a for a in accepted if not any(a is not b and a[0]<b[1] and b[0]<a[1] for b in accepted)]
    changed=0
    anchored=set()
    for start,end,(_,_,segment,raw,sub) in accepted:
        existing={s[0] for s in raw['subsegments'] if isinstance(s,list) and s}
        cuts={start,end}
        if segment.name not in anchored:
            anchored.add(segment.name)
            # New source units alter spimdisasm's inference. Pin the starts and
            # ends of already emitted targets before introducing those units.
            for fn in known:
                if not (segment.rom_start<=fn.rom<fn.rom+fn.size<=segment.rom_end
                        and fn.vram==segment.vram+fn.rom-segment.rom_start):continue
                for i,part in enumerate(raw['subsegments']):
                    if not isinstance(part,list) or len(part)<2 or part[1]!='c':continue
                    limit=next((s[0] for s in raw['subsegments'][i+1:] if isinstance(s,list) and s),segment.rom_end)
                    if part[0]<=fn.rom<fn.rom+fn.size<=limit:
                        cuts.add(fn.rom)
                        end = fn.rom+fn.size
                        # A new source unit beginning with a NOP can turn padding or a delay slot
                        # into a spurious one-word function. Preserve a separately known entry.
                        if (rom[end:end+4] != bytes(4) or any(other.rom == end for other in known)
                                or any(offset == end for offset, _, _ in calls)):
                            cuts.add(end)
                        break
        for at in sorted(cuts):
            if at<segment.rom_end and at not in existing:
                raw['subsegments'].append([at,'c',f'{sub[2]}_{at:06X}'])
                changed+=1
        # Keep custom dictionaries in their original order: candidates only use
        # simple list-based text ranges, and sort by explicit ROM starts.
        raw['subsegments'].sort(key=lambda s:s[0] if isinstance(s,list) else s.get('start',segment.rom_end))
    note_path=workdir/'analysis-coverage.json'
    notes=json.loads(note_path.read_text()) if note_path.is_file() else []
    if changed:
        config.write_text(yaml.safe_dump(cfg,sort_keys=False,default_flow_style=None),encoding='utf-8')
        message=f'Recovered {len(accepted)} framed entries with call evidence from raw disassembler output; unmatched code still needs normal exact judging.'
        log(message);notes.append(message)
    if len(probes)>MAX_CANDIDATES:
        notes.append(f'Mixed-code recovery reached its {MAX_CANDIDATES}-candidate pass limit; coverage remains incomplete.')
    if changed or len(probes)>MAX_CANDIDATES:
        note_path.write_text(json.dumps(list(dict.fromkeys(notes))),encoding='utf-8')
    return bool(changed)


def _detected_functions(workdir, image, console):
    """Keep every existing entry at its original ROM/RAM address and first instruction.

    New calls can split an initially merged body into several functions. Saved exact
    extents are separately restored/rejudged by reimport; fresh open extents may improve.
    """
    from . import split

    rom=(workdir/image).read_bytes();targets={}
    for path in (workdir/'asm/nonmatchings').glob('**/*.s'):
        fn=split.parse_function(path,path.parent.name,console.big_endian)
        if fn is None or fn.piece:continue
        split._complete_delay_slot(fn,rom,console.big_endian)
        targets[(fn.rom,fn.vram,fn.lines[0].word)] = fn.size
    return targets


def detected_entries(workdir, image, console):
    return set(_detected_functions(workdir, image, console))


def _anchor_lost_overlay_entries(config, missing, sizes):
    """Retry scoped banks whose existing entries disappeared after a split elsewhere."""
    from . import split

    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    segments = split.segments_from_config_data(cfg)
    changed = False
    for key in sorted(missing):
        rom, vram, _ = key
        size = sizes[key]
        owners = [s for s in segments if s.rom_start <= rom < rom + size <= s.rom_end
                  and s.vram + rom - s.rom_start == vram]
        if len(owners) != 1:
            continue
        owner = owners[0]
        raw = next((s for s in cfg['segments'] if isinstance(s, dict) and s.get('name') == owner.name), None)
        if not raw or not raw.get('exclusive_ram_id'):
            continue
        subs = raw.get('subsegments', [])
        for index, sub in enumerate(subs):
            if not isinstance(sub, list) or len(sub) < 3 or sub[1] != 'c':
                continue
            end = next((s[0] if isinstance(s, list) else s.get('start') for s in subs[index+1:]
                        if (isinstance(s, list) and s) or (isinstance(s, dict) and s.get('start') is not None)),
                       owner.rom_end)
            if not sub[0] <= rom < rom + size <= end:
                continue
            for at in (rom, rom + size):
                if at < owner.rom_end and not any(isinstance(s, list) and s and s[0] == at for s in subs):
                    stem = f'{str(sub[2])[:170]}_anchor_{at:06X}'
                    names = {s[2] for s in subs if isinstance(s, list) and len(s) > 2}
                    name = stem
                    suffix = 1
                    while name in names:
                        name = f'{stem}_{suffix}'
                        suffix += 1
                    subs.append([at, 'c', name])
                    changed = True
            break
        subs.sort(key=lambda s: s[0] if isinstance(s, list) else s.get('start', owner.rom_end))
    if changed:
        config.write_text(yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None), encoding='utf-8')
    return changed


def run(config, workdir, image, console, log=print):
    """Bounded fresh disassembly with entry preservation and a full initial-layout fallback."""
    from . import split

    if arch.get(console.arch).called_code_extent is None:return ''
    original_config=config.read_bytes()
    coverage_path=workdir/'analysis-coverage.json'
    original_notes=coverage_path.read_bytes() if coverage_path.is_file() else b'[]'
    original_entries=None;original_sizes={};visited=set();out=''
    for turn in range(MAX_PASSES):
        if not refine(config,workdir,image,console,log,visited=visited):break
        if original_entries is None:
            original_sizes = _detected_functions(workdir, image, console)
            original_entries = set(original_sizes)
        shutil.rmtree(workdir/'asm')  # generated output, before any saved project work is imported
        out+='\n'+split._splat(['split',config.name],workdir,log,timeout=REFINE_TIMEOUT)
        missing = original_entries - detected_entries(workdir,image,console)
        if missing and _anchor_lost_overlay_entries(config, missing, original_sizes):
            shutil.rmtree(workdir/'asm')
            out+='\n'+split._splat(['split',config.name],workdir,log,timeout=REFINE_TIMEOUT)
            missing = original_entries - detected_entries(workdir,image,console)
        if missing:
            config.write_bytes(original_config)
            shutil.rmtree(workdir/'asm')
            out+='\n'+split._splat(['split',config.name],workdir,log,timeout=REFINE_TIMEOUT)
            notes=json.loads(original_notes)
            message='Mixed-code recovery was discarded because re-disassembly changed an existing entry; the original split was retained for review.'
            notes.append(message);coverage_path.write_text(json.dumps(notes),encoding='utf-8');log(message)
            break
        if turn==MAX_PASSES-1:
            notes=json.loads(coverage_path.read_text()) if coverage_path.is_file() else []
            notes.append('Mixed-code recovery stopped after its bounded pass count; remaining raw regions need a coverage audit.')
            coverage_path.write_text(json.dumps(notes),encoding='utf-8')
    return out
