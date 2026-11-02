"""Bounded MIPS indirect-transfer evidence. No guessed mapping becomes an executable edge."""
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


def jump_table(image,start,end,vram,at,order,ranges=(),mappings=()):
    """Recognize one exact guard/scale/LUI/add/load/JR idiom, with no interior entry."""
    if at-start<28 or at+8>end:return None
    words=[int.from_bytes(image[p:p+4],order) for p in range(at-28,at+8,4)]
    bound,branch,delay,scale,hi,add,load,jump,slot=words
    index=(bound>>21)&31;condition=(bound>>16)&31;count=bound&0xffff
    dest=(jump>>21)&31;base=(hi>>16)&31;scaled=(scale>>11)&31
    if bound>>26!=11 or not 0<count<=MAX_ENTRIES or not index or not condition or condition==index:return None
    if branch>>26!=4 or {(branch>>21)&31,(branch>>16)&31}!={0,condition}:return None
    # The guard's delay slot can be NOP or clearing an unrelated register.
    clear=delay>>26==0 and delay&63 in (33,37) and not delay&0x03ff0000 and ((delay>>11)&31) not in (index,condition)
    if delay and not clear:return None
    if scale>>26 or scale&63 or (scale>>6)&31!=2 or (scale>>16)&31!=index or (scale>>21)&31:return None
    if hi>>26!=15 or not base or not scaled or base==scaled:return None
    if add>>26 or add&63!=33 or (add>>11)&31!=base or {(add>>21)&31,(add>>16)&31}!={base,scaled}:return None
    if load>>26!=35 or (load>>21)&31!=base or (load>>16)&31!=dest or jump&0xfc1fffff!=8 or not dest or slot:return None
    guard=at-28
    default=guard+8+signed(branch)*4
    if not start<=default<end or guard<default<=at+4:return None
    address=(((hi&0xffff)<<16)+signed(load))&0xffffffff
    result={'guard_address':vram+guard-start,'address':vram+at-start,'table_address':address,'entry_count':count,'state':'unresolved'}
    for p in range(start,end-3,4):
        w=int.from_bytes(image[p:p+4],order);op=w>>26
        if op in (1,4,5,6,7,20,21,22,23) or (op==17 and (w>>21)&31==8):target=p+4+signed(w)*4
        elif op in (2,3):target=start+((((vram+p-start+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)
        else:continue
        if guard<target<=at and not guard<=p<=at:return {**result,'reason':'an incoming edge bypasses the guard'}
    choices,error=locations(address,count*4,image,ranges,mappings)
    if error:return {**result,'reason':error}
    valid=[]
    for offset,verified in choices:
        targets=[int.from_bytes(image[p:p+4],order) for p in range(offset,offset+count*4,4)]
        if all(t%4==0 and vram<=t<vram+end-start and not vram+guard-start<t<=vram+at-start+4 for t in targets):valid.append((offset,verified,targets))
    if len(valid)!=1:return {**result,'reason':'no unique mapped table with all targets inside the candidate'}
    offset,verified,targets=valid[0]
    return {**result,'rom_offset':offset,'mapping_verified':verified,'targets':targets,
            'state':'validated' if verified else 'mapping_hypothesis',
            'reason':'bounded table; follow every case' if verified else 'review the raw-data mapping before following cases'}


def calls(image,functions,order):
    """Track affine addresses through a straight-line block to load -> JALR.

    Unknown operations end a trace. Indexed cells are hypotheses: the index's
    reachability/range is not inferred from this local expression.
    """
    result=[];words=0;limited=False
    for fn in functions:
        if fn.get('handwritten'):continue
        regs={};epoch=0;skip_delay=False
        def value(r):return (0,{}) if not r else regs.get(r,(0,{f'{epoch}:{r}':1}))  # noqa: B023 synchronous interpreter state
        def plus(a,b,sign=1):
            if not isinstance(a,tuple) or not isinstance(b,tuple):return None
            coeff=dict(a[1])
            for k,v in b[1].items():coeff[k]=coeff.get(k,0)+sign*v
            coeff={k:v for k,v in coeff.items() if v}
            if len(coeff)>2 or any(abs(v)>256 for v in coeff.values()):return None
            return ((a[0]+sign*b[0])&0xffffffff,coeff)
        for at in range(fn['rom'],fn['rom']+fn['size']-3,4):
            words+=1
            if words>MAX_WORDS or len(result)>=MAX_CALLS:return result,True
            if skip_delay:
                skip_delay=False;regs={};epoch+=1;continue
            w=int.from_bytes(image[at:at+4],order);op=w>>26;rs=(w>>21)&31;rt=(w>>16)&31;rd=(w>>11)&31;f=w&63
            pc=fn['vram']+at-fn['rom'];out=None;destination=None
            if op==0 and f==9:
                address=regs.get(rs)
                if rd and isinstance(address,dict) and address.get('load') is not None:
                    result.append({'function':fn['name'],'address':pc,'load_address':address['load']})
                regs={};epoch+=1;skip_delay=True;continue
            if op in (1,2,3,4,5,6,7,20,21,22,23) or (op==17 and rs==8) or (op==0 and f==8):
                regs={};epoch+=1;skip_delay=True;continue
            if op==15:out=((w&0xffff)<<16,{});destination=rt
            elif op==9:out=plus(value(rs),(signed(w),{}));destination=rt
            elif op==13:
                a=value(rs);out=((a[0]|(w&0xffff)),{}) if isinstance(a,tuple) and not a[1] else None;destination=rt
            elif op==0 and f in (33,35):
                out=value(rs) if rt==0 else (value(rt) if rs==0 and f==33 else plus(value(rs),value(rt),1 if f==33 else -1))
                destination=rd
            elif op==0 and f==0:
                a=value(rt);n=(w>>6)&31
                if isinstance(a,tuple) and all(abs(v<<n)<=256 for v in a[1].values()):out=((a[0]<<n)&0xffffffff,{k:v<<n for k,v in a[1].items()})
                destination=rd
            elif op==35:
                a=plus(value(rs),(signed(w),{}));out={'load':a} if a is not None else None;destination=rt
            else:
                # No guessed preservation through instructions, branches or calls.
                regs={};epoch+=1;continue
            if destination:
                if out is None:regs[destination]=(0,{f'unknown:{pc}:{destination}':1})
                else:regs[destination]=out
    return result,limited


def consumers(image,functions,rows,order,ranges,mappings):
    traced,limited=calls(image,functions,order)
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
                    found.append({'function':call['function'],'address':call['address'],'pointer_rom':ref['rom_offset'],
                                  'mapping_verified':bool(mapping_ok),'indexed':bool(terms),'stride':stride,'possible_index':index,
                                  'state':'cell_load_call' if mapping_ok and not terms else 'consumer_hypothesis','reason':problem or ('index reachability is unproven' if terms else ('reviewed cell mapping' if mapping_ok else 'raw-data mapping is unproven'))})
                    if len(found)>=8:break
                if len(found)>=8:break
            if len(found)>=8:break
        row['callback_consumers']=found
    return limited
