"""Verify locally indexed SDK objects; signatures are leads, the ROM linker is the judge."""
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ..elfobj import _function_symbols, section_target
from ..rom import match
from ..rom.symbols import Symbol
from .source_assembly import fingerprint, verified

PREFIX='sdk-object-reference:'


def index(db, path, corpus):
    """Store provenance only. Never copy SDK code into settings or fetch a library."""
    path=Path(path).resolve()
    db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
               (PREFIX+corpus,json.dumps({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})))


def objects(net, hint):
    from .twins import _ar_members, object_prints
    for corpus in hint.get('corpora',[])[:4]:
        row=net.store.one('SELECT value FROM settings WHERE key=?',PREFIX+corpus)
        if not row:continue
        record=json.loads(row['value']);path=Path(record['path'])
        if not path.is_file() or path.stat().st_size>100*1024*1024:continue
        try:data=path.read_bytes()
        except OSError:continue
        if hashlib.sha256(data).hexdigest()!=record['sha256']:continue
        stat=path.stat();record={**record,'stamp':[stat.st_mtime_ns,stat.st_size]}
        members=_ar_members(data) if path.suffix.lower()=='.a' else [data]
        for member in members:
            try:prints=object_prints(member)
            except (ELFError,ValueError,KeyError):continue
            for name in hint.get('names',[]):
                if name in prints:yield member,name,prints[name],record,corpus


def link(net, proj, fn, obj, name):
    """Infer only consistent external relocation bindings; then verify every code/data byte.

    Internal data remains owned by the linker and must match the ROM. No symbol map
    or shared header is edited. Other processors/imported builds need their own linker adapter.
    """
    if proj['arch']!='mips' or not net.is_rom(proj) or net.fork_slug(proj['slug']):return None
    elf=ELFFile(BytesIO(obj))
    if elf['e_machine']!='EM_MIPS' or elf.little_endian==bool(net.cfg(proj).get('big_endian',True)):return None
    found=_function_symbols(elf).get(name)
    if not found:return None
    shndx,start,size=found
    if size!=fn['size']:return None
    code=elf.get_section(shndx).data()[start:start+size]
    raw=match.raw_relocs(elf,shndx,start,size,code);target=net._rom_target(fn)
    at={(off,kind):addr for off,kind,addr in target.relocs}
    if len(raw)>len(at):return None
    syms=net.symbols(proj['id']).copy();bindings={}
    for i,r in enumerate(raw):
        if (r['off'],r['type']) not in at:return None
        sym=r['symtab'].get_symbol(r['symidx'])
        if sym['st_shndx']!='SHN_UNDEF':continue
        residual=section_target('mips',raw,i)
        if residual is None:return None
        address=(at[r['off'],r['type']]-residual)&0xFFFFFFFF
        if sym.name in bindings and bindings[sym.name]!=address:return None
        bindings[sym.name]=address
    for symbol,address in bindings.items():
        # This private map resolves SDK spelling, not a suggested global rename.
        syms.by_name[symbol]=Symbol(symbol,address)
    result=match.link(obj,name,target,syms,net.reader(proj,fn))
    return result if result.exact and not result.problems else None


def verify(net, proj, fn, hint, *, publish=True):
    if fn['status']!='asm' or len(hint.get('names',[]))!=1:return False
    if verified(proj,fn):return True
    candidates=list(objects(net,hint))
    if not candidates:return False
    expected=fingerprint(proj,fn)
    for obj,name,signature,record,corpus in candidates:
        if signature.size!=fn['size']:continue
        result=link(net,proj,fn,obj,name)
        if result is None:continue
        proof={'verified':True,'kind':'sdk','name':name,'corpus':corpus,'fingerprint':expected,
               'source_file':record['path'],'source_stamp':record['stamp'],'original_file_sha256':record['sha256'],
               'object_sha256':hashlib.sha256(obj).hexdigest(),
               'target_sha256':hashlib.sha256(bytes(fn['target'])).hexdigest(),
               'bytes':len(result.linked_code),'evidence':'Exact linked SDK code and referenced data; not matching C.', 'at':time.time()}
        if not publish:return proof
        return save(net,proj,fn,proof)
    return False


def save(net,proj,fn,proof):
    with net.project_store(proj['id']).tx() as db:
        fresh=db.execute('SELECT * FROM functions WHERE id=?',(fn['id'],)).fetchone()
        current=net.project_row(proj['slug'])
        if fresh['status']!='asm' or fingerprint(current,fresh)!=proof['fingerprint']:return False
        if db.execute('SELECT 1 FROM claims WHERE function_id=?',(fn['id'],)).fetchone():return False
        meta=json.loads(fresh['meta'] or '{}');meta['verified_assembly']=proof
        meta['sdk_display']={'name':proof['name'],'corpora':[proof['corpus']]}
        meta['asm_code']='Verified SDK assembly; excluded from C matching.'
        db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),fn['id']))
        net._event(db,proj['id'],None,'assembly-verified',fn['name']+': '+proof['name'])
    return True


def discover(net,proj,limit=32):
    """SDK objects can contain fixed MMIO immediates which a ROM annotates as relocations.

    Same-size indexed names are bounded leads only. The full linker must agree;
    ambiguous successful names and truncated candidate sets receive no identity.
    """
    if proj['arch']!='mips' or not net.is_rom(proj) or net.fork_slug(proj['slug']):return 0
    from .sdk_library import allowed
    count=0;started=time.monotonic();store=net.project_store(proj['id'])
    cursor_key='sdk-assembly-cursor:'+str(proj['id'])
    saved=store.one('SELECT value FROM settings WHERE key=?',cursor_key)
    cursor=int(saved['value']) if saved else 0
    rows=store.all("SELECT * FROM functions WHERE project_id=? AND status='asm' AND id>? ORDER BY id LIMIT ?",proj['id'],cursor,limit)
    if not rows:rows=store.all("SELECT * FROM functions WHERE project_id=? AND status='asm' ORDER BY id LIMIT ?",proj['id'],limit)
    examined=cursor
    selection=net.cfg(proj).get('sdk_libraries')
    for fn in rows:
        if time.monotonic()-started>3:break
        examined=fn['id']
        if verified(proj,fn):continue
        leads=net.store.all('SELECT DISTINCT corpus,name FROM sdk_signatures WHERE arch=? AND big_endian=? AND size=? '
                            'AND (? IS NULL OR corpus IN (SELECT value FROM json_each(?))) ORDER BY corpus,name LIMIT 33',
                            proj['arch'],int(net.cfg(proj).get('big_endian',True)),fn['size'],
                            None if selection is None else 1,json.dumps(selection or []))
        if len(leads)>32:continue
        proofs=[];complete=True
        for lead in leads:
            if time.monotonic()-started>3:
                complete=False;break
            from .maintenance import checkpoint
            checkpoint()
            if not allowed(net,proj,lead['corpus']):continue
            proof=verify(net,proj,fn,{'names':[lead['name']],'corpora':[lead['corpus']]},publish=False)
            if proof:proofs.append(proof)
        if complete and proofs and len({p['name'] for p in proofs})==1:
            count+=int(save(net,proj,fn,proofs[0]))
    with store.tx() as db:db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(cursor_key,str(examined)))
    return count
