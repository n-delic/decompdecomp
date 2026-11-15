"""Bounded candidate-local struct padding proposals from aligned MIPS memory operands.

Shared headers are never edited. Complex types, packing, bitfields and ambiguous offsets are left to callers.
Every proposal still needs the ordinary compiler/link byte judge.
"""

import json
import re
from collections import defaultdict

STRUCT = re.compile(r'\bstruct(?:\s+[A-Za-z_]\w*)?\s*\{([^{}]*)\}')
FIELD = re.compile(r'\s*((?:(?:unsigned|signed)\s+)?(?:char|short|int|long|float)|[us](?:8|16|32)|u?int(?:8|16|32)_t|[A-Za-z_]\w*\s*\*)\s+([A-Za-z_]\w*)(?:\s*\[\s*(0x[0-9a-fA-F]+|[0-9]+)\s*\])?\s*;')
MEMORY = {32, 33, 35, 36, 37, 40, 41, 43}


def variants(source, report, target, big):
    from .similar_matches import TOKENS

    if not report.get('candidate_target'):
        return []
    left, right = bytes.fromhex(report['candidate_target']), target['target'] or b''
    if len(left) != len(right) or len(left) % 4:
        return []
    relocated = {r[0] for r in json.loads(report.get('candidate_relocs', '[]'))}
    relocated.update(r[0] for r in json.loads(target['relocs'] or '[]'))
    offsets = defaultdict(set)
    order = 'big' if big else 'little'
    for pos in range(0, len(left), 4):
        a, b = int.from_bytes(left[pos:pos+4],order), int.from_bytes(right[pos:pos+4],order)
        if pos in relocated or a >> 16 != b >> 16 or a >> 26 not in MEMORY or (a >> 21) & 31 in (0,28,29,30):
            continue
        old, new = a & 65535, b & 65535
        if old < 32768 and new < 32768:
            offsets[old].add(new)
    moves = {old: next(iter(new)) for old,new in offsets.items() if len(new)==1 and old not in new}
    if not moves:
        return []
    masked = TOKENS.sub(lambda m: ' ' * len(m.group()) if m.group().startswith(('/*','//','"',"'")) else m.group(),source)
    if re.search(r'\b(?:packed|pragma|union)\b|__attribute__',masked):
        return []
    proposals=[]
    for struct in STRUCT.finditer(masked):
        body=struct[1];fields=list(FIELD.finditer(body))
        if FIELD.sub('',body).strip():
            continue
        offset=0;previous=None
        for f in fields:
            typ,name,count=f.groups();size=1 if typ in ('char','unsigned char','signed char','u8','s8','uint8_t','int8_t') else 2 if typ in ('short','unsigned short','signed short','u16','s16','uint16_t','int16_t') else 4
            align=size;offset=(offset+align-1)//align*align
            amount=int(count,16 if count and count.lower().startswith('0x') else 10) if count else 1
            new=moves.get(offset)
            if new is not None and re.search(r'(?:->|\.)\s*'+re.escape(name)+r'\b',masked):
                delta=new-offset
                if previous and previous[1] is not None and previous[0][1] in ('char','unsigned char','signed char','u8','s8','uint8_t','int8_t') and re.match(r'(?:_+)?(?:pad|padding|unk)',previous[0][2],re.I):
                    pad,pad_count=previous
                    value=pad_count+delta
                    if 0<=value<=4096:
                        start=struct.start(1)+pad.start(3);end=struct.start(1)+pad.end(3)
                        proposals.append(source[:start]+str(value)+source[end:])
                elif 0<delta<=4096 and new%align==0:
                    start=struct.start(1)+f.start()
                    padname='__reuse_pad_'+name
                    if not re.search(r'\b'+padname+r'\b',masked):
                        proposals.append(source[:start]+f' char {padname}[{delta}]; '+source[start:])
            previous=(f,amount if count else None)
            offset+=size*amount
            if len(proposals)>=4:
                return list(dict.fromkeys(proposals))[:4]
    return list(dict.fromkeys(proposals))[:4]
