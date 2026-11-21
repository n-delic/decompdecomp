"""Bounded MIPS indirect-transfer evidence. No guessed mapping becomes an executable edge."""

import struct

MAX_ENTRIES=256
MAX_CALLS=512
MAX_WORDS=500000


def signed(w):return (w&0xffff)-0x10000 if w&0x8000 else w&0xffff


def locations(address,size,image,ranges,mappings=()):
    """Declared ranges first; established-code deltas are explicitly weaker hypotheses."""
    hits={s['rom_start']+address-s['vram'] for s in ranges
          if s['vram']<=address and address+size<=s['vram']+s['rom_end']-s['rom_start']}
    if any(not 0<=off<=len(image)-size for off in hits):return [],'mapping lies outside the image'
    if len(hits)>1:return [],'conflicting mappings'
    if hits:return [(hits.pop(),True)],None
    return [(address-m,False) for m in mappings if 0<=address-m<=len(image)-size],None


def _guard(image,at,order,scheduled):
    """The guard/scale/LUI/add/load/JR words before the JR at `at`, as (guard's address, bound, branch, delay,
    scale, hi, add, load, jump, slot), or None. `scheduled`: the scale sits in the guard branch's delay slot, as
    an N64 racing game's GCC scheduled it (sltiu; beqz; sll in its slot; lui; addu; lw; jr), one word shorter."""
    if scheduled:
        bound,branch,scale,hi,add,load,jump,slot=[int.from_bytes(image[p:p+4],order) for p in range(at-24,at+8,4)]
        return at-24,bound,branch,0,scale,hi,add,load,jump,slot
    bound,branch,delay,scale,hi,add,load,jump,slot=[int.from_bytes(image[p:p+4],order) for p in range(at-28,at+8,4)]
    return at-28,bound,branch,delay,scale,hi,add,load,jump,slot


def _saved_base(image,start,guard,order,register):
    """Resolve a callee-saved base initialized before the first transfer."""
    import rabbitizer

    value = None
    transferred = False
    paired_low = -1
    for at in range(start,guard,4):
        if at==paired_low:
            continue
        word = int.from_bytes(image[at:at+4],order)
        ins = rabbitizer.Instruction(word)
        if not ins.isValid():
            return None
        if ins.hasDelaySlot():
            transferred = True
        if not ((ins.modifiesRt() and (word>>16)&31==register)
                or (ins.modifiesRd() and (word>>11)&31==register)):
            continue
        if transferred:
            return None
        if word>>16 == 0x3c00|register and at+8<=guard:
            low = int.from_bytes(image[at+4:at+8],order)
            if low>>16 != 0x2400|register<<5|register:
                return None
            value = ((word&0xffff)<<16)+signed(low)
            paired_low = at+4
        else:
            return None
    return value


def _saved_register_guard(image,start,at,order):
    if at-start<20:
        return None
    guard = at-20
    bound,branch,scale,add,load,jump,slot = (
        int.from_bytes(image[p:p+4],order) for p in range(guard,at+8,4))
    index,condition,count = (bound>>21)&31,(bound>>16)&31,bound&0xffff
    scaled = (scale>>11)&31
    left,right = (add>>21)&31,(add>>16)&31
    base = right if left==scaled else left
    if (bound>>26!=11 or not 0<count<=MAX_ENTRIES or not index or not condition or condition==index
            or branch>>16!=0x1000|condition<<5
            or scale!=index<<16|scaled<<11|2<<6
            or not scaled or base not in range(16,24) or scaled==base or scaled not in (left,right)
            or condition==base or add&0xfc00ffff!=scaled<<11|33
            or load!=0x8c000000|scaled<<21|scaled<<16 or jump!=scaled<<21|8 or slot):
        return None
    address = _saved_base(image,start,guard,order,base)
    if address is not None:
        return guard,branch,count,address&0xffffffff


def _ido_register_guard(image,start,at,order):
    """IDO materializes the complete table address before scaling its index."""
    for scheduled in (False, True):
        guard = at-(28 if scheduled else 32)
        if guard<start:
            continue
        words = [int.from_bytes(image[p:p+4],order) for p in range(guard,at+8,4)]
        if not scheduled:
            delay = words.pop(2)
            move = delay&0xfc1f07ff in (0x21,0x25)
            if delay and delay>>21!=0x57d and not move:  # sw to the current stack frame
                continue
        bound,branch,hi,lo,scale,add,load,jump,slot = words
        index, condition, count = (bound>>21)&31, (bound>>16)&31, bound&0xffff
        base, scaled = (hi>>16)&31, (scale>>11)&31
        if (bound>>26!=11 or not 1<=count<=MAX_ENTRIES or not index or not condition or condition==index
                or branch>>16!=0x1000|condition<<5 or not base or base==index
                or hi>>16!=0x3c00|base or lo>>16!=0x2400|base<<5|base
                or not scaled or scaled==base or scale!=index<<16|scaled<<11|2<<6
                or add&0xfc00ffff!=scaled<<11|33 or {(add>>21)&31,(add>>16)&31}!={base,scaled}
                or load!=0x8c000000|scaled<<21|base<<16 or jump!=base<<21|8 or slot):
            continue
        if not scheduled and move and (delay>>11)&31==index:
            continue
        return guard,branch,count,(((hi&0xffff)<<16)+signed(lo))&0xffffffff
    return None


def jump_table(image,start,end,vram,at,order,ranges=(),mappings=(),*,reader=None, allow_saved_base=False):
    """Recognize one exact guard/scale/LUI/add/load/JR idiom (its scale in the guard's delay slot or after it),
    with no interior entry. Import data classification may also follow an early saved-register base."""
    if at+8>end:return None
    uses_ido_guard = False
    for scheduled in (False,True):
        if at-start<(24 if scheduled else 28):continue
        guard,bound,branch,delay,scale,hi,add,load,jump,slot=_guard(image,at,order,scheduled)
        branch_pc = guard+4
        if scheduled and guard-start >= 12:
            fp, candidate, clear_fp, step = struct.unpack_from(
                '>4I' if order == 'big' else '<4I', image, guard-12)
            index_reg, guard_reg = (candidate>>21)&31, (candidate>>16)&31
            # The FP branch skips two instructions that leave the switch's
            # bound and index intact. The bound runs in its normal delay slot.
            if (fp in (0x45000003, 0x45010003) and candidate>>26 == 11
                    and clear_fp & 0xffff07ff == 0x44800000 and step>>26 == 9
                    and (step>>21)&31 == (step>>16)&31
                    and (step>>16)&31 not in (index_reg, guard_reg)
                    and bound == step):
                guard -= 8
                bound = candidate
        index=(bound>>21)&31;condition=(bound>>16)&31;count=bound&0xffff
        dest=(jump>>21)&31;base=(hi>>16)&31;scaled=(scale>>11)&31
        if bound>>26!=11 or not 0<count<=MAX_ENTRIES or not index or not condition or condition==index:continue
        if branch>>26!=4 or {(branch>>21)&31,(branch>>16)&31}!={0,condition}:continue
        # The guard's delay slot can be NOP or clearing an unrelated register.
        clear=delay>>26==0 and delay&63 in (33,37) and not delay&0x03ff0000 and ((delay>>11)&31) not in (index,condition)
        if delay and not clear:continue
        if scale>>26 or scale&63 or (scale>>6)&31!=2 or (scale>>16)&31!=index or (scale>>21)&31:continue
        if hi>>26!=15 or not base or not scaled or base==scaled:continue
        if add>>26 or add&63!=33 or (add>>11)&31!=base or {(add>>21)&31,(add>>16)&31}!={base,scaled}:continue
        if load>>26!=35 or (load>>21)&31!=base or (load>>16)&31!=dest or jump&0xfc1fffff!=8 or not dest or slot:continue
        address=(((hi&0xffff)<<16)+signed(load))&0xffffffff
        break
    else:
        saved = _saved_register_guard(image,start,at,order) if allow_saved_base else None
        if saved is None:
            saved = _ido_register_guard(image,start,at,order)
            uses_ido_guard = True
        if saved is None:return None
        guard,branch,count,address = saved
        branch_pc = guard+4
    default=branch_pc+4+signed(branch)*4
    if not start<=default<end or guard<default<=at+4:return None
    result={'guard_address':vram+guard-start,'address':vram+at-start,'table_address':address,'entry_count':count,'state':'unresolved'}
    for p in range(start,end-3,4):
        w=int.from_bytes(image[p:p+4],order);op=w>>26
        if op in (1,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8):target=p+4+signed(w)*4
        elif op in (2,3):target=start+((((vram+p-start+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)
        else:continue
        # A normal FP branch can compute the bound in its delay slot,
        # then jump directly to the guard branch. Both paths run the bound.
        bound_in_slot = (p == guard-4 and target == branch_pc
                         and w >> 16 in (0x4500, 0x4501))
        if guard<target<=at and not guard<=p<=at and not bound_in_slot:
            return {**result,'reason':'an incoming edge bypasses the guard'}
    def destinations(data, prefix=0):
        targets=[prefix|int.from_bytes(data[p:p+4],order) for p in range(0,len(data),4)]
        if all(t%4==0 and vram<=t<vram+end-start and not vram+guard-start<t<=vram+at-start+4 for t in targets):
            return targets
        return None

    # The ROM judge already has an overlay-aware reader for the current
    # function. Its read is independent of the coverage scanner's mappings.
    if reader is not None:
        data = reader(address,count*4)
        targets = destinations(data) if data is not None and len(data)==count*4 else None
        return {**result,'state':'validated','targets':targets} if targets else result

    ordinary = [r for r in ranges if 'target_prefix' not in r]
    choices,error=locations(address,count*4,image,ordinary,mappings)
    if error:return {**result,'reason':error}
    valid=[]
    for offset,verified in choices:
        if targets := destinations(image[offset:offset+count*4]):
            valid.append((offset,verified,targets))
    if not valid:
        aliases = [r for r in ranges if r.get('target_prefix')==vram&0xe0000000
                   and (r.get('boot_copy') or uses_ido_guard and r.get('code_delta')==vram-start)]
        choices,error = locations(address,count*4,image,aliases)
        if error:return {**result,'reason':error}
        for offset,verified in choices:
            data = image[offset:offset+count*4]
            if all(word<0x20000000 for word, in struct.iter_unpack('>I' if order=='big' else '<I',data)):
                if targets := destinations(data,vram&0xe0000000):
                    valid.append((offset,verified,targets))
    if len(valid)!=1:return {**result,'reason':'no unique mapped table with all targets inside the candidate'}
    offset,verified,targets=valid[0]
    return {**result,'rom_offset':offset,'mapping_verified':verified,'targets':targets,
            'state':'validated' if verified else 'mapping_hypothesis',
            'reason':'bounded table; follow every case' if verified else 'review the raw-data mapping before following cases'}


def calls(image,functions,order):
    """Bounded agreeing-path dataflow; no inferred callback reachability."""
    from .mips_callback_flow import trace
    return trace(image,functions,order,MAX_WORDS,MAX_CALLS)


def consumers(image,functions,rows,order,ranges,mappings,unresolved=None):
    traced,limited=calls(image,functions,order)
    linked=set()
    for row in rows:
        found=[]
        for ref in row['pointer_references']:
            cells={(s['vram']+ref['rom_offset']-s['rom_start'],True) for s in ranges if s['rom_start']<=ref['rom_offset']<s['rom_end']}
            if not cells:cells={(ref['rom_offset']+m,False) for m in mappings}
            for call in traced:
                base,terms=call['load_address']
                for cell,verified in cells:
                    stride=next(iter(terms.values())) if len(terms)==1 else None
                    if not terms and base==cell:index=None
                    elif stride and 4<=stride<=256 and stride%4==0 and 0<=cell-base<stride*256 and (cell-base)%stride==0:index=(cell-base)//stride
                    else:continue
                    mapped,problem=locations(cell,4,image,ranges)
                    mapping_ok=verified and not problem and len(mapped)==1 and mapped[0][0]==ref['rom_offset']
                    linked.add((call['function'],call['address']))
                    found.append({'function':call['function'],'address':call['address'],'pointer_rom':ref['rom_offset'],
                                  'evidence_chain':call.get('evidence_chain',[]),'mapping_verified':bool(mapping_ok),'indexed':bool(terms),'stride':stride,'possible_index':index,
                                  'state':'cell_load_call' if mapping_ok and not terms else 'consumer_hypothesis','reason':problem or ('index reachability is unproven' if terms else ('reviewed cell mapping' if mapping_ok else 'raw-data mapping is unproven'))})
                    if len(found)>=8:break
                if len(found)>=8:break
            if len(found)>=8:break
        row['callback_consumers']=found
    if unresolved is not None:
        unresolved.extend({**c,'reason':'Runtime pointer or cell mapping unresolved; not linked to a candidate'} for c in traced if (c['function'],c['address']) not in linked)
    return limited
