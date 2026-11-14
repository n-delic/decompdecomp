"""Operator-reviewed MIPS extents: immutable previews, retained child records and guarded undo."""
import base64
import hashlib
import json
import struct
import time
from pathlib import Path

import rabbitizer

from ..network import NetworkError
from ..rom import split

FIELDS = ('name','vram','rom_offset','unit','size','n_insns','target','relocs','asm','status','meta','best_source','best_score','seed','seed_key')
PREFIX = 'boundary-repair:'


def reason(console, row):
    if row['status']=='matched':
        return None
    raw=row['target'] or b''
    if console.arch=='mips' and raw==bytes(4):
        return 'Single nop: possible padding, not a complete standalone function.'
    meta=json.loads(row['meta'] or '{}') if 'meta' in row.keys() else {}
    return console.boundary_reason(raw) or meta.get('boundary_tail_review')


def blocked(net, proj):
    if not proj or not getattr(net,'is_rom',lambda _:False)(proj):return set()
    from .code_coverage import _view, boundaries
    def build():
        names={r['function'] for r in boundaries(net,proj) if not r.get('override')}
        from .metadata_rows import rows
        return frozenset(r['id'] for r in rows(net.project_store(proj['id']),('id','name','gone_mark'),'SELECT id,name,gone_mark FROM functions WHERE project_id=?',proj['id'])
                         if r['gone_mark'] is not None or r['name'] in names)
    return _view(net,proj,'boundary-blocked',build)


def _rows(net, proj):
    return [dict(r) for r in net.project_store(proj['id']).all('SELECT * FROM functions WHERE project_id=? ORDER BY id',proj['id'])]


def _digest(value):
    if isinstance(value,bytes):return hashlib.sha256(value).hexdigest()
    def encode(item):
        if isinstance(item,bytes):return {'bytes':base64.b64encode(item).decode()}
        raise TypeError(type(item).__name__)
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=encode).encode()).hexdigest()


def _revision(net, proj, affected=()):
    # Unrelated workers may keep submitting candidates. Guard layout, matched C,
    # shared inputs and the edited rows, not incidental attempt counters elsewhere.
    ids=set(affected);rows=[]
    for row in _rows(net,proj):
        rows.append(row if row['id'] in ids else {k:row[k] for k in
                    ('id','name','vram','rom_offset','size','target','relocs','unit','status','matched_source','meta')})
    return _digest([dict(net.project_row(proj['slug'])),rows,
                    [list(r) for r in net.project_store(proj['id']).all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',proj['id'])],
                    [list(r) for r in net.project_store(proj['id']).all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]])


def _supported(net, slug):
    net._rom_only(slug,'boundary repair')
    proj=net.project_row(slug)
    if proj['arch']!='mips':raise NetworkError('Boundary repairs currently support mapped MIPS ROM projects. Other processors need an extent adapter.')
    return proj


def _image(net, proj):
    cfg=net.cfg(proj)
    try:image=(Path(proj['root'])/cfg['rom']['file']).read_bytes()
    except OSError as error:raise NetworkError('The original image is unavailable. Restore it before previewing a boundary repair.') from error
    segments=[split.Segment(**s) for s in cfg.get('segments',[])]
    return image,segments


def audit(net, slug):
    proj=_supported(net,slug)
    from .code_coverage import boundaries
    findings=[]
    rows=_rows(net,proj);by_name={r['name']:r for r in rows}
    for finding in boundaries(net,proj):
        fn=by_name[finding['function']];target=fn['target'] or b''
        last=int.from_bytes(target[-4:],'big' if proj['big_endian'] else 'little') if len(target)>=4 else 0
        # These are possible repairs, not evidence that adjacent entries belong together.
        suggested='delay_slot' if target and rabbitizer.Instruction(last).hasDelaySlot() else ''
        findings.append({**finding,'suggested_action':suggested})
    history=[]
    for r in net.project_store(proj['id']).all('SELECT key,value FROM settings WHERE key LIKE ?',PREFIX+str(proj['id'])+':%'):
        item=json.loads(r['value'])
        if not item.get('undone'):history.append({'key':r['key'],'function':item['function'],'action':item['action'],'time':item['time'],'evidence':item.get('evidence',''),'entries':[int(fid) for fid in item['before']]})
    return {'findings':findings,'history':sorted(history,key=lambda r:r['time'],reverse=True)[:30]}


def preview(net, slug, name, action, end=None, *, investigation=False):
    proj=_supported(net,slug);fn=dict(net._function(name,slug));rows=_rows(net,proj)
    if fn['status']=='matched':raise NetworkError('A matched function cannot be reclassified or resized.')
    meta=json.loads(fn['meta'] or '{}')
    if meta.get('gone'):raise NetworkError('This entry is archived. Undo its repair before editing it.')
    image,segments=_image(net,proj)
    start=fn['rom_offset'];address=fn['vram']
    if start is None or address is None or image[start:start+fn['size']]!=fn['target']:
        raise NetworkError('Stored bytes differ from the original mapped image. Reimport/review the mapping first.')
    owners=[s for s in segments if s.rom_start<=start<s.rom_end and s.vram+start-s.rom_start==address]
    if len(owners)!=1:raise NetworkError('This entry needs one unambiguous original image mapping.')
    owner=owners[0];pieces=[fn];newend=address+fn['size']
    if action=='join_previous':
        previous=[r for r in rows if r['id']!=fn['id'] and not json.loads(r['meta'] or '{}').get('gone') and r['unit']==fn['unit']
                  and r['rom_offset'] is not None and r['rom_offset']+r['size']==start and r['vram']+r['size']==address]
        if len(previous)!=1:raise NetworkError('No unique adjacent preceding piece in this source unit. Inspect mappings before joining.')
        pieces=[previous[0],fn];fn=previous[0];start=fn['rom_offset'];address=fn['vram']
    elif action=='delay_slot':
        last=int.from_bytes(fn['target'][-4:],'big' if proj['big_endian'] else 'little')
        if not rabbitizer.Instruction(last).hasDelaySlot():raise NetworkError('The last instruction does not require a delay slot.')
        newend+=4
    elif action=='adjust':
        if type(end) is not int:raise NetworkError('Enter an aligned end address.')
        newend=end
    elif action not in ('padding','data','keep'):
        raise NetworkError('Choose a boundary repair action.')
    if newend%4 or not address<newend<=address+65536:raise NetworkError('Choose an aligned extent of at most 64 KiB.')
    finish=start+newend-address
    if start<owner.rom_start or finish>owner.rom_end or finish>len(image):raise NetworkError('The proposed extent crosses the original segment mapping.')
    resizing=action in ('join_previous','delay_slot','adjust')
    if resizing:
        # An entirely absorbed piece stays archived under its own ID, with all history.
        for row in rows:
            if row['id']==fn['id'] or json.loads(row['meta'] or '{}').get('gone'):continue
            lo=row['rom_offset'];hi=lo+row['size'] if lo is not None else None
            physical=lo is not None and lo<finish and start<hi
            virtual=row['vram'] is not None and row['vram']<newend and address<row['vram']+row['size']
            if not (physical or virtual):continue
            if (not physical or row['unit']!=fn['unit'] or row['vram']-address!=lo-start or not start<=lo<hi<=finish):
                raise NetworkError('The preview overlaps another entry or overlay. Choose an extent that fully contains compatible pieces only.')
            if all(p['id']!=row['id'] for p in pieces):pieces.append(row)
        if newend<address+fn['size']:
            # Shrink only into a separately retained tail, never throw away original bytes.
            if net.project_store(proj['id']).one('SELECT 1 FROM functions WHERE project_id=? AND vram=? AND gone_mark IS NULL',proj['id'],newend):
                raise NetworkError('A function already starts at the proposed tail. Review a join instead.')
    if any(p['status']=='matched' for p in pieces):raise NetworkError('The repair would change an existing matched function.')
    if any(image[p['rom_offset']:p['rom_offset']+p['size']]!=p['target'] for p in pieces):
        raise NetworkError('A proposed piece differs from the original image. Review/reimport before joining.')
    ids=[p['id'] for p in pieces]
    if not investigation and any(net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?',fid) for fid in ids):
        raise NetworkError('Release the affected claims before changing boundaries.')
    raw=image[start:finish]
    from .. import arch
    from .boundary_evidence import assembly
    lines=assembly(arch.get(proj['arch']).boundary_instruction,image,owner,address,newend,
                   [address,fn['vram']+fn['size'],newend,*[p['vram'] for p in pieces]],proj['big_endian'])
    return {'function':name,'owner':fn['name'],'action':action,'end':newend,'start':address,
            'old_size':fn['size'],'old_end':fn['vram']+fn['size'],'new_size':len(raw),'affected':[{'id':p['id'],'name':p['name'],'size':p['size'],'start':p['vram'],'end':p['vram']+p['size']} for p in pieces],
            'revision':_revision(net,proj,ids),'image':_digest(image),'assembly':lines,
            'warning':'Safety checks reverify existing matches and guard mappings, overlaps and changed inputs. They do not prove the proposed boundary. Sources, attempts and notes are retained. Resized functions need a fresh match.',
            'pieces':pieces,'raw':raw,'unit':fn['unit'],'rom':start}


def public(plan):
    return {k:v for k,v in plan.items() if k not in ('pieces','raw')}


def _pack(row):
    return {k:({'bytes':base64.b64encode(row[k]).decode()} if isinstance(row[k],bytes) else row[k]) for k in FIELDS}


def _restore(db, fid, saved):
    values=[base64.b64decode(saved[k]['bytes']) if isinstance(saved[k],dict) and 'bytes' in saved[k] else saved[k] for k in FIELDS]
    db.execute('UPDATE functions SET '+','.join(k+'=?' for k in FIELDS)+' WHERE id=?',(*values,fid))


def _verify_matches(net, proj, log):
    def progress(value):
        getattr(log,'check_cancel',lambda:None)()
        getattr(log,'progress',lambda p:None)({**value,'phase':'Reverifying existing matches'})
        if value['done']%25==0 or value['done']==value['total']:
            log(f"{value['done']}/{value['total']} existing matches checked.")
    return net.regression_test(proj,None,header_for=lambda f:net._context(proj['id'],f['unit'])['header'],progress=progress)


def apply(net, slug, name, action, revision, image_hash, end=None, note='', log=print):
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before editing shared function boundaries.')
    if not isinstance(note,str) or not note.strip():raise NetworkError('Record the evidence for this boundary decision.')
    plan=preview(net,slug,name,action,end)
    if plan['revision']!=revision or plan['image']!=image_hash:raise NetworkError('The project or original image changed. Preview again.')
    log('Reverifying existing matches before the boundary decision…')
    broken=_verify_matches(net,proj,log)
    if broken:raise NetworkError('Existing matches could not be reverified: '+', '.join(n for n,_ in broken[:5]))
    getattr(log,'check_cancel',lambda:None)()
    result=_commit(net,proj,plan,name,action,plan['revision'],image_hash,note)
    log('Boundary decision saved. Original records and attempt history retained.')
    return result


def _commit(net, proj, plan, name, action, revision, image_hash, note):
    """Save one previewed decision with its undo record; matches were reverified by the caller."""
    rows=plan['pieces'];head=rows[0];old={str(p['id']):_pack(p) for p in rows}
    symbols=[dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])]
    key=PREFIX+str(proj['id'])+':'+str(time.time_ns());created=[]
    with net.project_store(proj['id']).tx() as db:
        if _revision(net,proj,[p['id'] for p in rows])!=revision:raise NetworkError('Project inputs changed while checking matches. Preview again.')
        if _digest(_image(net,proj)[0])!=image_hash:raise NetworkError('The original image changed. Preview again.')
        if any(db.execute('SELECT 1 FROM claims WHERE function_id=?',(p['id'],)).fetchone() for p in rows):raise NetworkError('An affected function was claimed. Release it and preview again.')
        meta=json.loads(head['meta'] or '{}');meta['boundary_decision']={'action':action,'evidence':note[:4000],'time':time.time()}
        if action in ('padding','data'):
            meta['gone']=True;meta['boundary_classification']=action
            db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),head['id']))
        elif action=='keep':
            meta['boundary_override']=True
            db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),head['id']))
        else:
            meta.pop('boundary_override',None);meta.pop('boundary_review',None)
            meta['boundary_archives']=[p['id'] for p in rows[1:]]
            for child in rows[1:]:
                childmeta=json.loads(child['meta'] or '{}');childmeta.update(gone=True,boundary_absorbed_by=head['name'])
                db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(childmeta),child['id']))
            # Rebuild relocation/assembly targets through the same console adapter as import.
            lines=[]
            for i in range(0,len(plan['raw']),4):
                word=int.from_bytes(plan['raw'][i:i+4],'big' if proj['big_endian'] else 'little')
                ins=rabbitizer.Instruction(word,head['vram']+i)
                lines.append(split.AsmLine(head['rom_offset']+i,head['vram']+i,word,ins.getOpcodeName(),ins.disassemble().split(None,1)[-1]))
            rf=split.RomFunction(head['name'],head['vram'],head['rom_offset'],head['unit'],lines,Path(),set())
            target=net._make_target(proj,rf,net.symbols(proj['id']))
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,status='open',meta=?,best_score=NULL,seed=NULL,seed_key=NULL WHERE id=?",
                       (len(plan['raw']),len(lines),target.code,json.dumps(target.to_json()),net.console(proj).function_asm(rf),json.dumps(meta),head['id']))
            db.execute('UPDATE symbols SET size=? WHERE project_id=? AND name=?',(len(plan['raw']),proj['id'],head['name']))
            if len(plan['raw'])<head['size']:
                tailname='boundary_tail_'+format(plan['end'],'08X')
                if db.execute('SELECT 1 FROM functions WHERE project_id=? AND name=?',(proj['id'],tailname)).fetchone():tailname+='_'+format(time.time_ns(),'x')
                offset=len(plan['raw']);tailmeta=json.dumps({'boundary_tail_review':'Retained tail from a reviewed boundary adjustment; confirm its entry point before agent work.'})
                tailraw=head['target'][offset:];taillines=[]
                for i in range(0,len(tailraw),4):
                    word=int.from_bytes(tailraw[i:i+4],'big' if proj['big_endian'] else 'little')
                    ins=rabbitizer.Instruction(word,plan['end']+i)
                    taillines.append(split.AsmLine(head['rom_offset']+offset+i,plan['end']+i,word,ins.getOpcodeName(),ins.disassemble().split(None,1)[-1]))
                tailrf=split.RomFunction(tailname,plan['end'],head['rom_offset']+offset,head['unit'],taillines,Path(),set())
                tailtarget=net._make_target(proj,tailrf,net.symbols(proj['id']))
                fid=db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (proj['id'],tailname,plan['end'],head['rom_offset']+offset,len(tailraw),len(taillines),tailtarget.code,json.dumps(tailtarget.to_json()),net.console(proj).function_asm(tailrf),head['unit'],tailmeta)).lastrowid
                created.append(fid)
        after={str(p['id']):_pack(dict(db.execute('SELECT * FROM functions WHERE id=?',(p['id'],)).fetchone())) for p in rows}
        for fid in created:after[str(fid)]=_pack(dict(db.execute('SELECT * FROM functions WHERE id=?',(fid,)).fetchone()))
        history={'evidence':note[:4000],'function':name,'action':action,'time':time.time(),'before':old,'after':after,'created':created,'symbols':symbols,
                 'image':image_hash,'symbol_after':[dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])],
                 'config':proj['config'],'contexts':_digest([list(r) for r in net.project_store(proj['id']).all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]),
                 'attempt_id':db.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]}
        db.execute('INSERT INTO settings VALUES(?,?)',(key,json.dumps(history)))
        net._event(db,proj['id'],None,'boundary',f"{action}: {name}. {note[:600]}")
    _invalidate(net,proj)
    return {'key':key,'function':plan['owner'],'action':action}


# Repairs the bytes alone decide. A missing delay slot is required by the instruction set; a piece whose code runs
# on into the next one is the same flow. Each is applied only when the repaired extent ends on a complete path, and
# nothing absorbed is a direct call target or makes its own stack frame (a separate function after a call that
# never returns). Everything else stays for review. An N64 game had 90 flagged entries, 56 of these shapes, which
# took a preview and a full match re-verification each by hand. A single word of alignment filler between a return
# and the next entry is archived as padding (automatic_boundaries.alignment_fill, which new imports apply).
SAFE_ROUNDS = 8
SAFE_NOTE = {'delay_slot':'Automatic safe fix: the final instruction required its delay slot; the extended extent ends on a complete path.',
             'join_previous':'Automatic safe fix: the preceding code runs on into this piece without a return; the joined extent ends on a complete path.',
             'padding':'Automatic safe fix: one word of 8-byte alignment filler between a function that returned and the next entry; nothing references it. It stays in the image, outside any function.'}


def safe_job_kind(slug):
    return 'Apply safe boundary fixes: '+slug


def _call_targets(image, segments, big_endian):
    """Direct `jal` destinations anywhere in the mapped image: an entry something calls is never absorbed."""
    out=set();fmt='>I' if big_endian else '<I'
    for s in segments:
        end=min(s.rom_end,len(image));start=s.rom_start+(-s.rom_start%4)
        for (word,),at in zip(struct.iter_unpack(fmt,image[start:end-(end-start)%4]),range(start,end,4)):
            if word>>26==3:out.add(((s.vram+at-s.rom_start+4)&0xF0000000)|((word&0x3FFFFFF)<<2))
    return out


def _decided(meta):
    return bool(meta.get('boundary_override') or 'you' in meta.get('not_c',{}) or
                any(meta.get(k) for k in ('set_aside','asm_twin','verified_assembly')))


def _flow_targets(image, segments, big_endian):
    """Every direct branch and jump destination in the mapped image (data read as code only adds more)."""
    out=set();fmt='>I' if big_endian else '<I'
    for s in segments:
        end=min(s.rom_end,len(image));start=s.rom_start+(-s.rom_start%4)
        for (w,),at in zip(struct.iter_unpack(fmt,image[start:end-(end-start)%4]),range(start,end,4)):
            op=w>>26;pc=s.vram+at-s.rom_start+4
            if op in (2,3):out.add((pc&0xF0000000)|((w&0x3FFFFFF)<<2))
            elif op in (1,4,5,6,7,20,21,22,23) or op==17 and (w>>21)&31==8:
                delta=w&0xffff;out.add(pc+(delta-0x10000 if delta&0x8000 else delta)*4)
    return out


def _pointed_at(image, big_endian, address):
    word=address.to_bytes(4,'big' if big_endian else 'little');at=image.find(word)
    while at>=0:
        if at%4==0:return True
        at=image.find(word,at+1)
    return False


def _fill(net, proj, plan, calls, image, flow):
    """Whether the previewed entry is one alignment word between a returned function and the next entry
    (automatic_boundaries.alignment_fill, the rule the importer now applies)."""
    from ..arch.mips import JR_RA, is_frame
    from ..rom.automatic_boundaries import alignment_fill
    fn=plan['pieces'][0];order='big' if proj['big_endian'] else 'little'
    if plan['action']!='padding' or len(plan['pieces'])!=1 or fn['size']!=4 or _decided(json.loads(fn['meta'] or '{}')):return False
    near={r['vram']:r for r in net.project_store(proj['id']).all(
        'SELECT name,vram,rom_offset,size,target FROM functions WHERE project_id=? AND gone_mark IS NULL AND vram BETWEEN ? AND ?',
        proj['id'],fn['vram']-65536,fn['vram']+4)}
    previous=next((r for r in near.values() if r['vram']+r['size']==fn['vram'] and r['rom_offset'] is not None
                   and r['rom_offset']+r['size']==fn['rom_offset']),None)
    following=near.get(fn['vram']+4)
    if previous is None or following is None or following['rom_offset']!=fn['rom_offset']+4:return False
    tail=previous['target'] or b'';head=following['target'] or b''
    return alignment_fill(fn['vram'],len(tail)>=8 and int.from_bytes(tail[-8:-4],order)==JR_RA,
                          len(head)>=4 and (following['vram'] in calls or is_frame(int.from_bytes(head[:4],order))),
                          fn['vram'] in flow or _pointed_at(image,proj['big_endian'],fn['vram']))


def _safe(net, proj, console, plan, calls, fill=None):
    """Why `plan` (a preview) can be applied without review, or None. `fill`: (image, flow targets) for padding."""
    if plan['action']=='padding':
        return plan if fill and _fill(net,proj,plan,calls,*fill) else None
    if reason(console,{'status':'open','target':plan['raw'],'meta':'{}'}):return None
    head=plan['pieces'][0];order='big' if proj['big_endian'] else 'little'
    # A person's decision (kept as separate, set aside by you) or verified assembly is never reopened by a rule.
    if any(_decided(json.loads(p['meta'] or '{}')) for p in plan['pieces']):return None
    for piece in sorted(plan['pieces'][1:],key=lambda p:p['vram']):
        if piece['vram'] in calls:return None
        # Zeros after a complete function are alignment padding, a person's call; a missing delay slot may be a nop.
        if plan['action']!='delay_slot' and not any(piece['target'] or b''):return None
        first=int.from_bytes((piece['target'] or b'')[:4],order)
        if len(piece['target'] or b'')>4 and first&0xFFFF8000==0x27BD8000:return None  # addiu $sp,$sp,-N
        # The code before the joint ends with a call and its delay slot: a call that never returns, so what
        # follows is likely its own function.
        before=plan['raw'][piece['vram']-head['vram']-8:piece['vram']-head['vram']-4]
        if plan['action']!='delay_slot' and len(before)==4:
            w=int.from_bytes(before,order)
            if w>>26==3 or (w>>26==0 and w&63==9):return None
    return plan


def _safe_plans(net, slug, proj, console, calls, skip, tick=lambda done,total:None, fill=None):
    from .code_coverage import boundaries
    found=[]
    rows={r['vram']:r for r in _rows(net,proj) if r['gone_mark'] is None and r['vram'] is not None}
    findings=sorted(boundaries(net,proj),key=lambda f:f['vram'] or 0)
    for index,finding in enumerate(findings):
        tick(index,len(findings))
        if finding['override'] or finding['function'] in skip:continue
        fn=net._function(finding['function'],slug)
        following=rows.get(fn['vram']+fn['size']) if fn['vram'] is not None else None
        tries=[(finding['function'],'padding')] if fn['size']==4 else []
        tries+=[(finding['function'],'delay_slot'),(finding['function'],'join_previous')]
        if following and following['unit']==fn['unit']:tries.append((following['name'],'join_previous'))
        for name,action in tries:
            try:plan=preview(net,slug,name,action)
            except NetworkError:continue
            if _safe(net,proj,console,plan,calls,fill):
                found.append((finding['function'],name,action));break
    return found


def apply_safe(net, slug, log=print):
    """Apply every repair the original bytes settle, one undoable decision each; leave the rest for review."""
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before editing shared function boundaries.')
    progress=getattr(log,'progress',lambda value:None);cancel=getattr(log,'check_cancel',lambda:None)
    from .code_coverage import boundaries
    total=len([f for f in boundaries(net,proj) if not f['override']])
    if not total:
        log('No incomplete entries need review.');return {'fixed':[],'remaining':0}
    image,segments=_image(net,proj)
    calls=_call_targets(image,segments,proj['big_endian']);fill=(image,_flow_targets(image,segments,proj['big_endian']))
    log(f'Checking {total} flagged entries for repairs the original bytes settle…')
    log('Reverifying existing matches before any boundary decision…')
    broken=_verify_matches(net,proj,log)
    if broken:raise NetworkError('Existing matches could not be reverified: '+', '.join(n for n,_ in broken[:5]))
    console=net.console(proj);fixed=[];failed=set()
    for _ in range(SAFE_ROUNDS):
        cancel()
        found=_safe_plans(net,slug,proj,console,calls,failed,lambda done,count:progress({'phase':'Checking flagged entries','done':done,'total':count}),fill)
        if not found:break
        for flagged,name,action in found:
            cancel()
            if any(net._function(n,slug)['gone_mark'] is not None for n in {flagged,name}):continue  # absorbed this round
            try:
                plan=preview(net,slug,name,action)
                if not _safe(net,proj,console,plan,calls,fill):continue
                result=_commit(net,proj,plan,name,action,plan['revision'],plan['image'],SAFE_NOTE[action])
            except NetworkError as error:
                # A worker claimed a piece or the layout moved under it: leave it for review rather than retry.
                failed.add(flagged);log(f'{flagged}: left for review ({error})');continue
            fixed.append({**result,'flagged':flagged})
            log(f"{flagged}: {action.replace('_',' ')} → {result['function']}")
            progress({'phase':'Applying safe fixes','done':len(fixed),'total':total})
    remaining=len([f for f in boundaries(net,proj) if not f['override']])
    log(f'Applied {len(fixed)} safe fixes; {remaining} entries still need review. Each fix can be undone from Repair history.')
    return {'fixed':fixed,'remaining':remaining}


def _invalidate(net, proj):
    for attr in ('_syms','_xrefs','_ops','_warmed'):
        getattr(net,attr,{}).pop(proj['id'],None)
    net.twins.forget(proj);net.bump_generation(proj['id'])


def undo(net,slug,key,log=print):
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before undoing boundary changes.')
    if not isinstance(key,str) or not key.startswith(PREFIX+str(proj['id'])+':'):raise NetworkError('Choose a repair in this project.')
    row=net.project_store(proj['id']).one('SELECT value FROM settings WHERE key=?',key)
    if not row:raise NetworkError('Repair history is unavailable.')
    history=json.loads(row['value'])
    if history.get('undone'):raise NetworkError('This decision was already undone.')
    if proj['config']!=history['config'] or _digest([list(r) for r in net.project_store(proj['id']).all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])])!=history['contexts']:
        raise NetworkError('Compiler settings, mappings or headers changed since this repair. Review those changes before undoing.')
    if _digest(_image(net,proj)[0])!=history['image']:raise NetworkError('The original image changed since this repair. Reimport/review it before undoing.')
    if history['symbol_after'] and [dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],history['symbol_after'][0]['name'])]!=history['symbol_after']:
        raise NetworkError('The repaired symbol changed. Undo would overwrite newer work.')
    ids=[int(fid) for fid in history['after']]
    revision=_revision(net,proj,ids)
    log('Checking existing matches before undo…')
    if _verify_matches(net,proj,log):
        raise NetworkError('Existing matches could not be reverified. Undo was not applied.')
    with net.project_store(proj['id']).tx() as db:
        if _revision(net,proj,ids)!=revision or _digest(_image(net,proj)[0])!=history['image']:raise NetworkError('Project or original image changed during undo verification. Try again.')
        for fid in ids:
            current=db.execute('SELECT * FROM functions WHERE id=? AND project_id=?',(fid,proj['id'])).fetchone()
            if not current or current['status']=='matched' or db.execute('SELECT 1 FROM claims WHERE function_id=?',(fid,)).fetchone():
                raise NetworkError('An affected function is matched, changed or claimed. Review its newer work before undoing.')
            if db.execute('SELECT 1 FROM attempts WHERE function_id=? AND id>?',(fid,history['attempt_id'])).fetchone():
                raise NetworkError('New attempts exist on the repaired entries. Review their work before undoing.')
            if str(fid) in history['after'] and _pack(current)!=history['after'][str(fid)]:raise NetworkError('The repaired entries changed. Undo would overwrite newer work.')
        for fid,saved in history['before'].items():_restore(db,int(fid),saved)
        for fid in history['created']:
            meta=json.loads(db.execute('SELECT meta FROM functions WHERE id=?',(fid,)).fetchone()[0] or '{}');meta['gone']=True
            db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),fid))
        for symbol in history['symbols']:db.execute('UPDATE symbols SET size=? WHERE project_id=? AND name=?',(symbol['size'],proj['id'],symbol['name']))
        history['undone']=True;db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(history),key))
        net._event(db,proj['id'],None,'boundary','Undid boundary decision for '+history['function'])
    _invalidate(net,proj)
    return {'undone':True}
