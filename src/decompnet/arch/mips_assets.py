"""Bounded, block-local argument and buffer evidence for ROM Explorer.

Unlike import-time mapping this never changes addresses or claims runtime reachability.
Joins, branches, unsupported instructions and call clobbers discard uncertain values.
Delay slots are applied before recording a call; likely branches supply no fallthrough facts.
"""
import struct

from .mips import _mips_uses, fields, signed_immediate

MAX_WORDS = 32768
MAX_EVENTS = 2048
CALLER_SAVED = set(range(1,16)) | {24,25,28,31}


def constant(value, at):return dict(kind='constant',value=value & 0xffffffff,offset=0,evidence=[at])


def plus(value, delta, at):
    if value is None:return None
    if value['kind']=='constant':return constant(value['value']+delta,at) | {'evidence':(value['evidence']+[at])[-8:]}
    return {**value,'offset':value.get('offset',0)+delta,'evidence':(value['evidence']+[at])[-8:]}


def read(vram, code, *, big_endian=True, argument_count=4, gp=0, cpu='', check=lambda:None):
    """Calls, loads and stores with explicit constant/symbolic arguments in one block.

    Stack slots retain stored values; memory loads elsewhere stay symbolic rather
    than reading mutable ROM initialization bytes as current runtime state.
    """
    words=struct.unpack(('>' if big_endian else '<')+f'{len(code)//4}I',code[:len(code)//4*4])
    joins=set()
    for k,w in enumerate(words):
        op,rs,_,_,_=fields(w)
        if op in (1,4,5,6,7,20,21,22,23) or op==17 and rs==8:
            joins.add(k+1+signed_immediate(w))
        elif op==2:joins.add(((((vram+k*4+4)&0xf0000000)|((w&0x3ffffff)<<2))-vram)//4)
    def initial(entry=False):
        values={0:constant(0,vram),29:dict(kind='stack',value='frame',offset=0,evidence=[])}
        if entry:
            values.update({r:dict(kind='argument',value=r-4,offset=0,evidence=[]) for r in range(4,4+argument_count)})
            if gp:values[28]=constant(gp,vram)
        return values
    regs=initial(True);stack={};writes=[];events=[];pending=None;reset_at=None;block=0
    for k,w in enumerate(words[:MAX_WORDS]):
        if k%128==0:check()
        if len(events)>=MAX_EVENTS:break
        if k and (k in joins or reset_at==k):
            regs=initial();stack={};writes=[];pending=None;block+=1
        at=vram+k*4;op,rs,rt,rd,fun=fields(w);imm=signed_immediate(w)
        value=None;dest=None;uses=_mips_uses(w,cpu)
        a,b=regs.get(rs),regs.get(rt)
        if op==15:dest=rt;value=constant((w&0xffff)<<16,at)
        elif op in (8,9):dest=rt;value=plus(a,imm,at)
        elif op in (12,13,14) and a and a['kind']=='constant':
            dest=rt;n=a['value'];u=w&0xffff
            value=constant(n&u if op==12 else n|u if op==13 else n^u,at)
        elif op==0 and fun in (0,2,3) and b and b['kind']=='constant':
            shift=(w>>6)&31;n=b['value'];dest=rd
            value=constant(n<<shift if fun==0 else n>>shift if fun==2 else (n if n<0x80000000 else n-0x100000000)>>shift,at)
        elif op==0 and fun in (0x21,0x25,0x2d):
            dest=rd
            if a and b and b['kind']=='constant' and (b['value']==0 or fun!=0x25):value=plus(a,b['value'],at)
            elif a and b and a['kind']=='constant' and a['value']==0:value=plus(b,0,at)
            elif a and b and a['kind']==b['kind']=='constant':value=constant(a['value']|b['value'],at)
        elif op in (0x20,0x21,0x23,0x24,0x25,0x27,0x28,0x29,0x2b,0x31,0x35,0x39,0x3d):
            width={0x20:1,0x24:1,0x28:1,0x21:2,0x25:2,0x29:2,0x35:8,0x3d:8}.get(op,4)
            place=plus(a,imm,at);store=op in (0x28,0x29,0x2b,0x39,0x3d)
            if place:
                if place['kind']=='stack':
                    if store:events.append(dict(kind='store',instruction=at,place=place,width=width,value=b if store else None,block=block))
                    slot=place['offset']
                    if store:
                        # Any overlapping write invalidates a prior pointer-sized spill.
                        for key in list(stack):
                            if key<slot+width and slot<key+4:stack.pop(key)
                        if width==4 and b:stack[slot]=plus(b,0,at)
                    elif width==4:value=stack.get(slot)
                else:
                    events.append(dict(kind='store' if store else 'load',instruction=at,place=place,width=width,value=b if store else None,block=block))
                    if not store and width==4:value=dict(kind='memory',value=at,offset=0,evidence=[at])
            elif store:events.append(dict(kind='store',instruction=at,place=None,width=width,value=b if store else None,block=block))
            if store:
                if place and place['kind']!='stack':
                    kept=[]
                    for entry in writes:
                        old=entry['place']
                        if old['kind']==place['kind']=='constant':
                            left,right=old['value'],place['value']
                        elif old['kind']==place['kind'] and old['value']==place['value']:
                            left,right=old.get('offset',0),place.get('offset',0)
                        else:continue
                        if left+4<=right or right+width<=left:kept.append(entry)
                    writes=kept;stack={}
                    if width==4 and b:writes.append(dict(place=place,value=plus(b,0,at)))
                elif place and place['kind']=='stack':writes=[]
                elif place is None:writes=[];stack={}
            if not store and op not in (0x31,0x35):dest=rt
        if op in (0x1f,0x2a,0x2c,0x2d,0x2e,0x38,0x3a,0x3b,0x3c,0x3e,0x3f):
            # Wide/partial and conditional stores are barriers even when their
            # exact byte lane or vector behavior is outside this scalar reader.
            events.append(dict(kind='store',instruction=at,place=None,width=16,block=block))
            stack={};writes=[]
        if uses is None:
            regs=initial();stack={};writes=[];pending=None;block+=1
        else:
            for reg in uses[1]-{0}:regs.pop(reg,None)
        if dest and value is not None:regs[dest]=value
        if pending is not None and pending['slot']==k:
            args=[regs.get(r) for r in range(4,4+argument_count)]
            sp=regs.get(29)
            for n in range(argument_count,8):
                args.append(stack.get(sp['offset']+16+(n-4)*4) if sp and sp['kind']=='stack' and argument_count==4 else None)
            memory=writes+[dict(place=dict(kind='stack',value='frame',offset=slot,evidence=[]),value=value) for slot,value in stack.items()]
            events.append(dict(kind='call',instruction=pending['at'],target=pending['target'],args=args,memory=memory,block=block))
            for reg in CALLER_SAVED:regs.pop(reg,None)
            regs[2]=dict(kind='return',value=pending['at'],offset=0,evidence=[pending['at']])
            # Unknown callees can write stack memory via passed pointers.
            stack={};writes=[];pending=None
        if op==3:
            pending=dict(slot=k+1,at=at,target=((at+4)&0xf0000000)|((w&0x3ffffff)<<2))
        elif op==0 and fun==9:
            pending=dict(slot=k+1,at=at,target=a['value'] if a and a['kind']=='constant' else None)
        elif op in (1,2,4,5,6,7,20,21,22,23) or op==0 and fun==8 or op==17 and rs==8:
            reset_at=k+2
    return dict(events=events,branched=bool(joins),limited=len(words)>MAX_WORDS or len(events)>=MAX_EVENTS,
                note='Block-local static argument evidence; branches and unknown values are not resolved. Calls may fail or be asynchronous.')
