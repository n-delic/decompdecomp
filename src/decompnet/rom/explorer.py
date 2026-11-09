"""An evidence ledger for every analysis-image byte, separate from exact code matching.

Imported mappings and relocations are facts about the current analysis, not proof of
runtime reachability. Statistical and format clues remain explicitly tentative.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import re
import struct
import zlib
from collections import Counter
from functools import lru_cache

PAGE_BYTES = 64 * 1024
SAMPLE_BYTES = 4096
DECODE_BYTES = 1024 * 1024
MAX_DECODE_PROBES = 256
MAX_FORMAT_CLUES = 8192
HEADERS = re.compile(b'\x1f\x8b\x08|\x78[\x01\x5e\x9c\xda]|Yaz0|Yay0|MIO0|RNC[\x01\x02]|\x89PNG\r\n\x1a\n|RIFF|OggS|VAGp|\xff\xd8\xff')
STRINGS = re.compile(rb'[\x20-\x7e]{8,}\x00')


def _clue(image, offset, budget):
    """Validate common streams within a fixed output budget; headers alone stay leads."""
    head = image[offset:offset+16]
    label = None
    if head.startswith(b'\x1f\x8b\x08'):label = 'gzip'
    elif len(head) >= 2 and head[0] == 0x78 and (head[0]*256+head[1]) % 31 == 0:label = 'zlib'
    if label:
        clue = dict(offset=offset, label=label, confidence='tentative',
                    reason='Recognized stream header; decoding not validated within the scan budget.')
        if budget[0] > 0:
            budget[0] -= 1
            try:
                decoder = zlib.decompressobj(31 if label == 'gzip' else 15)
                raw = image[offset:offset+DECODE_BYTES]
                output = decoder.decompress(raw, DECODE_BYTES)
                if decoder.eof:
                    clue.update(kind='compressed',confidence='validated', size=len(raw)-len(decoder.unused_data),
                                 expanded_size=len(output), reason='Decoder reached the stream end and validated its checksum.')
            except zlib.error:
                clue['confidence']='rejected'
                clue['reason'] = 'Rejected compression signature: decoding failed. These bytes are not classified as compressed.'
        return clue
    for magic, name, kind in ((b'Yaz0','Yaz0','compressed'), (b'Yay0','Yay0','compressed'),
                              (b'MIO0','MIO0','compressed'), (b'RNC','RNC','compressed'),
                              (b'\x89PNG\r\n\x1a\n','PNG image','image'), (b'\xff\xd8\xff','JPEG image','image'),
                              (b'OggS','Ogg container','audio'),(b'VAGp','VAGp audio','audio')):
        if head.startswith(magic):
            clue=dict(offset=offset,kind=kind,label=name,confidence='tentative',reason='Format signature only; payload and extent have not been validated.')
            if kind in ('image','audio') and name!='Ogg container':
                from .asset_formats import MAX_BYTES, AssetError, identify
                if not budget[1]:return None
                budget[1]-=1
                try:
                    info=identify(image[offset:offset+MAX_BYTES])
                    clue.update(size=info['size'],format=info['format'],width=info.get('width'),height=info.get('height'),confidence='tentative' if info['format']=='JPEG' else 'validated',
                                reason=info.get('note','Bounded dimensions, container structure and payload checks passed.'))
                except AssetError:return None
            return clue
    if head.startswith(b'RIFF'):
        subtype = head[8:12]
        clue=dict(offset=offset,kind='audio' if subtype==b'WAVE' else 'container',label='RIFF '+subtype.decode('ascii','replace'),confidence='tentative',reason='Container header only; payload and extent have not been validated.')
        if subtype==b'WAVE':
            from .asset_formats import MAX_BYTES, AssetError, identify
            if not budget[1]:return None
            budget[1]-=1
            try:
                info=identify(image[offset:offset+MAX_BYTES]);clue.update(size=info['size'],confidence='validated',reason='PCM WAVE structure, format and duration checks passed.')
            except AssetError:return None
        return clue
    return None


def analyse(image, segments, functions, symbols, *, big_endian=True, progress=lambda _: None, check=lambda: None, ranges=(), assets=()):
    """Partition the entire image, retaining overlapping mappings rather than choosing an overlay.

    Large anonymous spans are display pages, not inferred asset boundaries. Function
    references use imported relocation targets, including interior addresses.
    """
    size = len(image)
    segments = [dict(s) for s in segments]
    for s in segments:
        if not 0 <= s['rom_start'] < s['rom_end'] <= size:
            raise ValueError('A segment exceeds the analysis image. Reimport the project before scanning.')
    by_address=sorted(segments,key=lambda s:s['vram'])
    addresses=[s['vram'] for s in by_address]
    furthest=[]
    for s in by_address:furthest.append(max(furthest[-1] if furthest else 0,s['vram']+s['rom_end']-s['rom_start']))

    @lru_cache(maxsize=8192)
    def lookup(address):
        result=[]
        i=bisect.bisect_right(addresses,address)-1
        while i>=0 and furthest[i]>address:
            s=by_address[i]
            if address<s['vram']+s['rom_end']-s['rom_start']:
                result.append(dict(segment=s['name'],rom=s['rom_start']+address-s['vram']))
            i-=1
        return result

    identity = hashlib.sha256(image).hexdigest()
    cuts = {0, size, *range(0, size, PAGE_BYTES)}
    events = {}

    def span(lo, hi, kind, record):
        if not 0 <= lo < hi <= size:return
        cuts.update((lo, hi))
        events.setdefault(lo, []).append((True, kind, record))
        events.setdefault(hi, []).append((False, kind, record))

    for i, r in enumerate(ranges):span(r['start'],r['end'],'transfer',i)
    for i, s in enumerate(segments):span(s['rom_start'], s['rom_end'], 'mapping', i)
    for i, f in enumerate(functions):
        check()
        if f.get('rom_offset') is not None:span(f['rom_offset'], f['rom_offset']+f['size'], 'function', i)
    symbol_spans = []
    for s in symbols:
        check()
        if s['kind'] in ('function','func','bss') or not s['size']:continue
        mappings = lookup(s['address'])
        for m in mappings:
            lo, hi = m['rom'], m['rom']+s['size']
            seg = next(v for v in segments if v['name']==m['segment'] and v['rom_start']<=lo<v['rom_end'])
            if hi > seg['rom_end']:continue
            entry = dict(s, segment=m['segment'], ambiguous=len(mappings)>1)
            symbol_spans.append(entry)
            span(lo, hi, 'symbol', len(symbol_spans)-1)
    clues = [dict(asset) for asset in assets]
    asset_starts = sorted(c['offset'] for c in assets)
    asset_ends = []
    for c in sorted(assets,key=lambda c:c['offset']):
        asset_ends.append(max(asset_ends[-1] if asset_ends else 0,c['offset']+c['size']))
    for clue in clues:
        cuts.update((clue["offset"],clue["offset"]+clue["size"]))
    budget = [MAX_DECODE_PROBES,MAX_DECODE_PROBES]
    format_limit=False;contained_end=0
    # Scan each byte once, including headers straddling a display page.
    for lo in range(0, size, PAGE_BYTES):
        check()
        for hit in HEADERS.finditer(image,lo,min(size,lo+PAGE_BYTES+16)) if not format_limit else ():
            if hit.start() >= lo+PAGE_BYTES:break
            asset_index=bisect.bisect_right(asset_starts,hit.start())-1
            if hit.start()<contained_end or asset_index>=0 and hit.start()<asset_ends[asset_index]:continue
            if len(clues)-len(assets)>=MAX_FORMAT_CLUES:
                format_limit=True;break
            clue = _clue(image, hit.start(), budget)
            if clue:
                clues.append(clue)
                cuts.add(clue['offset'])
                if clue.get('size'):
                    cuts.add(clue['offset']+clue['size'])
                    if clue['confidence']=='validated':contained_end=max(contained_end,clue['offset']+clue['size'])
        progress(dict(phase='Reading image',done=min(size,lo+PAGE_BYTES),total=size))
    for i,c in enumerate(clues):
        span(c['offset'],min(size,c['offset']+c.get('size',16)),'clue',i)
    ordered = sorted(cuts)
    active = {k:set() for k in ('mapping','function','symbol','clue','transfer')}
    rows = []
    for lo, hi in zip(ordered, ordered[1:]):
        check()
        for add, kind, idx in events.get(lo, []):
            (active[kind].add if add else active[kind].discard)(idx)
        maps = [dict(segment=segments[i]['name'],address=segments[i]['vram']+lo-segments[i]['rom_start'])
                for i in sorted(active['mapping'])]
        syms = [symbol_spans[i] for i in sorted(active['symbol'])]
        owners = [functions[i]['name'] for i in sorted(active['function'])]
        sample = image[lo:min(hi,lo+SAMPLE_BYTES)]
        counts = Counter(sample)
        entropy = round(-sum((n/len(sample))*math.log2(n/len(sample)) for n in counts.values()), 2) if sample else 0
        evidence = sorted([clues[i] for i in active['clue']],key=lambda c:c['confidence']!='validated')
        for i in sorted(active['transfer']):
            transfer=ranges[i]
            evidence.append(dict(label='Loader source range',confidence='inferred',reason=f"Call arguments describe ROM {transfer['start']:#x}–{transfer['end']:#x} (exclusive); may contain code or assets."))
        kind, confidence = 'unknown', 'unknown'
        if owners:kind, confidence = 'code', 'recorded'
        elif any('preview_settings' in e for e in evidence):
            asset=next(e for e in evidence if 'preview_settings' in e)
            kind,confidence=asset['kind'],asset['confidence']
        elif syms:
            kind, confidence = ('strings' if all(s['kind']=='string' for s in syms) else 'data'), 'recorded'
            if any(s['ambiguous'] for s in syms):confidence = 'tentative'
        elif any('kind' in e for e in evidence):
            clue=next(e for e in evidence if 'kind' in e);kind,confidence=clue['kind'],clue['confidence']
        elif sample and len(counts)==1:
            # This is a sample clue, never a claim that the whole range is padding.
            kind, confidence = 'repeated bytes', 'tentative'
        elif STRINGS.search(sample):kind, confidence = 'strings', 'tentative'
        if not owners:
            if STRINGS.search(sample):evidence.append(dict(label='Text',confidence='tentative',reason='Printable, NUL-terminated text in the sample.'))
            if entropy >= 7.5:evidence.append(dict(label='High entropy',confidence='tentative',reason='Could be compressed, encrypted or dense raw data. Entropy alone cannot identify a format.'))
        rows.append(dict(id=f'{lo:x}-{hi:x}',start=lo,end=hi,size=hi-lo,kind=kind,confidence=confidence,
                         mappings=maps,symbols=syms,functions=owners,evidence=evidence,entropy=entropy,
                         sampled_bytes=len(sample),references=[],pointers=[],
                         detected_asset=next((e for e in evidence if 'preview_settings' in e),None)))
    starts = [r['start'] for r in rows]

    def region(offset):
        i = bisect.bisect_right(starts,offset)-1
        return rows[i] if i>=0 and offset<rows[i]['end'] else None

    # Keep instruction and overlay provenance. Cross-overlay candidates are useful
    # leads but are not reported as certain uses of the same runtime address.
    for n, f in enumerate(functions):
        check()
        source = [s for s in segments if f.get('rom_offset') is not None and
                  s['rom_start'] <= f['rom_offset'] < s['rom_end']]
        source_names = {s['name'] for s in source}
        for off, rtype, address in f.get('relocs', []):
            mappings = lookup(address)
            local = [m for m in mappings if m['segment'] in source_names]
            chosen = local or mappings
            for m in chosen:
                row = region(m['rom'])
                if row is None:continue
                row['references'].append(dict(function=f['name'],instruction=f['vram']+off,
                    target=address,offset=m['rom'],segment=m['segment'],relocation=rtype,
                    confidence='recorded' if len(chosen)==1 else 'tentative',
                    reason='Imported address reference' if len(chosen)==1 else 'Address has multiple possible overlay mappings'))
        progress(dict(phase='Linking function references',done=n+1,total=len(functions)))
    fmt = '>I' if big_endian else '<I'
    for n,row in enumerate(rows):
        check()
        progress(dict(phase='Inspecting data clues',done=n+1,total=len(rows)))
        if row['kind']=='code':continue
        lo, hi = row['start'], row['end']
        # Aligned pointer words provide a second hop, never transitive proof of use.
        raw = image[(lo+3)//4*4:min(hi,lo+SAMPLE_BYTES)]
        raw = raw[:len(raw)//4*4]
        pointer_count = 0
        for j, (address,) in enumerate(struct.iter_unpack(fmt,raw)):
            maps = lookup(address) if address not in (0,0xffffffff) else []
            if maps:
                pointer_count += 1
                if len(row['pointers'])<32:
                    row['pointers'].append(dict(at=(lo+3)//4*4+j*4,target=address,
                                               destinations=[dict(m,region=region(m['rom'])['id']) for m in maps if region(m['rom'])]))
        if pointer_count >= 4 and pointer_count >= len(raw)//8:
            row['evidence'].append(dict(label='Possible pointer table',confidence='tentative',reason=f'{pointer_count} sampled aligned words point into mapped regions; integer coincidences remain possible.'))
            if row['kind']=='unknown':row.update(kind='pointer table',confidence='tentative')
        row['reference_count'] = len(row['references'])
    runtime=[]
    runtime_refs=sorted((address,f['name'],f['vram']+off) for f in functions for off,_,address in f.get('relocs',[]))
    runtime_addresses=[ref[0] for ref in runtime_refs]
    for symbol in symbols:
        if symbol['kind']!='bss':continue
        check()
        lo=bisect.bisect_left(runtime_addresses,symbol['address'])
        hi=bisect.bisect_left(runtime_addresses,symbol['address']+symbol['size'])
        refs=[dict(function=name,instruction=at,target=address) for address,name,at in runtime_refs[lo:hi]]
        runtime.append(dict(symbol,references=refs))
    totals = Counter()
    for row in rows:
        row['reference_count'] = len(row['references'])
        totals['recorded' if row['confidence'] in ('recorded','validated') else 'unknown' if row['confidence']=='unknown' else 'tentative'] += row['size']
    return dict(schema=1,image_hash=identity,size=size,regions=rows,runtime_symbols=runtime,totals=dict(totals),complete=True,
                decoder_probes=MAX_DECODE_PROBES-budget[0],media_probes=MAX_DECODE_PROBES-budget[1],format_limit=format_limit,
                note=('Media-validation budget reached; additional media headers were not classified. ' if not budget[1] else '')+('Format-candidate limit reached; additional signatures were not inspected. ' if format_limit else '')+'Coverage describes the analysis image, which may include expanded code. Recorded labels come from the import; validated labels confirm a bounded stream or asset structure, not runtime use. Anonymous spans are display pages, not asset boundaries. References can be incomplete; no references found does not mean unused. Pointer chains are leads, not proven data flow. Entropy and pointer sampling examine at most 4 KiB per region. No code or matching status is changed.')
