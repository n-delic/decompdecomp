"""Operator-reviewed MIPS extents: immutable previews, retained child records and guarded undo."""
import base64
import hashlib
import json
import re
import struct
import time
from contextlib import nullcontext
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
    if meta.get('boundary_override_source') == 'shared_entry' and meta.get('shared_entry'):
        return ('Independently called adjacent entries share this tail. The short prefix is intentional; '
                'its original bytes and exact-match requirement are unchanged.')
    proof = meta.get('verified_assembly') or {}
    if (row['status'] == 'asm' and proof.get('verified') is True and proof.get('kind') == 'sdk'
            and proof.get('verification_method') == 'builtin-sdk-digest'
            and proof.get('bytes') == len(raw) and proof.get('target_sha256') == hashlib.sha256(raw).hexdigest()):
        # A known assembly entry can end with a call to a non-returning SDK routine.
        return meta.get('boundary_tail_review')
    return console.boundary_reason(raw) or meta.get('boundary_tail_review')


def _invalidate_shared_entries(db, project_id):
    """Boundary edits can change either entry or its callers; do not retain inferred reviews."""
    db.execute("""UPDATE functions SET meta=json_remove(meta, '$.shared_entry', '$.boundary_override',
               '$.boundary_override_source') WHERE project_id=? AND
               json_extract(meta, '$.boundary_override_source')='shared_entry'""", (project_id,))


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


# Project settings that record work rather than layout or compiler inputs. Saved progress bumps progress_revision about
# every 45 s while agents work, so hashing it made a 150 s match check almost never commit on an N64 game with 2,000
# matches ("Project inputs changed while checking matches", six times in a row). candidate_recovery, partial_reuse
# and exact_match_inputs ignore the same counters.
VOLATILE = ('claim_order', 'repository_sync_folder')


def _config_inputs(config):
    """A project's config without the settings that only record work, spending or where things are saved."""
    from .exact_match_inputs import WORKFLOW_SETTINGS
    cfg=json.loads(config) if isinstance(config,str) else dict(config)
    for key in WORKFLOW_SETTINGS+VOLATILE:cfg.pop(key,None)
    return cfg


def _revision(net, proj, affected=()):
    # Unrelated workers may keep submitting candidates. Guard layout, matched C,
    # shared inputs and the complete edited rows, not incidental counters elsewhere.
    # Selecting * first inflated every draft, seed and assembly only to discard it.
    from .metadata_rows import rows as metadata
    store=net.project_store(proj['id']);ids=set(affected)
    fields=('id','name','vram','rom_offset','size','target','relocs','unit','status','matched_source','meta')
    # hex(NULL) is empty text: preserve NULL separately from an empty target.
    query='SELECT '+','.join('CASE WHEN target IS NULL THEN NULL ELSE hex(target) END AS target' if k=='target' else k for k in fields)
    rows=metadata(store,fields,query+' FROM functions WHERE project_id=? ORDER BY id',proj['id'])
    edited={r['id']:dict(r) for r in store.all(
        'SELECT * FROM functions WHERE project_id=? AND id IN (SELECT value FROM json_each(?))',proj['id'],json.dumps(sorted(ids)))} if ids else {}
    for index,row in enumerate(rows):
        row['target']=bytes.fromhex(row['target']) if row['target'] is not None else None
        if row['id'] in edited:rows[index]=edited[row['id']]
    project=dict(net.project_row(proj['slug']));project['config']=_config_inputs(project['config'])
    return _digest([project,rows,
                    [list(r) for r in store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',proj['id'])],
                    [list(r) for r in store.all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]])


def _layout_rows(net, proj):
    """Discovery needs extents and original bytes, never every function's saved C or assembly."""
    from .metadata_rows import rows
    fields=('id','name','vram','rom_offset','unit','size','target','status','meta','gone_mark')
    query='SELECT '+','.join('CASE WHEN target IS NULL THEN NULL ELSE hex(target) END AS target' if k=='target' else k for k in fields)
    result=rows(net.project_store(proj['id']),fields,query+' FROM functions WHERE project_id=? ORDER BY id',proj['id'])
    for row in result:row['target']=bytes.fromhex(row['target']) if row['target'] is not None else None
    return result


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
    rows=_layout_rows(net,proj);by_name={r['name']:r for r in rows}
    for finding in boundaries(net,proj):
        fn=by_name[finding['function']];target=fn['target'] or b''
        last=int.from_bytes(target[-4:],'big' if proj['big_endian'] else 'little') if len(target)>=4 else 0
        # These are possible repairs, not evidence that adjacent entries belong together.
        suggested='delay_slot' if target and rabbitizer.Instruction(last).hasDelaySlot() else ''
        findings.append({**finding,'suggested_action':suggested})
    # Possible leading padding is a suggestion to inspect, not a block: agents may still claim these.
    from .start_boundaries import audit_rows
    # Entry fixes the importer left alone (set aside, hand-written-looking, turned off): suggestions to inspect,
    # before start_boundaries' vaguer "the first word may be padding" for the same function.
    seen={f['function'] for f in findings}
    try:entries=entry_findings(net,proj,layout=rows)
    except NetworkError:entries={}
    findings+=[{'function':f.name,'suggested_action':f.kind,'start':f.vram,'entry':f.at,'confidence':f.confidence,
                'note':f.summary()+('. '+f.warning if f.warning else ''),'evidence':f.evidence,'override':True}
               for f in entries.values() if f.name not in seen and (f.confidence!='auto' or not entry_fixes_enabled(net,proj))]
    seen={f['function'] for f in findings}
    findings+=[row for row in audit_rows(net,proj) if row['function'] not in seen]
    history=[]
    for r in net.project_store(proj['id']).all('SELECT key,value FROM settings WHERE key LIKE ?',PREFIX+str(proj['id'])+':%'):
        item=json.loads(r['value'])
        if not item.get('undone'):history.append({'key':r['key'],'function':item['function'],'action':item['action'],'time':item['time'],'evidence':item.get('evidence',''),'entries':[int(fid) for fid in item['before']],
                                                  'automatic':bool(item.get('automatic')),'entry':item.get('entry')})
    return {'findings':findings,'history':sorted(history,key=lambda r:r['time'],reverse=True)[:30],
            'automatic':entry_fixes_enabled(net,proj)}


def preview(net, slug, name, action, end=None, *, entry=None, investigation=False):
    """Build a fresh guarded preview; automatic discovery uses only the shared extent checks below. `entry`: for
    split_gap, where the next function starts (`end` is where this one ends; the words between are kept out of any
    function)."""
    proj=_supported(net,slug)
    mapped=_image(net,proj);image,segments=mapped
    plan=_preview(net,proj,dict(net._function(name,slug)),action,end,entry=entry,investigation=investigation,mapped=mapped,retain=True)
    plan['function']=name
    owners=[s for s in segments if s.rom_start<=plan['rom']<s.rom_end and s.vram+plan['rom']-s.rom_start==plan['start']]
    if len(owners)!=1:raise NetworkError('This entry needs one unambiguous original image mapping.')
    from .. import arch
    from .boundary_evidence import assembly
    plan['assembly']=assembly(arch.get(proj['arch']).boundary_instruction,image,owners[0],plan['start'],plan['end'],
                              [plan['start'],plan['old_end'],plan['end'],*([plan['entry']] if plan.get('entry') else []),*[p['vram'] for p in plan['pieces']]],proj['big_endian'])
    plan['revision']=_revision(net,proj,[p['id'] for p in plan['pieces']])
    plan['image']=_digest(image)
    return plan


def _preview(net, proj, fn, action, end=None, *, entry=None, investigation=False, rows=None, mapped=None, retain=False):
    # Discovery shares immutable inputs within one pass. Every selected decision
    # still goes through a fresh public preview and the transaction-time guards.
    name=fn['name']
    if rows is None:rows=_layout_rows(net,proj)
    if fn['status']=='matched':raise NetworkError('A matched function cannot be reclassified or resized.')
    meta=json.loads(fn['meta'] or '{}')
    if meta.get('gone'):raise NetworkError('This entry is archived. Undo its repair before editing it.')
    image,segments=mapped if mapped is not None else _image(net,proj)
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
    elif action in ('adjust','trim_start','split_end','split_gap'):
        # trim_start/split_end: cut at `end`, the real entry, which becomes its own function (entry_fixes). For
        # trim_start what is left before it is archived as padding; for split_end it stays this function.
        # split_gap: this function ends at `end`, the words up to `entry` stay outside any function (archived as
        # padding, original bytes kept) and `entry` starts a function of its own: an entry that swallowed a return,
        # a couple of stray words that are no instruction, then the next function's prologue. It took a split_end
        # and then a trim_start on its tail, and split_function refused the second cut (the stray words "run on").
        if type(end) is not int:raise NetworkError('Enter an aligned end address.')
        newend=end
        if action!='adjust' and not address<newend<address+fn['size']:raise NetworkError('The entry must lie inside the function.')
        if action=='split_gap' and (type(entry) is not int or entry%4 or not newend<entry<address+fn['size']):
            raise NetworkError('Enter where the next function starts: an aligned address after the new end and inside the function.')
    elif action not in ('padding','data','keep'):
        raise NetworkError('Choose a boundary repair action.')
    if newend%4 or not address<newend<=address+65536:raise NetworkError('Choose an aligned extent of at most 64 KiB.')
    finish=start+newend-address
    if start<owner.rom_start or finish>owner.rom_end or finish>len(image):raise NetworkError('The proposed extent crosses the original segment mapping.')
    resizing=action in ('join_previous','delay_slot','adjust','trim_start','split_end','split_gap')
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
            if any(net.project_store(proj['id']).one('SELECT 1 FROM functions WHERE project_id=? AND vram=? AND gone_mark IS NULL',proj['id'],at)
                   for at in (newend,*([entry] if action=='split_gap' else []))):
                raise NetworkError('A function already starts at the proposed tail. Review a join instead.')
    if any(p['status']=='matched' for p in pieces):raise NetworkError('The repair would change an existing matched function.')
    if any(image[p['rom_offset']:p['rom_offset']+p['size']]!=p['target'] for p in pieces):
        raise NetworkError('A proposed piece differs from the original image. Review/reimport before joining.')
    ids=[p['id'] for p in pieces]
    if not investigation and any(net.project_store(proj['id']).one('SELECT 1 FROM claims WHERE function_id=?',fid) for fid in ids):
        raise NetworkError('Release the affected claims before changing boundaries.')
    if retain:
        retained=[]
        for piece in pieces:
            current=net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',piece['id'])
            if current is None or any(current[k]!=v for k,v in piece.items()):
                raise NetworkError('Project inputs changed while previewing the boundary. Preview again.')
            retained.append(dict(current))
        pieces=retained
    raw=image[start:finish]
    return {'function':name,'owner':fn['name'],'action':action,'end':newend,'start':address,'entry':entry if action=='split_gap' else None,
            'old_size':fn['size'],'old_end':fn['vram']+fn['size'],'new_size':len(raw),'affected':[{'id':p['id'],'name':p['name'],'size':p['size'],'start':p['vram'],'end':p['vram']+p['size']} for p in pieces],
            'warning':'Safety checks reverify existing matches and guard mappings, overlaps and changed inputs. They do not prove the proposed boundary. Sources, attempts and notes are retained. Resized functions need a fresh match.',
            'pieces':pieces,'raw':raw,'unit':fn['unit'],'rom':start}


STRAY_WORDS = 8  # at most this many words between a return and the next entry count as stray


def stray_cut(proj, fn, addr):
    """What a cut at `addr` should be instead when the words just before it are stray: ('split_gap', end) when `fn`
    returns first (the jump and its delay slot end at `end`) and at most STRAY_WORDS words, not all nops, follow
    before `addr`; ('trim_start', None) when every word of `fn` before `addr` is such a word and one is no
    instruction at all (the tail an earlier cut at the return left, starting on the stray words). None otherwise.
    A cut cannot say either: the words would stay in one of the two functions and "run on" into the next. Mapped
    MIPS only, as every boundary repair here."""
    if proj['arch'] != 'mips':
        return None
    from .. import arch
    facts = arch.get(proj['arch']).boundary_instruction
    order = 'big' if proj['big_endian'] else 'little'
    code = (fn['target'] or b'')[:addr - fn['vram']]
    words = [int.from_bytes(code[i:i + 4], order) for i in range(0, len(code) - 3, 4)]
    for i in range(len(words) - 2, -1, -1):  # (a jump whose delay slot is inside the first part)
        if facts(words[i], fn['vram'] + 4 * i)['terminal']:
            end = fn['vram'] + 4 * (i + 2)
            # (nops after a return are alignment the split trims off the first part already)
            stray = 0 < (addr - end) // 4 <= STRAY_WORDS and any(code[end - fn['vram']:])
            return ('split_gap', end) if stray else None
    if words and len(words) <= STRAY_WORDS and not all(rabbitizer.Instruction(w).isValid() for w in words):
        return ('trim_start', None)
    return None


def public(plan):
    return {k:v for k,v in plan.items() if k not in ('pieces','raw')}


R_MIPS_26 = 4  # a jump: the raw rebuild reads these from the words again


def _data_relocs(pieces):
    """{absolute instruction address: (type, address, word)} of every relocation but a jump that the pieces' own
    targets carry (the split's `%hi(D_80008260)`/`%lo(...)` annotations)."""
    found = {}
    for p in pieces:
        code = p['target'] or b''
        for off, rtype, addr in json.loads(p['relocs'] or '[]'):
            if rtype != R_MIPS_26 and 0 <= off <= len(code) - 4:
                found.setdefault(p['vram'] + off, (rtype, addr, bytes(code[off:off + 4])))
    return found


_ANNOTATION = {5: 'hi', 6: 'lo', 7: 'gp_rel'}  # R_MIPS_HI16, LO16, GPREL16: written into the line as the split wrote them
_IMMEDIATE = re.compile(r'-?(?:0x[0-9A-Fa-f]+|\d+)(?=\s*(?:\(\$\w+\))?\s*$)')


def _lines(raw, vram, rom, big_endian, found, symbols):
    """The words of an extent as assembly lines, each data reference `found` recorded on the same word written back as
    the split's annotation (`%hi(D_80008274)`, `%lo(...)`), so the target, its relocations and the stored assembly
    say what the import said.

    A decision rebuilds the changed extent from the raw words, and rabbitizer's lines carry only jumps: every data
    reference of the split's annotated assembly was lost with it (23 of 35 boundary-adjusted functions of an N64 game,
    the ones still open there among them). The linker places a candidate's own data (string and float literals in its
    `.rdata`) only where the target refers to it, so a float literal the target loads with `lui $at; lwc1` could never
    link; every `lui`/`addiu` of a named string was a differing row though its bytes matched; and the float-literal
    families never fired, since they read the constants from the assembly's `%lo(...)($at)` loads."""
    order = 'big' if big_endian else 'little'
    lines = []
    for i in range(0, len(raw) - 3, 4):
        word = int.from_bytes(raw[i:i + 4], order)
        ins = rabbitizer.Instruction(word, vram + i)
        operands = ins.disassemble().split(None, 1)[-1]
        hit = found.get(vram + i)
        if hit and hit[0] in _ANNOTATION and hit[2] == raw[i:i + 4]:
            name, off = symbols.name_at(hit[1])
            text = f"%{_ANNOTATION[hit[0]]}({name}{f' + 0x{off:X}' if off else ''})"
            annotated, n = _IMMEDIATE.subn(text, operands, count=1)
            operands = annotated if n else operands
        lines.append(split.AsmLine(rom + i, vram + i, word, ins.getOpcodeName(), operands))
    return lines


def _carried(found, vram, code, relocs):
    """`relocs` (offset, type, address) with what `_lines` could not write into a line (another relocation type) added
    from `found`, where the word there is still the one it was recorded on."""
    have = {off for off, _, _ in relocs}
    out = list(relocs)
    for at, (rtype, addr, word) in found.items():
        off = at - vram
        if 0 <= off <= len(code) - 4 and off not in have and bytes(code[off:off + 4]) == word:
            out.append((off, rtype, addr))
            have.add(off)
    return sorted(out)


def _rebuilt(net, proj, name, vram, rom, unit, raw, found):
    """(target, assembly text, line count) of an extent rebuilt from its words with the data references `found`."""
    symbols = net.symbols(proj['id'])
    lines = _lines(raw, vram, rom, proj['big_endian'], found, symbols)
    rf = split.RomFunction(name, vram, rom, unit, lines, Path(), set())
    target = net._make_target(proj, rf, symbols)
    target.relocs = _carried(found, vram, target.code, target.relocs)
    return target, net.console(proj).function_asm(rf), len(lines)


def restore_relocs(net, proj):
    """Give back the data references that decisions saved before `_lines` existed dropped (the targets they rebuilt
    and the tails they created: relocations and stored assembly), from the original rows each decision keeps for undo.
    Idempotent; the decision's `after` record is updated with the row so undo still recognises it. Returns how many
    functions changed."""
    store = net.project_store(proj['id'])
    histories = []
    for row in store.all('SELECT key,value FROM settings WHERE key LIKE ?', PREFIX + str(proj['id']) + ':%'):
        history = json.loads(row['value'])
        if not history.get('undone'):
            histories.append((row['key'], history))
    found = {}
    for _, history in histories:
        for saved in history['before'].values():
            if saved.get('vram') is None or not isinstance(saved.get('target'), dict):
                continue
            found.update({k: v for k, v in _data_relocs([{**saved, 'target': base64.b64decode(saved['target']['bytes'])}]).items()
                          if k not in found})
    if not found:
        return 0
    changed = set()
    with store.tx() as db:
        for key, history in histories:
            dirty = False
            for fid in {*history['after'], *map(str, history.get('created', ()))}:
                row = db.execute('SELECT * FROM functions WHERE id=? AND project_id=?', (int(fid), proj['id'])).fetchone()
                if (row is None or row['vram'] is None or not row['target'] or row['id'] in changed
                        or row['gone_mark'] is not None or json.loads(row['meta'] or '{}').get('gone')):
                    continue
                relocs = [tuple(r) for r in json.loads(row['relocs'] or '[]')]
                if not any(row['vram'] <= at < row['vram'] + len(row['target']) for at in found):
                    continue
                target, asm, _ = _rebuilt(net, proj, row['name'], row['vram'], row['rom_offset'], row['unit'], row['target'], found)
                if target.code != bytes(row['target']):
                    continue
                restored = sorted({*relocs, *map(tuple, target.relocs)})
                # the assembly only where a rebuild wrote it without annotations (a split's own text has its labels)
                bare = not re.search(r'%(?:hi|lo|gp_rel)\(', row['asm'] or '')
                if restored == sorted(relocs) and (asm == row['asm'] or not bare):
                    continue
                asm = asm if bare else row['asm']
                packed = _pack(row)
                db.execute('UPDATE functions SET relocs=?,asm=? WHERE id=?', (json.dumps([list(r) for r in restored]), asm, row['id']))
                changed.add(row['id'])
                if history['after'].get(fid) == packed:
                    history['after'][fid] = _pack(db.execute('SELECT * FROM functions WHERE id=?', (row['id'],)).fetchone())
                    dirty = True
            if dirty:
                db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(history), key))
    if changed:
        _invalidate(net, proj)
    return len(changed)


def _pack(row):
    return {k:({'bytes':base64.b64encode(row[k]).decode()} if isinstance(row[k],bytes) else row[k]) for k in FIELDS}


def _restore(db, fid, saved):
    values=[base64.b64decode(saved[k]['bytes']) if isinstance(saved[k],dict) and 'bytes' in saved[k] else saved[k] for k in FIELDS]
    db.execute('UPDATE functions SET '+','.join(k+'=?' for k in FIELDS)+' WHERE id=?',(*values,fid))


def _verify_matches(net, proj, log, only=None):
    def progress(value):
        getattr(log,'check_cancel',lambda:None)()
        getattr(log,'progress',lambda p:None)({**value,'phase':'Reverifying existing matches'})
        if value['done']%25==0 or value['done']==value['total']:
            log(f"{value['done']}/{value['total']} existing matches checked.")
    return net.regression_test(proj,None,only=only,header_for=lambda f:net._context(proj['id'],f['unit'])['header'],progress=progress)


def _referring(net, proj, spans):
    """The matched functions whose relocations point into any of `spans` ([lo, hi) addresses): the only matched code
    a boundary change can relink (a resized or new symbol, an archived piece). Everything else links as before, so a
    2,000-match game checks a few dozen instead of all of them (140-210 s, which saved progress outran)."""
    return [r for r in net.project_store(proj['id']).all("SELECT * FROM functions WHERE project_id=? AND status='matched' AND gone_mark IS NULL",proj['id'])
            if any(lo<=address<hi for _,_,address in json.loads(r['relocs'] or '[]') for lo,hi in spans)]


def _spans(plan):
    return [(min(plan['start'],*(p['start'] for p in plan['affected'])),max(plan['old_end'],plan['end'],*(p['end'] for p in plan['affected'])))]


COMMIT_TRIES = 3


def apply(net, slug, name, action, revision, image_hash, end=None, note='', log=print, entry=None):
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before editing shared function boundaries.')
    if not isinstance(note,str) or not note.strip():raise NetworkError('Record the evidence for this boundary decision.')
    plan=preview(net,slug,name,action,end,entry=entry)
    if plan['revision']!=revision or plan['image']!=image_hash:raise NetworkError('The project or original image changed. Preview again.')
    first=public(plan)
    for attempt in range(COMMIT_TRIES):
        if attempt:
            # A worker matched or changed something while the check ran: the same decision, checked again on what
            # is there now. A different preview (the layout itself moved) is the person's to review.
            plan=preview(net,slug,name,action,end,entry=entry)
            if {k:v for k,v in public(plan).items() if k!='revision'}!={k:v for k,v in first.items() if k!='revision'} or plan['image']!=image_hash:
                raise NetworkError('The project or original image changed. Preview again.')
        matched=_referring(net,proj,_spans(plan))
        log(f'Reverifying {len(matched)} existing match{"" if len(matched)==1 else "es"} that refer into the changed extent…')
        broken=_verify_matches(net,proj,log,only=matched)
        if broken:raise NetworkError('Existing matches could not be reverified: '+', '.join(n for n,_ in broken[:5]))
        getattr(log,'check_cancel',lambda:None)()
        try:result=_commit(net,proj,plan,name,action,plan['revision'],image_hash,note)
        except NetworkError as error:
            if 'while checking matches' not in str(error) or attempt==COMMIT_TRIES-1:raise
            log('Project inputs changed while checking matches; checking again…');continue
        log('Boundary decision saved. Original records and attempt history retained.')
        return result


def _commit(net, proj, plan, name, action, revision, image_hash, note, automatic=False, preferred_entries=None):
    """Save one previewed decision with its undo record; matches were reverified by the caller. `automatic`: an
    entry fix the importer applied (entry_fixes), labelled so in Repair history and never applied again once undone."""
    rows=plan['pieces'];head=rows[0];old={str(p['id']):_pack(p) for p in rows}
    symbols=[dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])]
    key=PREFIX+str(proj['id'])+':'+str(time.time_ns());created=[];created_symbols=[]
    with net.project_store(proj['id']).tx() as db:
        if _revision(net,proj,[p['id'] for p in rows])!=revision:raise NetworkError('Project inputs changed while checking matches. Preview again.')
        if _digest(_image(net,proj)[0])!=image_hash:raise NetworkError('The original image changed. Preview again.')
        if any(db.execute('SELECT 1 FROM claims WHERE function_id=?',(p['id'],)).fetchone() for p in rows):raise NetworkError('An affected function was claimed. Release it and preview again.')
        meta=json.loads(head['meta'] or '{}');meta.pop('shared_entry', None);meta.pop('boundary_override_source', None)
        meta['boundary_decision']={'action':action,'evidence':note[:4000],'time':time.time()}
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
            # Rebuild relocation/assembly targets through the same console adapter as import, keeping the split's
            # data references (_lines).
            carried=_data_relocs(rows)
            target,asm,count=_rebuilt(net,proj,head['name'],head['vram'],head['rom_offset'],head['unit'],plan['raw'],carried)
            db.execute("UPDATE functions SET size=?,n_insns=?,target=?,relocs=?,asm=?,status='open',meta=?,best_score=NULL,seed=NULL,seed_key=NULL WHERE id=?",
                       (len(plan['raw']),count,target.code,json.dumps(target.to_json()),asm,json.dumps(meta),head['id']))
            db.execute('UPDATE symbols SET size=? WHERE project_id=? AND name=?',(len(plan['raw']),proj['id'],head['name']))
            if action=='trim_start':
                # The words before the real entry stay in the image, outside any function, under this row with
                # its attempts and notes (undo brings it back as it was).
                meta['gone']=True;meta['boundary_classification']='padding'
                db.execute('UPDATE functions SET meta=? WHERE id=?',(json.dumps(meta),head['id']))
            if len(plan['raw'])<head['size']:
                entry=action in ('trim_start','split_end','split_gap')
                offset=len(plan['raw'])
                # (kind, address, offset into the old extent, size): the retained tail, or for split_gap the stray
                # words, archived as padding outside any function, and then the next function's entry.
                if action=='split_gap':
                    gap=plan['entry']-plan['end']
                    cuts=[('padding',plan['end'],offset,gap),('entry',plan['entry'],offset+gap,head['size']-offset-gap)]
                else:
                    cuts=[('entry' if entry else 'tail',plan['end'],offset,head['size']-offset)]
                for kind,at,piece_offset,piece_size in cuts:
                    tailname={'entry':'func_','padding':'pad_','tail':'boundary_tail_'}[kind]+format(at,'08X')
                    tailraw=head['target'][piece_offset:piece_offset+piece_size]
                    preferred=(preferred_entries or {}).get((head['rom_offset']+piece_offset,at,piece_size,tailraw)) if kind=='entry' else None
                    existing_symbol=None;use_preferred=False
                    if preferred:
                        existing_symbol=db.execute('SELECT address,size,kind FROM symbols WHERE project_id=? AND name=?',(proj['id'],preferred)).fetchone()
                        occupied=db.execute('SELECT 1 FROM functions WHERE project_id=? AND name=?',(proj['id'],preferred)).fetchone()
                        if not occupied and (not existing_symbol or (existing_symbol['address'],existing_symbol['size'],existing_symbol['kind'])==(at,piece_size,'func')):
                            tailname=preferred;use_preferred=True
                        else:existing_symbol=None
                    taken=lambda candidate:(db.execute('SELECT 1 FROM functions WHERE project_id=? AND name=?',(proj['id'],candidate)).fetchone()
                                            or db.execute('SELECT 1 FROM symbols WHERE project_id=? AND name=?',(proj['id'],candidate)).fetchone())
                    if not use_preferred and taken(tailname):tailname+='_'+format(time.time_ns(),'x')
                    tailmeta=json.dumps({'boundary_split_from':head['name'],'boundary_decision':meta['boundary_decision']} if kind=='entry' else
                                        {'boundary_split_from':head['name'],'boundary_decision':meta['boundary_decision'],'gone':True,
                                         'boundary_classification':'padding'} if kind=='padding' else
                                        {'boundary_tail_review':'Retained tail from a reviewed boundary adjustment; confirm its entry point before agent work.'})
                    tailtarget,tailasm,tailcount=_rebuilt(net,proj,tailname,at,head['rom_offset']+piece_offset,head['unit'],tailraw,carried)
                    fid=db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,asm,unit,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (proj['id'],tailname,at,head['rom_offset']+piece_offset,len(tailraw),tailcount,tailtarget.code,json.dumps(tailtarget.to_json()),tailasm,head['unit'],tailmeta)).lastrowid
                    created.append(fid)
                    if kind=='entry':
                        # A real entry is a function symbol, so references to it read by name like any other.
                        if not existing_symbol:
                            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',(proj['id'],tailname,at,len(tailraw),'func'))
                            created_symbols.append(tailname)
        after={str(p['id']):_pack(dict(db.execute('SELECT * FROM functions WHERE id=?',(p['id'],)).fetchone())) for p in rows}
        for fid in created:after[str(fid)]=_pack(dict(db.execute('SELECT * FROM functions WHERE id=?',(fid,)).fetchone()))
        history={'evidence':note[:4000],'function':name,'action':action,'time':time.time(),'before':old,'after':after,'created':created,'symbols':symbols,
                 'created_symbols':created_symbols,'automatic':bool(automatic),'start':head['vram'],'entry':plan['entry'] if action=='split_gap' else plan['end'] if action in ('trim_start','split_end') else None,
                 'image':image_hash,'symbol_after':[dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],head['name'])],
                 'config':json.dumps(_config_inputs(proj['config']),sort_keys=True),'contexts':_digest([list(r) for r in net.project_store(proj['id']).all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])]),
                 'attempt_id':db.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]}
        db.execute('INSERT INTO settings VALUES(?,?)',(key,json.dumps(history)))
        _invalidate_shared_entries(db, proj['id'])
        net._event(db,proj['id'],None,'boundary',f"{action}: {name}. {note[:600]}")
    _invalidate(net,proj)
    return {'key':key,'function':plan['owner'],'action':action}


# Repairs the bytes alone decide. A missing delay slot is required by the instruction set; a piece whose code runs
# on into the next one is the same flow. Each is applied only when the repaired extent ends on a complete path, and
# nothing absorbed is a direct call target or makes its own stack frame (a separate function after a call that
# never returns). Everything else stays for review. An N64 game had 90 flagged entries, 56 of these shapes, which
# took a preview and a full match re-verification each by hand. A single word of alignment filler between a return
# and the next entry is archived as padding (automatic_boundaries.alignment_fill, which new imports apply), and so is
# an entry that only repeats part of another, complete one.
SAFE_ROUNDS = 8
SAFE_NOTE = {'delay_slot':'Automatic safe fix: the final instruction required its delay slot; the extended extent ends on a complete path.',
             'join_previous':'Automatic safe fix: the preceding code runs on into this piece without a return; the joined extent ends on a complete path.',
             'epilogue':'Automatic safe fix: after a call, the next piece restores the return address from this function\'s stack frame, pops exactly that frame and returns; it is this function\'s ending.',
             'padding':'Automatic safe fix: one word of 8-byte alignment filler between a function that returned and the next entry; nothing references it. It stays in the image, outside any function.',
             'duplicate':'Automatic safe fix: this entry only repeats instructions of another, complete entry with the same mapping; those bytes stay in that function.'}
LW_RA, POP = 0x8FBF0000, 0x27BD0000  # lw $ra,N($sp); addiu $sp,$sp,N


def safe_job_kind(slug):
    return 'Apply safe boundary fixes: '+slug


def _call_targets(image, segments, big_endian, skip=frozenset()):
    """Direct `jal` destinations anywhere in the mapped image: an entry something calls is never absorbed. `skip`:
    image offsets of words a person or a proven fix archived as padding or data, which never run (an N64 game's
    three filler words held a `jal` into the middle of a function and kept its pieces from being joined)."""
    out=set();fmt='>I' if big_endian else '<I'
    for s in segments:
        end=min(s.rom_end,len(image));start=s.rom_start+(-s.rom_start%4)
        for (word,),at in zip(struct.iter_unpack(fmt,image[start:end-(end-start)%4]),range(start,end,4)):
            if word>>26==3 and at not in skip:out.add(((s.vram+at-s.rom_start+4)&0xF0000000)|((word&0x3FFFFFF)<<2))
    return out


def _archived_words(net, proj):
    """Image offsets of the words archived as padding or data, unless a live function still covers them."""
    rows=net.project_store(proj['id']).all("SELECT rom_offset,size,gone_mark,json_extract(meta,'$.boundary_classification') AS kind "
                                           "FROM functions WHERE project_id=? AND rom_offset IS NOT NULL AND size>0",proj['id'])
    live={at for r in rows if r['gone_mark'] is None for at in range(r['rom_offset'],r['rom_offset']+r['size'],4)}
    return frozenset(at for r in rows if r['gone_mark'] is not None and r['kind'] in ('padding','data')
                     for at in range(r['rom_offset'],r['rom_offset']+r['size'],4) if at not in live)


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
    from ..rom.automatic_boundaries import alignment_fill, filler_extent
    fn=plan['pieces'][0];order='big' if proj['big_endian'] else 'little'
    if (plan['action']!='padding' or len(plan['pieces'])!=1 or not fn['target']
            or not filler_extent(fn['size'],int.from_bytes(fn['target'][:4],order)) or _decided(json.loads(fn['meta'] or '{}'))):return False
    near=[dict(r) for r in net.project_store(proj['id']).all(
        'SELECT id,name,vram,rom_offset,size,target FROM functions WHERE project_id=? AND gone_mark IS NULL AND vram BETWEEN ? AND ?',
        proj['id'],fn['vram']-65536,fn['vram']+4) if r['id']!=fn['id']]
    previous=next((r for r in near if r['vram']+r['size']==fn['vram'] and r['rom_offset'] is not None
                   and r['rom_offset']+r['size']==fn['rom_offset']),None)
    # Two entries may start at the next address (one of them a duplicate of the other's first word): either will do.
    following=[r for r in near if r['vram']==fn['vram']+4 and r['rom_offset']==fn['rom_offset']+4 and r['target']]
    if previous is None or not following:return False
    tail=previous['target'] or b''
    return alignment_fill(fn['vram'],len(tail)>=8 and int.from_bytes(tail[-8:-4],order)==JR_RA,
                          any(r['vram'] in calls or is_frame(int.from_bytes(r['target'][:4],order)) for r in following),
                          fn['vram'] in flow or _pointed_at(image,proj['big_endian'],fn['vram']))


def _duplicate(net, proj, plan, flagged):
    """Whether the previewed entry lies wholly inside another live entry with the same mapping that review doesn't
    flag: a cut that repeated the first word of a function (an N64 game had two, one from an approved function-end
    report, one from an agent's split). Archiving it loses nothing; the bytes stay in that function."""
    fn=plan['pieces'][0]
    if plan['action']!='padding' or len(plan['pieces'])!=1 or _decided(json.loads(fn['meta'] or '{}')):return False
    return net.project_store(proj['id']).one(
        'SELECT 1 FROM functions WHERE project_id=? AND id!=? AND gone_mark IS NULL AND rom_offset<=? AND rom_offset+size>=? '
        'AND size>? AND vram-rom_offset=? AND name NOT IN (SELECT value FROM json_each(?))',
        proj['id'],fn['id'],fn['rom_offset'],fn['rom_offset']+fn['size'],fn['size'],fn['vram']-fn['rom_offset'],json.dumps(sorted(flagged))) is not None


def _starts_in_delay_slot(net, proj, plan, image):
    """Whether the repaired function would begin on the delay slot of a live entry's last branch or jump: that
    word belongs to the entry before (Apply safe fixes once joined a function's tail onto the next one this way)."""
    start=plan['pieces'][0]['rom_offset']
    if start<4:return False
    word=int.from_bytes(image[start-4:start],'big' if proj['big_endian'] else 'little')
    if not rabbitizer.Instruction(word).hasDelaySlot():return False
    ids=[p['id'] for p in plan['pieces']]
    return net.project_store(proj['id']).one(
        'SELECT 1 FROM functions WHERE project_id=? AND gone_mark IS NULL AND rom_offset<=? AND rom_offset+size>? '
        'AND id NOT IN (SELECT value FROM json_each(?))',proj['id'],start-4,start-4,json.dumps(ids)) is not None


def _epilogue(code, frame, order):
    """Whether `code` ends the function whose frame is `frame` bytes: it reloads $ra from the stack, pops exactly
    that frame once, opens none of its own and ends on `jr $ra` and its delay slot. Code after a call that never
    returns would be a function of its own, which can't take $ra from a frame it never made."""
    words=[int.from_bytes(code[i:i+4],order) for i in range(0,len(code)-len(code)%4,4)]
    if len(words)<2 or words[-2]!=0x03E00008:return False
    moves=[w&0xFFFF for w in words if w&0xFFFF0000==POP]
    return moves==[frame] and any(w&0xFFFF0000==LW_RA for w in words[:-2])


def _inner_entry(code, order):
    """Whether `code` holds the start of another function after its first word: `jr $ra`, its delay slot, then
    `addiu $sp,$sp,-N`. GCC opens a function's frame once, so a frame opened after a return is a new function."""
    words=[int.from_bytes(code[i:i+4],order) for i in range(0,len(code)-len(code)%4,4)]
    return any(words[i]&0xFFFF8000==0x27BD8000 and words[i-2]==0x03E00008 for i in range(2,len(words)))


def _safe(net, proj, console, plan, calls, context=None):
    """The evidence for applying `plan` (a preview) without review, or None.
    `context`: (image, flow targets, flagged names) for the checks that read beyond the pieces."""
    if plan['action']=='padding':
        if not context:return None
        image,flow,flagged=context
        if _fill(net,proj,plan,calls,image,flow):return SAFE_NOTE['padding']
        return SAFE_NOTE['duplicate'] if _duplicate(net,proj,plan,flagged) else None
    if reason(console,{'status':'open','target':plan['raw'],'meta':'{}'}):return None
    head=plan['pieces'][0];order='big' if proj['big_endian'] else 'little'
    # A person's decision (kept as separate, set aside by you) or verified assembly is never reopened by a rule.
    if any(_decided(json.loads(p['meta'] or '{}')) for p in plan['pieces']):return None
    # A return, its delay slot and then a new stack frame start another function, wherever it sits in the extent:
    # checking only each piece's first word let 16 joins on an N64 game absorb a whole function each.
    if plan['action']!='delay_slot' and _inner_entry(plan['raw'],order):return None
    if context and _starts_in_delay_slot(net,proj,plan,context[0]):return None
    first=int.from_bytes(plan['raw'][:4],order);frame=0x10000-(first&0xFFFF) if first&0xFFFF8000==0x27BD8000 else None
    note=SAFE_NOTE[plan['action']] if plan['action'] in SAFE_NOTE else None
    for piece in sorted(plan['pieces'][1:],key=lambda p:p['vram']):
        if piece['vram'] in calls:return None
        # Zeros after a complete function are alignment padding, a person's call; a missing delay slot may be a nop.
        if plan['action']!='delay_slot' and not any(piece['target'] or b''):return None
        first=int.from_bytes((piece['target'] or b'')[:4],order)
        if len(piece['target'] or b'')>4 and first&0xFFFF8000==0x27BD8000:return None  # addiu $sp,$sp,-N
        # The code before the joint ends with a call and its delay slot: a call that never returns, so what
        # follows is likely its own function, unless it is plainly this function's ending.
        before=plan['raw'][piece['vram']-head['vram']-8:piece['vram']-head['vram']-4]
        if plan['action']!='delay_slot' and len(before)==4:
            w=int.from_bytes(before,order)
            if w>>26==3 or (w>>26==0 and w&63==9):
                if frame is None or not _epilogue(piece['target'] or b'',frame,order):return None
                note=SAFE_NOTE['epilogue']
    return note


def _safe_plans(net, slug, proj, console, calls, skip, tick=lambda done,total:None, context=None):
    from .code_coverage import boundaries
    found=[]
    layout=_layout_rows(net,proj);by_name={r['name']:r for r in layout}
    rows={r['vram']:r for r in layout if r['gone_mark'] is None and r['vram'] is not None}
    mapped=_image(net,proj)
    findings=sorted(boundaries(net,proj),key=lambda f:f['vram'] or 0)
    if context:context=(*context[:2],{f['function'] for f in findings})
    for index,finding in enumerate(findings):
        tick(index,len(findings))
        if finding['override'] or finding['function'] in skip:continue
        fn=by_name.get(finding['function'])
        if fn is None:continue  # A concurrent import added it after this discovery snapshot.
        following=rows.get(fn['vram']+fn['size']) if fn['vram'] is not None else None
        tries=[(finding['function'],'padding')] if fn['size']<=8 else []
        tries+=[(finding['function'],'delay_slot'),(finding['function'],'join_previous')]
        if following and following['unit']==fn['unit']:tries.append((following['name'],'join_previous'))
        for name,action in tries:
            try:plan=_preview(net,proj,by_name[name],action,rows=layout,mapped=mapped)
            except NetworkError:continue
            if _safe(net,proj,console,plan,calls,context):
                found.append((finding['function'],name,action));break
    return found


def _lookups(net):
    # The saved folder is still checked before each decision. Repeated lookups
    # inside that decision need not scan it again or publish an intermediate save.
    progress=getattr(net,'progress',None)
    return progress.deferring() if progress is not None else nullcontext()


def apply_safe(net, slug, log=print):
    """Publish retained fixes even after Stop or an error; no project lock spans the run."""
    try:
        return _apply_safe(net,slug,log)
    finally:
        progress=getattr(net,'progress',None)
        if progress is not None:progress.sync(slug,force=True)


def _apply_safe(net, slug, log=print):
    """Apply every repair the original bytes settle, one undoable decision each; leave the rest for review. The
    start and end fixes the importer makes (apply_entry_fixes) come first: the click asks for them even when the
    project's automatic runs are turned off."""
    proj=_supported(net,slug)
    if slug in net.rooms or net._link(slug) is not None:raise NetworkError('Leave the room before editing shared function boundaries.')
    progress=getattr(log,'progress',lambda value:None);cancel=getattr(log,'check_cancel',lambda:None)
    entry=[{**f,'flagged':f['function']} for f in _apply_entry_fixes(net,slug,proj,log)['fixed']]
    from .code_coverage import boundaries
    total=len([f for f in boundaries(net,proj) if not f['override']])
    if not total:
        log('No incomplete entries need review.');return {'fixed':entry,'remaining':0}
    image,segments=_image(net,proj)
    calls=_call_targets(image,segments,proj['big_endian'],_archived_words(net,proj));flow=_flow_targets(image,segments,proj['big_endian'])
    log(f'Checking {total} flagged entries for repairs the original bytes settle…')
    log('Reverifying existing matches before any boundary decision…')
    broken=_verify_matches(net,proj,log)
    if broken:raise NetworkError('Existing matches could not be reverified: '+', '.join(n for n,_ in broken[:5]))
    console=net.console(proj);fixed=[];failed=set()
    def checking(done,count):
        cancel()
        progress({'phase':'Checking flagged entries','done':done,'total':count})
    for _ in range(SAFE_ROUNDS):
        cancel()
        with _lookups(net):
            found=_safe_plans(net,slug,proj,console,calls,failed,checking,(image,flow))
        if not found:break
        context=(image,flow,{f['function'] for f in boundaries(net,proj)})
        for flagged,name,action in found:
            cancel()
            try:
                with _lookups(net):
                    if any(net._function(n,slug)['gone_mark'] is not None for n in {flagged,name}):continue  # absorbed this round
                    plan=preview(net,slug,name,action)
                    if not (note:=_safe(net,proj,console,plan,calls,context)):continue
                    result=_commit(net,proj,plan,name,action,plan['revision'],plan['image'],note)
            except NetworkError as error:
                # A worker claimed a piece or the layout moved under it: leave it for review rather than retry.
                failed.add(flagged);log(f'{flagged}: left for review ({error})');continue
            fixed.append({**result,'flagged':flagged})
            log(f"{flagged}: {action.replace('_',' ')} → {result['function']}")
            progress({'phase':'Applying safe fixes','done':len(fixed),'total':total})
    remaining=len([f for f in boundaries(net,proj) if not f['override']])
    log(f'Applied {len(fixed)} safe fixes; {remaining} entries still need review. Each fix can be undone from Repair history.')
    return {'fixed':entry+fixed,'remaining':remaining}


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
    if _config_inputs(proj['config'])!=_config_inputs(history['config']) or _digest([list(r) for r in net.project_store(proj['id']).all('SELECT unit,version,header FROM context_versions WHERE project_id=? ORDER BY version',proj['id'])])!=history['contexts']:
        raise NetworkError('Compiler settings, mappings or headers changed since this repair. Review those changes before undoing.')
    if _digest(_image(net,proj)[0])!=history['image']:raise NetworkError('The original image changed since this repair. Reimport/review it before undoing.')
    if history['symbol_after'] and [dict(r) for r in net.project_store(proj['id']).all('SELECT name,size FROM symbols WHERE project_id=? AND name=?',proj['id'],history['symbol_after'][0]['name'])]!=history['symbol_after']:
        raise NetworkError('The repaired symbol changed. Undo would overwrite newer work.')
    ids=[int(fid) for fid in history['after']]
    revision=_revision(net,proj,ids)
    spans=[(saved['vram'],saved['vram']+saved['size']) for saved in (*history['before'].values(),*history['after'].values())
           if saved.get('vram') is not None and saved.get('size') is not None]
    log('Checking existing matches before undo…')
    if _verify_matches(net,proj,log,only=_referring(net,proj,spans)):
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
        for symbol in history.get('created_symbols',()):db.execute('DELETE FROM symbols WHERE project_id=? AND name=?',(proj['id'],symbol))
        history['undone']=True;db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(history),key))
        _invalidate_shared_entries(db, proj['id'])
        net._event(db,proj['id'],None,'boundary','Undid boundary decision for '+history['function'])
    _invalidate(net,proj)
    return {'undone':True}


# ---- entry fixes: start and end problems the bytes settle (entry_fixes), applied at import and Check again ----

ENTRY_FIX_SETTING = 'automatic_boundary_fixes'  # the project's config: False turns the automatic fixes off
ENTRY_FIX_JOB = 'Fix proven function boundaries: '


def entry_fixes_enabled(net, proj):
    return net.cfg(proj).get(ENTRY_FIX_SETTING, True) is not False


def _declined(net, proj):
    """(start, entry) of every automatic fix someone undid: never applied again, so undo is durable."""
    out=set()
    for r in net.project_store(proj['id']).all('SELECT value FROM settings WHERE key LIKE ?',PREFIX+str(proj['id'])+':%'):
        item=json.loads(r['value'])
        if item.get('automatic') and item.get('undone'):out.add((item.get('start'),item.get('entry')))
    return out


class _Stored:
    """`address in stored`: whether an aligned word of the image holds it (searched only for the few words a fix
    would cut off; a set of every word of a 32 MB image would not be)."""

    def __init__(self, image, big_endian):
        self.image, self.big_endian, self.seen = image, big_endian, {}

    def __contains__(self, address):
        if address not in self.seen:self.seen[address]=_pointed_at(self.image,self.big_endian,address)
        return self.seen[address]


def entry_findings(net, proj, *, layout=None, mapped=None):
    """{name: entry_fixes.Fix} for the project's live functions. Only open functions nobody decided on may be
    fixed automatically, and those set aside by their shape alone (`asm_code` from asm_reason, no verdict): on an N64
    game four such functions were filed as assembly only for the stray words a fix cuts off ("writes $s3 before
    saving it"). For the rest (a person's or agents' verdict, verified or twin assembly) a fix is a suggestion at most."""
    from . import entry_fixes
    if proj['arch']!='mips':return {}
    layout=layout if layout is not None else _layout_rows(net,proj)
    image,segments=mapped if mapped is not None else _image(net,proj)
    order='big' if proj['big_endian'] else 'little'
    relocs={r['id']:r['relocs'] for r in net.project_store(proj['id']).all('SELECT id,relocs FROM functions WHERE project_id=? AND gone_mark IS NULL',proj['id'])}
    live=[r for r in layout if r['gone_mark'] is None and r['vram'] is not None and r['target']]
    entries=[entry_fixes.Entry(r['name'],r['vram'],r['target'],tuple(tuple(x) for x in json.loads(relocs.get(r['id']) or '[]')),r['status'])
             for r in live]

    def word_at(address):
        for seg in segments:
            if seg.contains(address,4):
                at=seg.rom_start+address-seg.vram
                return int.from_bytes(image[at:at+4],order) if 0<=at<=len(image)-4 else None
        return None
    # Tails a reviewed adjustment kept for review: confirmed when other code points at their first word.
    reviewed={r['name'] for r in live if (m:=json.loads(r['meta'] or '{}')).get('boundary_tail_review') and not m.get('boundary_override')}
    found=entry_fixes.decide(entries,big_endian=bool(proj['big_endian']),word_at=word_at,stored=_Stored(image,proj['big_endian']),reviewed=reviewed)
    rows={r['name']:r for r in live}
    for name,fix in found.items():
        row=rows[name];meta=json.loads(row['meta'] or '{}')
        shape_only=row['status']=='asm' and meta.get('asm_code') and not meta.get('not_c')
        if not (row['status']=='open' or shape_only) or _decided(meta):
            fix.confidence='suggest'
            fix.warning=fix.warning or (f'{name} is {"set aside" if row["status"]=="asm" else row["status"]}: a decision on it is a person\'s.')
    return found


def apply_entry_fixes(net, slug, log=print, *, preferred_entries=None):
    """Apply every entry fix the bytes settle (entry_fixes 'auto'), one undoable decision each, labelled automatic in
    Repair history; leave the rest as suggestions. Runs at import (before anything is drafted) and on Check again.
    Matched functions are never changed (the preview refuses them), and the few matches that refer into a changed
    extent are compiled again first. Idempotent: a fixed function no longer looks broken, and an undone fix is never
    applied again. Apply safe fixes runs the same fixes on a person's click, even when automatic runs are off.
    Returns {'fixed': [...], 'suggested': n}.
    ROM mode, MIPS only, like every repair here: a project-mode game's extents are its own build's (objdiff units,
    split_source), and other processors need their own entry rules (ROADMAP.md, "automatic start and end fixes")."""
    proj=net.project_row(slug)
    if proj['arch']!='mips' or not net.is_rom(proj) or not entry_fixes_enabled(net,proj):return {'fixed':[],'suggested':0}
    if slug in net.rooms or net._link(slug) is not None:return {'fixed':[],'suggested':0}  # the host's project decides
    try:
        return _apply_entry_fixes(net,slug,proj,log,preferred_entries=preferred_entries)
    finally:
        progress=getattr(net,'progress',None)
        if progress is not None:progress.sync(slug,force=True)


def _undo_key(fix):
    """What an undone automatic fix is remembered by (_declined): its history's start and entry."""
    if fix.kind=='join_previous':return (fix.at,None)  # the joined function starts where the one before did
    return (fix.vram,fix.at if fix.kind in ('trim_start','split_end') else None)


def _apply_entry_fixes(net, slug, proj, log, *, preferred_entries=None):
    cancel=getattr(log,'check_cancel',lambda:None);progress=getattr(log,'progress',lambda value:None)
    try:mapped=_image(net,proj)
    except NetworkError:return {'fixed':[],'suggested':0}
    declined=_declined(net,proj);fixed=[];failed=set();verified=False;suggested=0
    for _ in range(SAFE_ROUNDS):
        cancel()
        layout=_layout_rows(net,proj)
        found=entry_findings(net,proj,layout=layout,mapped=mapped)
        suggested=sum(f.confidence!='auto' for f in found.values())
        todo=[f for f in sorted(found.values(),key=lambda f:f.vram) if f.confidence=='auto' and f.name not in failed
              and _undo_key(f) not in declined]
        if not todo:break
        if not verified:
            # Matched code that refers into a changed extent is the code a new symbol could relink: build it
            # again first. Everything else is untouched, so a 2,000-match game doesn't recompile them all.
            spans=[(min(f.vram,f.at),f.vram+next(r['size'] for r in layout if r['name']==f.name)) for f in todo]
            matched=_referring(net,proj,spans)
            if matched:
                log(f'Checking {len(matched)} matched functions that refer into the functions to fix…')
                broken=_verify_matches(net,proj,log,only=matched)
                if broken:
                    log('Existing matches could not be reverified, so no boundary was fixed automatically: '+', '.join(n for n,_ in broken[:5]))
                    return {'fixed':[],'suggested':suggested+len(todo)}
            verified=True
        for index,fix in enumerate(todo):
            cancel()
            progress({'phase':'Fixing proven function boundaries','done':len(fixed),'total':len(fixed)+len(todo)-index})
            action=fix.kind
            note='Automatic: '+fix.summary()+'. '+' '.join(fix.evidence)
            try:
                with _lookups(net):
                    if net._function(fix.name,slug)['gone_mark'] is not None:continue
                    plan=preview(net,slug,fix.name,action,fix.at if action in ('trim_start','split_end') else None)
                    result=_commit(net,proj,plan,fix.name,action,plan['revision'],plan['image'],note,automatic=True,preferred_entries=preferred_entries)
            except NetworkError as error:
                # Claimed meanwhile or the layout moved: it stays a suggestion, tried again next time.
                failed.add(fix.name);log(f'{fix.name}: left for review ({error})');continue
            fixed.append({**result,'kind':fix.kind,'entry':fix.at})
            log(f'{fix.name}: {fix.summary()}')
    if fixed:
        log(f'Fixed {len(fixed)} proven function boundar{"y" if len(fixed)==1 else "ies"} automatically; each can be '
            'undone from Review function boundaries → Repair history.')
    return {'fixed':fixed,'suggested':suggested}


def set_entry_fixes(net, slug, enabled):
    """Turn the automatic start and end fixes on or off for a project (Review function boundaries' checkbox)."""
    _supported(net,slug)
    if not isinstance(enabled,bool):raise NetworkError('Choose whether to fix proven boundaries automatically.')
    net._update_cfg(slug,lambda cfg:cfg.update({ENTRY_FIX_SETTING:enabled}))
    return {'automatic':enabled}
