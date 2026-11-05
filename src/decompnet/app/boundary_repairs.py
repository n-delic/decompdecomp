"""Operator-reviewed MIPS extents: immutable previews, retained child records and guarded undo."""
import base64
import hashlib
import json
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
    from .code_coverage import boundaries
    names={r['function'] for r in boundaries(net,proj) if not r.get('override')}
    return {r['id'] for r in net.store.all('SELECT id,name,gone_mark FROM functions WHERE project_id=?',proj['id']) if r['gone_mark'] is not None or r['name'] in names}


def _rows(net, proj):
    return [dict(r) for r in net.store.all('SELECT * FROM functions WHERE project_id=? ORDER BY id',proj['id'])]


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
                    [list(r) for r in net.store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',proj['id'])],
                    [list(r) for r in net.store.all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]])


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
    for r in net.store.all('SELECT key,value FROM settings WHERE key LIKE ?',PREFIX+str(proj['id'])+':%'):
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
            if net.store.one('SELECT 1 FROM functions WHERE project_id=? AND vram=? AND gone_mark IS NULL',proj['id'],newend):
                raise NetworkError('A function already starts at the proposed tail. Review a join instead.')
    if any(p['status']=='matched' for p in pieces):raise NetworkError('The repair would change an existing matched function.')
    if any(image[p['rom_offset']:p['rom_offset']+p['size']]!=p['target'] for p in pieces):
        raise NetworkError('A proposed piece differs from the original image. Review/reimport before joining.')
    ids=[p['id'] for p in pieces]
    if not investigation and any(net.store.one('SELECT 1 FROM claims WHERE function_id=?',fid) for fid in ids):
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
    rows=plan['pieces'];head=rows[0];old={str(p['id']):_pack(p) for p in rows}
    symbols=[dict(r) for r in net.store.all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])]
    key=PREFIX+str(proj['id'])+':'+str(time.time_ns());created=[]
    with net.store.tx() as db:
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
                 'image':image_hash,'symbol_after':[dict(r) for r in net.store.all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])],
                 'config':proj['config'],'contexts':_digest([list(r) for r in net.store.all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]),
                 'attempt_id':db.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]}
        db.execute('INSERT INTO settings VALUES(?,?)',(key,json.dumps(history)))
        net._event(db,proj['id'],None,'boundary',f"{action}: {name}. {note[:600]}")
    _invalidate(net,proj)
    log('Boundary decision saved. Original records and attempt history retained.')
    return {'key':key,'function':plan['owner'],'action':action}


def _invalidate(net, proj):
    for attr in ('_syms','_xrefs','_ops','_warmed'):
        getattr(net,attr,{}).pop(proj['id'],None)
    net.twins.forget(proj);net.bump_generation(proj['id'])


def undo(net,slug,key,log=print):
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before undoing boundary changes.')
    if not isinstance(key,str) or not key.startswith(PREFIX+str(proj['id'])+':'):raise NetworkError('Choose a repair in this project.')
    row=net.store.one('SELECT value FROM settings WHERE key=?',key)
    if not row:raise NetworkError('Repair history is unavailable.')
    history=json.loads(row['value'])
    if history.get('undone'):raise NetworkError('This decision was already undone.')
    if proj['config']!=history['config'] or _digest([list(r) for r in net.store.all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])])!=history['contexts']:
        raise NetworkError('Compiler settings, mappings or headers changed since this repair. Review those changes before undoing.')
    if _digest(_image(net,proj)[0])!=history['image']:raise NetworkError('The original image changed since this repair. Reimport/review it before undoing.')
    if history['symbol_after'] and [dict(r) for r in net.store.all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],history['symbol_after'][0]['name'])]!=history['symbol_after']:
        raise NetworkError('The repaired symbol changed. Undo would overwrite newer work.')
    ids=[int(fid) for fid in history['after']]
    revision=_revision(net,proj,ids)
    log('Checking existing matches before undo…')
    if _verify_matches(net,proj,log):
        raise NetworkError('Existing matches could not be reverified. Undo was not applied.')
    with net.store.tx() as db:
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
