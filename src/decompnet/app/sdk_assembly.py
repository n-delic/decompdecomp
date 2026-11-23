"""Verify locally indexed SDK objects; signatures are leads, the ROM linker is the judge."""
import hashlib
import json
import time
from io import BytesIO
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ..elfobj import _function_symbols, section_target
from ..network import IN_BUILD
from ..rom import match
from ..rom.symbols import Symbol
from .source_assembly import fingerprint, verified

PREFIX='sdk-object-reference:'
SIGNATURE_METHODS=('sdk-signature','user-confirmed')  # set aside on detection, no archive linked (signature_proof)


def index(db, path, corpus):
    """Store provenance only. Never copy SDK code into settings or fetch a library."""
    path=Path(path).resolve()
    db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
               (PREFIX+corpus,json.dumps({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})))


def entries(elf):
    """Include exported zero-size aliases only at an explicitly sized function entry."""
    found=_function_symbols(elf)
    symtab=elf.get_section_by_name('.symtab')
    if symtab is None:return found
    typed={(s['st_shndx'],s['st_value']):(s['st_shndx'],s['st_value'],s['st_size'])
           for s in symtab.iter_symbols() if s['st_info']['type']=='STT_FUNC' and s['st_size']>0}
    for s in symtab.iter_symbols():
        key=(s['st_shndx'],s['st_value'])
        if s.name and s['st_info']['type']=='STT_NOTYPE' and s['st_info']['bind'] in ('STB_GLOBAL','STB_WEAK') and s['st_size']==0 and key in typed:
            found[s.name]=typed[key]
    return found


def object_prints(data):
    from .twins import object_prints as prints
    out=prints(data);elf=ELFFile(BytesIO(data));symbols=entries(elf)
    for name,entry in symbols.items():
        if name in out:continue
        original=next((n for n in out if symbols.get(n)==entry),None)
        if original:out[name]=out[original]
    return out


def reference(net, corpus):
    """Where an indexed corpus's archive is: its own record, or the SDK folder inventory's entry for it. Folders
    indexed before the records existed were never indexed again (an unchanged file is skipped), so on a user's N64
    setup with every 2.0H archive indexed no SDK object could be found to link."""
    row=net.store.one('SELECT value FROM settings WHERE key=?',PREFIX+corpus)
    if row:return json.loads(row['value'])
    from .sdk_library import inventory
    return next(({'path':path,'sha256':record['sha256']} for path,record in sorted(inventory(net)['files'].items())
                 if record.get('corpus')==corpus and record.get('sha256')),None)


_PARSED={}  # (path, sha256, mtime, size) -> [(member, prints)]: a libultra archive parsed once, not per function


def _members(path, record):
    """An archive's objects and their fingerprints, or None when it's gone or changed. Parsing a whole libultra
    archive for every function made checking a racing game's 92 library functions take three minutes."""
    from .twins import _ar_members
    try:stat=path.stat()
    except OSError:return None
    if not path.is_file() or stat.st_size>100*1024*1024:return None
    key=(str(path),record['sha256'],stat.st_mtime_ns,stat.st_size)
    if key not in _PARSED:
        try:data=path.read_bytes()
        except OSError:return None
        if hashlib.sha256(data).hexdigest()!=record['sha256']:return None
        parsed=[]
        for member in (_ar_members(data) if path.suffix.lower()=='.a' else [data]):
            try:parsed.append((member,object_prints(member)))
            except (ELFError,ValueError,KeyError):continue
        if len(_PARSED)>=8:_PARSED.pop(next(iter(_PARSED)))
        _PARSED[key]=parsed
    return [stat.st_mtime_ns,stat.st_size],_PARSED[key]


def objects(net, hint):
    for corpus in hint.get('corpora',[])[:4]:
        record=reference(net,corpus)
        if not record:continue
        found=_members(Path(record['path']),record)
        if found is None:continue
        stamp,members=found;record={**record,'stamp':stamp}
        for member,prints in members:
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
    found=entries(elf).get(name)
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
    # The linker uses sized function entries. An untyped alias can otherwise be
    # truncated by an internal assembler label; judge the canonical sized entry.
    canonical=next((s.name for s in elf.get_section_by_name('.symtab').iter_symbols()
                    if s['st_info']['type']=='STT_FUNC' and (s['st_shndx'],s['st_value'],s['st_size'])==found),name)
    result=match.link(obj,canonical,target,syms,net.reader(proj,fn))
    return result if result.exact and not result.problems else None


def verify(net, proj, fn, hint, *, publish=True, from_c=False):
    """`from_c`: an open function (C library code the agents would otherwise match), set aside only by the project's
    choice (`SETTING`); without it only assembly is checked."""
    if fn['status']!=('open' if from_c else 'asm') or len(hint.get('names',[]))!=1:return False
    candidates=list(objects(net,hint))
    if not candidates:return False
    expected=fingerprint(proj,fn)
    for obj,name,signature,record,corpus in candidates:
        if signature.size!=fn['size']:continue
        result=link(net,proj,fn,obj,name)
        if result is None:continue
        symbols=entries(ELFFile(BytesIO(obj)))
        aliases=sorted(n for n in object_prints(obj) if symbols.get(n)==symbols[name])
        proof={'verified':True,'kind':'sdk','alias_schema':1,'names':aliases,'name':name,'corpus':corpus,'fingerprint':expected,
               'source_file':record['path'],'source_stamp':record['stamp'],'original_file_sha256':record['sha256'],
               'object_sha256':hashlib.sha256(obj).hexdigest(),
               'entry':list(entries(ELFFile(BytesIO(obj)))[name]),
               'target_sha256':hashlib.sha256(bytes(fn['target'])).hexdigest(),
               'bytes':len(result.linked_code),'evidence':'Exact linked SDK code and referenced data; not matching C.', 'at':time.time()}
        if from_c:
            proof.update(from_c=True,evidence='Exact linked SDK code and referenced data from your local SDK archive; '
                                              'set aside from C matching by this project\'s library setting, not matching C.')
        proof=combine([proof])
        if not publish:return proof
        return save(net,proj,fn,proof)
    return False


def combine(proofs):
    """Different spellings are aliases only with structural same-entry evidence.

    Two distinct functions can compile to identical bytes; that is not alias proof.
    Object hashes bind section indexes/offsets to the same symbol table.
    """
    if not proofs:return None
    names=sorted({name for p in proofs for name in p.get('names',[p['name']])})
    if len(names)>1:
        entries={(p['object_sha256'],tuple(p.get('entry',[]))) for p in proofs}
        if len(entries)!=1 or not proofs[0].get('entry'):return None
    return {**proofs[0],'name':' / '.join(names),'names':names,
            'alias_note':'Same SDK object entry; original symbol spelling is unknown.' if len(names)>1 else ''}


def save(net,proj,fn,proof):
    with net.project_store(proj['id']).tx() as db:
        fresh=db.execute('SELECT * FROM functions WHERE id=?',(fn['id'],)).fetchone()
        current=net.project_row(proj['slug'])
        statuses=('open','asm') if proof.get('verification_method')=='builtin-sdk-digest' else ('open',) if proof.get('from_c') else ('asm',)
        if fresh['status'] not in statuses or fingerprint(current,fresh)!=proof['fingerprint']:return False
        if proof.get('from_c') and not enabled(net,current):return False
        if json.loads(fresh['meta'] or '{}').get('reopened'):return False
        if db.execute('SELECT 1 FROM claims WHERE function_id=?',(fn['id'],)).fetchone():return False
        meta=json.loads(fresh['meta'] or '{}');meta['verified_assembly']=proof
        meta['sdk_display']={'name':proof['name'],'names':proof.get('names',[proof['name']]),'corpora':[proof['corpus']]}
        meta['asm_code']=(('Library code found by library detection' if proof.get('verification_method') in (*SIGNATURE_METHODS, 'builtin-sdk-digest')
                           else 'Library C your SDK archive reproduces byte for byte')+'; set aside from C matching by '
                          'this project\'s library setting.' if proof.get('from_c') else 'Verified SDK assembly; excluded from C matching.')
        db.execute("UPDATE functions SET status='asm',meta=? WHERE id=?",(json.dumps(meta),fn['id']))
        # SDK code is its own event, never "assembly-verified": that is the game's own hand-written assembly, still
        # the user's to look at, where this is library code a port replaces
        how=('found by library detection' if proof.get('verification_method') in (*SIGNATURE_METHODS,'builtin-sdk-digest')
             else 'linked against your SDK')
        net._event(db,proj['id'],None,'sdk-verified',f"{fn['name']}: {proof['name']} ({how})")
    return True


SETTING='sdk_set_aside_c'  # project config: library C that library detection finds is set aside, not matched (on unless turned off)


def enabled(net,proj):
    # On unless the project turned it off: agents spent hours on Controller Pak functions a port replaces and a
    # rebuild takes as assembly, while the game's own code waited.
    return bool(net.cfg(proj).get(SETTING, True))


def _lead(net,proj,fn,hint):
    """(name, corpora, the port's source or None) of an open function library detection found (`hint`: one name),
    or None. With a port kit only what it replaces or its glue supplies (`PortKit.sdk_group`), or what it compiles
    as the game's C ("native": the N64 port's libultra math and synthesizer) when the user's ultralib has that C for
    the build detection matched (port/ultralib.source): the port then compiles ultralib's C in its place. A name the
    kit doesn't know (a racing game's `_n_loadBuffer`, `read32`) is the game's own code to it and keeps its C work."""
    meta=json.loads(fn['meta'] or '{}')
    if meta.get('reopened'):return None
    names=hint.get('names') or []
    if len(names)!=1 or not names[0] or ' / ' in names[0]:return None
    kit=net.console(proj).port_kit
    group=kit.sdk_group(names[0]) if kit is not None else 'replaced'
    if group in ('replaced','glue'):return names[0],list(hint.get('corpora') or []),None
    if group!='native':return None
    from ..port import ultralib
    found=ultralib.source(names[0],hint,(meta.get('naming') or {}).get('fingerprint'))
    if found is None:return None
    path,build=found
    return names[0],list(hint.get('corpora') or []),{'ultralib':path.relative_to(ultralib.root()).as_posix(),'build':build}


def ultralib_needed(net,proj,limit=None):
    """The open functions library detection named that the N64 port builds as libultra's C (`PortKit.sdk_group`
    "native": its math and synthesizer), while the user's ultralib isn't installed: `_lead` sets those aside only
    from ultralib's own C, so without it they stay open, and agents and deep passes spend whole budgets on SDK code
    (an N64 game's guMtxIdent, guTranslateF and guPosition). The project then requires the `ultralib-source` pack like
    its compiler (the overview's `library_pack`, Engine.install_compilers). Read from the recorded identities only
    (`Network.sdk_functions`'s cached ones), so the overview can ask: no fingerprinting here. [] when none, the pack
    is installed, or the project's library setting is off."""
    # (the console first: one whose port compiles no libultra C never needs the pack, and asks nothing more)
    kit=net.console(proj).port_kit
    if kit is None or kit.sdk_group('guMtxIdent')!='native':return []
    if not enabled(net,proj) or proj['arch']!='mips' or not net.is_rom(proj) or net.fork_slug(proj['slug']):return []
    from ..port import ultralib
    if ultralib.root() is not None:return []
    hints=_recorded(net,proj)
    if not hints:return []
    names=[]
    for row in net.project_store(proj['id']).all("SELECT id,name,meta FROM functions WHERE project_id=? AND status='open' AND "
                                                  "id IN (SELECT value FROM json_each(?)) ORDER BY vram",proj['id'],json.dumps(sorted(hints))):
        if json.loads(row['meta'] or '{}').get('reopened'):continue
        found=hints[row['id']].get('names') or []
        if len(found)==1 and found[0] and kit.sdk_group(found[0])=='native':
            names.append(row['name'])
            if limit is not None and len(names)>=limit:break
    return names


def _recorded(net,proj):
    """The recorded SDK identities alone (Network.sdk_functions, cached), not the engine's live fingerprinting."""
    from ..network import Network
    return Network.sdk_functions(net,proj)


def ultralib_set_aside(net,proj):
    """{function: (ultralib's file, build)} of the functions set aside for the port to build from ultralib's C."""
    out={}
    for row in net.project_store(proj['id']).all("SELECT name,meta FROM functions WHERE project_id=? AND status='asm' AND "
                                                  "json_extract(meta,'$.verified_assembly.port_source.ultralib') IS NOT NULL",proj['id']):
        found=json.loads(row['meta'])['verified_assembly']['port_source']
        out[row['name']]=(found['ultralib'],found['build'])
    return out


def detected(net,proj):
    """{function id: hint} for the library code the project's detection finds, the same Play leaves out of a build
    (port/wasm/play.library): full-size signatures from the console's built-in table (the N64's ultralib rebuild) and
    the SDK folders the user indexed, and identities the user confirmed for the port."""
    from ..port.identities import confirmed
    return net.sdk_functions(proj)|confirmed(net,proj)


def signature_proof(proj,fn,name,hint):
    """Set aside on detection alone, when no local archive links the bytes: the target's full-size
    relocation-normalised fingerprint is one library function's, now or when it was named from one (a recorded
    identity, `Network.sdk_functions`), or the user confirmed it. Weaker than linking, and said so: it's never a
    match, and a changed target undoes it (`source_assembly.verified`)."""
    confirmed=hint.get('corpora')==['confirmed by the user']
    if not confirmed and not hint.get('fingerprint') and hint.get('evidence')!='recorded SDK signature':return None
    return {'verified':True,'kind':'sdk','from_c':True,'alias_schema':1,'name':name,'names':[name],
            'corpus':(hint.get('corpora') or ['library detection'])[0],'corpora':list(hint.get('corpora') or []),
            'verification_method':'user-confirmed' if confirmed else 'sdk-signature','signature':hint.get('fingerprint'),
            'fingerprint':fingerprint(proj,fn),'target_sha256':hashlib.sha256(bytes(fn['target'] or b'')).hexdigest(),
            'evidence':(hint.get('evidence') or 'confirmed by the user')+'; set aside from C matching by this '
                       'project\'s library setting. Bytes not linked against an SDK archive; not matching C.',
            'at':time.time()}


def discover_c(net,proj,stop=None,progress=None):
    """Set aside the open functions library detection finds (`detected`), when the project chose that (`SETTING`).
    Where the user's own SDK archive has the function, its object is linked at the function's address and every
    code and data byte compared, as for SDK assembly; otherwise the detection is the evidence (`signature_proof`).
    Background runs take 3 seconds a turn and resume; a user-started run (`stop`, an Event) does them all, reports
    `progress` and stops when asked."""
    if not enabled(net,proj) or proj['arch']!='mips' or not net.is_rom(proj) or net.fork_slug(proj['slug']):return 0
    store=net.project_store(proj['id']);cursor_key='sdk-c-cursor:'+str(proj['id'])
    saved=store.one('SELECT value FROM settings WHERE key=?',cursor_key)
    cursor=0 if stop is not None or not saved else int(saved['value'])
    hints=detected(net,proj)
    if not hints:return 0
    rows=store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND id>? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                   proj['id'],cursor,json.dumps(sorted(hints)))
    count=0;started=time.monotonic();examined=cursor;finished=True
    for done,fn in enumerate(rows):
        from .maintenance import checkpoint
        checkpoint()
        if stop is not None and stop.is_set() or stop is None and time.monotonic()-started>3:
            finished=False;break
        if progress:progress({'done':done,'total':len(rows),'set_aside':count,'function':fn['name']})
        examined=fn['id'];hint=hints[fn['id']]
        lead=_lead(net,proj,fn,hint)
        if not lead or store.one('SELECT 1 FROM claims WHERE function_id=?',fn['id']):continue
        proof=(verify(net,proj,fn,{'names':[lead[0]],'corpora':lead[1]},publish=False,from_c=True)
               or signature_proof(proj,fn,lead[0],hint))
        if proof and lead[2]:
            proof['port_source']=lead[2]  # the port builds ultralib's C for it (port/ultralib.units)
        if proof:count+=int(save(net,proj,fn,proof))
    if stop is None:
        with store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(cursor_key,str(0 if finished else examined)))
    return count


def choose_c(net,slug,on):
    """Save the project's choice. Turning it off gives every function it set aside back to C matching."""
    proj=net.project_row(slug);reopened=0
    with net.project_store(proj['id']).tx() as db:
        row=db.execute('SELECT id,config FROM projects WHERE slug=?',(slug,)).fetchone()
        cfg=json.loads(row['config']);cfg[SETTING]=bool(on)
        db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),row['id']))
        if not on:
            for fn in db.execute("SELECT id,name,meta FROM functions WHERE project_id=? AND status='asm' AND "
                                 "json_extract(meta,'$.verified_assembly.from_c')=1",(row['id'],)).fetchall():
                meta={k:v for k,v in json.loads(fn['meta']).items() if k not in ('verified_assembly','asm_code')}
                db.execute("UPDATE functions SET status='open',meta=? WHERE id=?",(json.dumps(meta),fn['id']))
                reopened+=1
            if reopened:
                net._event(db,row['id'],None,'reopen',f'{reopened} library functions open for C matching again (library setting turned off)')
    return reopened


def set_aside_c(net,proj):
    return net.project_store(proj['id']).one("SELECT COUNT(*) AS n FROM functions WHERE project_id=? AND status='asm' AND "
                                             "json_extract(meta,'$.verified_assembly.from_c')=1",proj['id'])['n']


def discover(net,proj,limit=32):
    """SDK objects can contain fixed MMIO immediates which a ROM annotates as relocations.

    Same-size indexed names are bounded leads only. The full linker must agree;
    Only aliases of the same object entry may share an identity. Distinct entries
    and truncated candidate sets receive no identity.
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
        if verified(proj,fn) and json.loads(fn['meta'] or '{}').get('verified_assembly',{}).get('alias_schema')==1:continue
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
        if complete and proofs:
            proof=combine(proofs)
            if proof:count+=int(save(net,proj,fn,proof))
    with store.tx() as db:db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(cursor_key,str(examined)))
    return count



def known_identity(console, cfg, code, *, has_relocs=False):
    """Identify reference-free SDK assembly or SDK code bound to its original image."""
    if cfg.get('kind') == 'fork' or not cfg.get('big_endian', True):
        return None
    if len(code) in console.sdk_linked_sizes:
        found = console.sdk_linked_identity(code, cfg.get('rom', {}).get('analysis_sha1', ''))
        if found and (not found.get('from_c') or cfg.get(SETTING, True)):
            return found
    if not has_relocs:
        found = console.sdk_assembly_identity(code)
        # SDK library C with no relocation (the console's exact-body table) follows the same project setting as the
        # linked image's C above; the entry stub and the handwritten assembly are not C and are always identified
        if found and found.get('from_c') and not cfg.get(SETTING, True):
            return None
        return found


def known_proof(console, proj, fn):
    """Bind a curated SDK identity to the current function and its analysis image."""
    cfg = json.loads(proj['config'])
    code = bytes(fn['target'] or b'')
    found = known_identity(console, cfg, code, has_relocs=bool(json.loads(fn['relocs'] or '[]')))
    if not found or fn['size'] != len(code):
        return None
    if found.get('evidence'):
        evidence = found['evidence']
    elif found.get('from_c') and not found.get('image_sha1'):
        evidence = ('Exact complete bytes of SDK library C code with no relocations, from the SDK\'s own libultra builds; '
                    'excluded from C matching by the library setting.')
    elif found.get('from_c'):
        evidence = 'Exact SDK library C bytes in a known linked image; excluded from C matching by the library setting.'
    elif found.get('image_sha1'):
        evidence = 'Exact SDK assembly bytes in a known linked image; no C coverage.'
    else:
        evidence = 'Exact complete bytes of a verified self-contained handwritten SDK routine; no C coverage.'
    return {**found, 'verified': True, 'kind': 'sdk', 'verification_method': 'builtin-sdk-digest',
            'alias_schema': 1, 'fingerprint': fingerprint(proj, fn), 'evidence': evidence, 'at': time.time()}


def discover_known(net, proj, *, function_id=None, check_cancel=None):
    """Backfill local ROM targets without SDK files, compiling code or starting agents."""
    if proj['slug'] in net.links or not net.is_rom(proj) or net.fork_slug(proj['slug']):
        return 0
    console = net.console(proj)
    sizes = sorted(set(console.sdk_assembly_sizes) | set(console.sdk_linked_sizes))
    # the cartridge's entry stub is told by its place, not its size: its length varies with the game's padding
    entry = net.cfg(proj).get('rom', {}).get('entrypoint') if console.sdk_entry_stub else None
    if not sizes and entry is None:
        return 0
    store = net.project_store(proj['id'])
    placeholders = ','.join('?' for _ in sizes)
    target_filter = ' AND id=?' if function_id is not None else ''
    args = (proj['id'], *sizes, *([entry] if entry is not None else []), *([function_id] if function_id is not None else []))
    where = f"(size IN ({placeholders})" + (" OR vram=?" if entry is not None else "") + ")"
    rows = store.all(f"""SELECT id,name,project_id,vram,rom_offset,size,target,relocs,status,meta
                         FROM functions WHERE project_id=? AND status IN ('open','asm') AND {IN_BUILD}
                         AND {where}{target_filter}""", *args)
    from .maintenance import checkpoint

    count = 0
    for fn in rows:
        checkpoint()
        if check_cancel is not None:
            check_cancel()
        if json.loads(fn['meta'] or '{}').get('reopened') or verified(proj, fn):
            continue
        proof = known_proof(console, proj, fn)
        if proof:
            count += int(save(net, proj, fn, proof))
    return count
