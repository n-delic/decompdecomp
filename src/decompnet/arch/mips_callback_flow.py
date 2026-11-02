"""Conservative scalar MIPS callback dataflow, bounded to each declared function.

Only identical incoming facts survive joins. Stack slots are private to a known
SP offset; unknown stores and calls invalidate memory facts. This proves static
load/call chains, not that a path or an indexed table element executes.
"""
from collections import deque


def trace(image, functions, order, max_words, max_calls):
    results=[];steps=0
    for fn in functions:
        if fn.get('handwritten'):continue
        start=fn['rom'];end=min(start+fn['size'],len(image));base=fn['vram']
        # Values are affine expressions or loaded values retaining field origins.
        def expr(n=0, terms=()):return ('expr',n&0xffffffff,tuple(sorted(terms)))
        def scalar(v):return expr(0,((v[3],1),)) if v[0]=='load' else v
        def add(a,b,sign=1):
            a=scalar(a);b=scalar(b);terms=dict(a[2])
            for k,v in b[2]:terms[k]=terms.get(k,0)+sign*v
            terms={k:v for k,v in terms.items() if v}
            if len(terms)>2 or any(abs(v)>256 for v in terms.values()):return None
            return expr(a[1]+sign*b[1],terms.items())
        def merge(a,b):return tuple({k:v for k,v in x.items() if y.get(k)==v} for x,y in zip(a,b))
        incoming={start:({29:expr(0,(('stack',1),))},{})};queue=deque([start]);records={}
        def offer(at,state,start=start,end=end,incoming=incoming,queue=queue):
            if not start<=at<end or at%4:return
            old=incoming.get(at);new=state if old is None else merge(old,state)
            if old!=new:incoming[at]=new;queue.append(at)
        def instruction(at,state,base=base,start=start):
            regs,stack=map(dict,state);w=int.from_bytes(image[at:at+4],order)
            op=w>>26;rs=(w>>21)&31;rt=(w>>16)&31;rd=(w>>11)&31;f=w&63
            pc=base+at-start;imm=(w&65535)-(65536 if w&32768 else 0)
            def val(r):return expr() if not r else regs.get(r,expr(0,((f'unknown:{pc}:{r}',1),)))
            out=None;dest=None
            if op==15:out=expr((w&65535)<<16);dest=rt
            elif op==9:out=add(val(rs),expr(imm));dest=rt
            elif op==13:
                a=val(rs);out=expr(a[1]|(w&65535)) if a[0]=='expr' and not a[2] else None;dest=rt
            elif op==0 and f in (33,35,37):
                dest=rd
                if rt==0:out=val(rs)
                elif rs==0 and f!=35:out=val(rt)
                elif f!=37:out=add(val(rs),val(rt),1 if f==33 else -1)
            elif op==0 and f==0:
                a=scalar(val(rt));n=(w>>6)&31;dest=rd
                if all(abs(v<<n)<=256 for _,v in a[2]):out=expr(a[1]<<n,((k,v<<n) for k,v in a[2]))
            elif op in (35,43):
                address=add(val(rs),expr(imm))
                private=address is not None and address[2]==(('stack',1),)
                if op==43:
                    if private:
                        v=val(rt)
                        if v[0]=='load':v=(*v[:4],v[4]+((pc,'stack spill'),))
                        stack[address[1]]=v
                    else:stack.clear()
                else:
                    dest=rt
                    if private:
                        out=stack.get(address[1])
                        if out and out[0]=='load':out=(*out[:4],out[4]+((pc,'stack restore'),))
                    elif address is not None:
                        # A loaded record pointer stays opaque, but its field chain
                        # remains available for review instead of assuming its value.
                        parent=val(rs);chain=parent[4] if parent[0]=='load' else ()
                        out=('load',address,None,f'load:{pc}',chain+((pc,f'load field {imm:+d}'),))
            elif w==0:pass
            else:
                # Unsupported semantics cannot preserve guessed register aliases.
                regs.clear();stack.clear()
            if dest:
                if out is None:regs.pop(dest,None)
                else:
                    if out[0]=='load' and not (op==35):out=(*out[:4],out[4]+((pc,'register copy'),))
                    regs[dest]=out
            return regs,stack
        while queue:
            at=queue.popleft();steps+=1
            if steps>max_words:return results,True
            state=incoming[at];w=int.from_bytes(image[at:at+4],order);op=w>>26;rs=(w>>21)&31;rd=(w>>11)&31;f=w&63
            conditional=op in (1,4,5,6,7,20,21,22,23) or (op==17 and rs==8)
            transfer=conditional or op in (2,3) or (op==0 and f in (8,9))
            if not transfer:offer(at+4,instruction(at,state));continue
            pc=base+at-start
            # Reprocessing after a join replaces evidence, removing stale chains.
            records.pop(at,None)
            if op==0 and f==9 and rd:
                v=state[0].get(rs)
                if v and v[0]=='load':
                    a=v[1];records[at]={'function':fn['name'],'address':pc,'load_address':(a[1],dict(a[2])),
                        'evidence_chain':[{'address':p,'operation':label} for p,label in v[4]+((pc,'indirect call'),)]}
            if at+8>end:continue
            slot=int.from_bytes(image[at+4:at+8],order);slotop=slot>>26
            if slotop in (1,2,3,4,5,6,7,20,21,22,23) or (slotop==0 and slot&63 in (8,9)) or (slotop==17 and (slot>>21)&31==8):continue
            after=instruction(at+4,state)
            if conditional:
                imm=(w&65535)-(65536 if w&32768 else 0)
                # Link branches modify RA; branch-likely annuls the untaken slot.
                if op==1 and (w>>16)&31>=16:continue
                offer(at+4+imm*4,after)
                offer(at+8,state if op in (20,21,22,23) or (op in (1,17) and w&0x20000) else after)
            elif op==2:
                target=((pc+4)&0xf0000000)|((w&0x3ffffff)<<2);offer(start+target-base,after)
            elif op==3 or (op==0 and f==9):
                # Preserve only the stack base; the callee may change all memory.
                sp=after[0].get(29);offer(at+8,({29:sp} if sp else {},{}))
        results.extend(records.values())
        if len(results)>=max_calls:return results[:max_calls],True
    return results,False
