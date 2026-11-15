"""Re-split dropped text only at independently called, processor-validated framed entries."""
import json
import shutil
from bisect import bisect_right

import yaml

from .. import arch

MAX_PASSES = 3
MAX_CANDIDATES = 512
REFINE_TIMEOUT = 120


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
        if any(f.piece and segment.rom_start<=f.rom<segment.rom_end for f in known):
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
    calls=set()
    for fn in functions:
        for line in fn.lines:
            if line.word>>26==3:
                calls.add(((line.vram+4)&0xF0000000)|((line.word&0x3FFFFFF)<<2))
    probes=[]
    for target in sorted(calls):
        mappings=[s for s in segments if s.contains(target,4)]
        if len(mappings)!=1:continue  # shared overlay RAM is not a unique destination
        segment=mappings[0];offset=segment.rom_start+target-segment.vram
        index=bisect_right(starts,offset)-1
        if index<0 or offset>=dropped[index][1]:continue
        row=dropped[index]
        if row[2]!=segment:continue
        if (offset,target,row[1]) not in visited:
            probes.append((offset,target,row))
    accepted=[]
    for offset,target,row in probes[:MAX_CANDIDATES]:
        check_cancel()
        visited.add((offset,target,row[1]))
        size=validate(rom,offset,row[1],target,big_endian=console.big_endian,cpu=console.cpu_variant,
                      asm_check=console.asm_reason)
        if size and not any(target<other<target+size for other in calls):
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
                        cuts.update((fn.rom,fn.rom+fn.size));break
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
        message=f'Recovered {len(accepted)} directly called framed entries from raw disassembler output; unmatched code still needs normal exact judging.'
        log(message);notes.append(message)
    if len(probes)>MAX_CANDIDATES:
        notes.append(f'Mixed-code recovery reached its {MAX_CANDIDATES}-candidate pass limit; coverage remains incomplete.')
    if changed or len(probes)>MAX_CANDIDATES:
        note_path.write_text(json.dumps(list(dict.fromkeys(notes))),encoding='utf-8')
    return bool(changed)


def detected_entries(workdir, image, console):
    """Keep every existing entry at its original ROM/RAM address and first instruction.

    New calls can split an initially merged body into several functions. Saved exact
    extents are separately restored/rejudged by reimport; fresh open extents may improve.
    """
    from . import split

    rom=(workdir/image).read_bytes();targets=set()
    for path in (workdir/'asm/nonmatchings').glob('**/*.s'):
        fn=split.parse_function(path,path.parent.name,console.big_endian)
        if fn is None or fn.piece:continue
        split._complete_delay_slot(fn,rom,console.big_endian)
        targets.add((fn.rom,fn.vram,fn.lines[0].word))
    return targets


def run(config, workdir, image, console, log=print):
    """Bounded fresh disassembly with entry preservation and a full initial-layout fallback."""
    from . import split

    if arch.get(console.arch).called_code_extent is None:return ''
    original_config=config.read_bytes()
    coverage_path=workdir/'analysis-coverage.json'
    original_notes=coverage_path.read_bytes() if coverage_path.is_file() else b'[]'
    original_entries=None;visited=set();out=''
    for turn in range(MAX_PASSES):
        if not refine(config,workdir,image,console,log,visited=visited):break
        if original_entries is None:original_entries=detected_entries(workdir,image,console)
        shutil.rmtree(workdir/'asm')  # generated output, before any saved project work is imported
        out+='\n'+split._splat(['split',config.name],workdir,log,timeout=REFINE_TIMEOUT)
        if not original_entries<=detected_entries(workdir,image,console):
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
