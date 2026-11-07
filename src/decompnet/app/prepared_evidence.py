"""Private source/header/profile evidence: recompile first, publish one exact bundle atomically."""
import hashlib
import json
import time

from ..network import NetworkError
from . import source_preparation as prep


def digest(net, proj, fn):
    from .permuter_inputs import build_state
    return hashlib.sha256(repr((prep.inputs(net,proj,fn),build_state(proj))).encode()).hexdigest()


def listing(net, proj):
    rows=net.project_store(proj['id']).all("SELECT r.signature,r.feedback,f.name,f.status FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE f.project_id=? AND r.signature LIKE 'local-source-%' ORDER BY CASE WHEN r.outcome='0' THEN 0 ELSE 1 END,r.rowid DESC LIMIT 80",proj['id'])
    result=[]
    for row in rows:
        item=json.loads(row['feedback'] or '{}')
        if not item:continue
        result.append({'id':row['signature'],'function':row['name'],'score':item.get('score'),
                       'exact':bool(item.get('exact')),'flags':item.get('flags',[]),
                       'toolchain':item.get('toolchain'),'category':item.get('category','legacy'),
                       'source':item.get('source',''),'header':item.get('header',''),'diagnostic':item.get('diagnostic',''),
                       'reviewable':bool(item.get('exact') and item.get('inputs') and row['status']=='open')})
    return result


def scoped_config(proj, fn, settings):
    """Select one original entry, keeping neighbouring and overlaid entries' compiler profiles."""
    cfg=json.loads(proj['config']);lo,hi=fn['vram'],fn['vram']+fn['size'];keep=[]
    if dict(fn).get('rom_offset') is not None:
        from ..project import function_compiler_key
        # This selector is by original image position, so overlays sharing a RAM address remain independent.
        cfg.setdefault('function_compilers',{})[function_compiler_key(fn)]={
            'vram':fn['vram'],'function':fn['name'],**settings}
        return cfg
    for old in cfg.get('compiler_ranges',[]):
        for a,b in ((old['start'],min(old['end'],lo)),(max(old['start'],hi),old['end'])):
            if a<b:keep.append({**old,'start':a,'end':b})
    cfg['compiler_ranges']=sorted(keep+[{'start':lo,'end':hi,**settings}],key=lambda r:r['start'])
    return cfg


def publish_profile(net, db, fn, expected, settings, agent):
    """Inside _record's transaction. Compilation and the exact verdict already happened outside it."""
    proj=db.execute('SELECT * FROM projects WHERE id=?',(fn['project_id'],)).fetchone()
    claim=db.execute('SELECT agent_id,expires_at FROM claims WHERE function_id=?',(fn['id'],)).fetchone()
    import time

    from ..permuter_claims import nonexclusive
    if (fn['status']!='open' or prep.inputs(net,proj,fn)!=expected or
            (not nonexclusive(agent) and (not claim or claim['agent_id']!=agent.id or claim['expires_at']<=time.time()))):
        raise NetworkError('The prepared bundle changed or its claim expired. Run preparation again.')
    if not fn['size'] or fn['size']<=0:
        raise NetworkError('The function needs a valid extent before applying a compiler bundle.')
    from ..project import compiler_for
    own=compiler_for(json.loads(proj['config']),fn['vram'], fn)
    if all(own.get(k)==settings.get(k) for k in ('toolchain','flags')):
        return prep.inputs(net,proj,fn)  # private headers can change without republishing an unchanged profile
    overlaps=db.execute('SELECT id,vram,rom_offset,size FROM functions WHERE project_id=? AND id!=? '
                        'AND vram<? AND vram+size>?',
                        (fn['project_id'],fn['id'],fn['vram']+fn['size'],fn['vram'])).fetchall()
    for other in overlaps:
        if (fn['rom_offset'] is None or other['rom_offset'] is None
                or other['rom_offset']-fn['rom_offset']==other['vram']-fn['vram']):
            raise NetworkError('The compiler range overlaps another function. Resolve the boundary before applying this bundle.')
    # Profiles select function entry addresses. Distinct overlay images may overlap in RAM;
    # stop before every other entry rather than changing any neighbour's selected profile.
    next_start=db.execute('SELECT MIN(vram) FROM functions WHERE project_id=? AND vram>?',
                          (fn['project_id'],fn['vram'])).fetchone()[0]
    end=min(fn['vram']+fn['size'],next_start) if next_start is not None else fn['vram']+fn['size']
    cfg=scoped_config(proj,{**dict(fn),'size':end-fn['vram']},settings)
    from .function_profile_suggestions import settle_checks
    settle_checks(cfg,fn,settings)
    db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    net._event(db,proj['id'],agent.id,'compiler','Approved source/header/profile bundle for '+fn['name'])
    return prep.inputs(net,{**dict(proj),'config':json.dumps(cfg)},fn)


def apply(net, proj, key, log, refresh=False, include_assembly=False, agent=None):
    from .compiler_context import acceptable
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Prepared profile bundles are available for local ROM projects; imported builds own their headers and compiler settings.')
    if refresh:
        proj=net.project_row(proj['slug'])
    row=net.project_store(proj['id']).one('SELECT r.*,f.project_id FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE r.signature=? AND f.project_id=?',key,proj['id'])
    if not row:raise NetworkError('This prepared bundle is no longer available. Run preparation again.')
    item=json.loads(row['feedback'] or '{}')
    fn=net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',row['function_id'])
    if (not item.get('exact') or not item.get('inputs') or fn['status'] not in (('open','asm') if include_assembly else ('open',))
            or not all(k in item for k in ('source','header','toolchain','flags'))
            or (not refresh and item['inputs']!=digest(net,proj,fn))):
        raise NetworkError('This prepared bundle is stale or not exact. Run preparation again.')
    if agent is None:agent,_=net.local_agent('autopilot')
    if not net.permuter._hold(agent,fn,count_turn=False,seconds=60):
        raise NetworkError('This function is being worked on. Wait until it is released.')
    try:
        starting_digest=digest(net,proj,fn)
        expected=prep.inputs(net,proj,fn)
        settings={'toolchain':item['toolchain'],'flags':item['flags']}
        private={**dict(proj),'config':json.dumps(scoped_config(proj,fn,settings))}
        log('Rechecking prepared source, header, flags and ROM data…')
        result=net._compile(private,item['header'],item['source'],fn)
        if not result.ok or not acceptable(net,proj,fn,result.obj):
            raise NetworkError('The prepared bundle no longer compiles as one function. Run preparation again.')
        candidate,verdict,problems=net._evaluate(fn,proj,result.obj)
        if candidate is None or verdict is not True:
            raise NetworkError('The prepared bundle is no longer byte-identical: '+'; '.join(problems))
        if digest(net,net.project_row(proj['slug']),net.project_store(proj['id']).one('SELECT * FROM functions WHERE id=?',fn['id']))!=starting_digest:
            raise NetworkError('Build inputs changed while rechecking. Run preparation again.')
        net._record(fn,agent,0,item['source'],0,matched=True,via='local-source-review',
                    effective_settings=result.effective_settings,prepared_header=item['header'],
                    prepared_inputs=expected,prepared_compiler=settings,
                    prepared_assembly=fn['status']=='asm',restoring_exact=fn['status']=='asm')
        log('Exact source, private header and function-only compiler profile saved together.')
        return {'matched':True,'function':fn['name']}
    finally:net.permuter._let_go(agent,fn)


def try_pair(net, proj, function, source, header, log, settings=None):
    """Explicit local API candidate: keep body/header separate and rejudge before atomic promotion."""
    from .. import scoring, toolchains
    from ..project import compiler_for
    from .compiler_context import acceptable
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Private source/header pairs require a local ROM project. Imported projects use their build headers.')
    if not source.strip() or max(len(source.encode()),len(header.encode()))>prep.MAX_HEADER:
        raise NetworkError('Supply a C body and private header, each at most 1 MB.')
    fn=net._function(function,proj['slug'])
    if fn['status']!='open':
        raise NetworkError('Choose an unmatched function for a prepared source/header pair.')
    if not settings:
        settings=compiler_for(json.loads(proj['config']),fn['vram'], fn)
    if (not isinstance(settings,dict) or settings.get('cmd') or not isinstance(settings.get('toolchain'),str)
            or not isinstance(settings.get('flags'),list) or not all(isinstance(flag,str) for flag in settings['flags'])):
        raise NetworkError('Choose an installed compiler and a list of flags for this pair.')
    settings={k:settings[k] for k in ('toolchain','flags')}
    if toolchains.installed(settings['toolchain']) is None:
        raise NetworkError('Install this compiler in Compilers & tools before submitting a source/header pair.')
    agent,_=net.local_agent('autopilot')
    if not net.permuter._hold(agent,fn,count_turn=False,seconds=60):
        raise NetworkError('This function is being worked on. Wait until it is released.')
    try:
        proj=net.project_row(proj['slug']);fn=net._function(function,proj['slug'])
        expected=prep.inputs(net,proj,fn);starting_digest=digest(net,proj,fn)
        private={**dict(proj),'config':json.dumps(scoped_config(proj,fn,settings))}
        started=time.monotonic()
        log('Compiling separate source and private header with the selected profile…')
        result=net._compile(private,header,source,fn)
        if not result.ok or not acceptable(net,proj,fn,result.obj):
            from .preparation_diagnostics import compile_error
            raise NetworkError('This source/header pair did not compile as one function: '+compile_error(net,fn,result.log))
        candidate,verdict,problems=net._evaluate(fn,proj,result.obj)
        if candidate is None:
            raise NetworkError('This source/header pair could not be linked: '+'; '.join(problems))
        diff=scoring.diff(net._target_obj(fn,proj),candidate)
        exact=verdict is True  # ROM data/link verification is mandatory, not just disassembly score zero.
        score=0 if exact else max(1,diff.score)
        if digest(net,net.project_row(proj['slug']),net._function(function,proj['slug']))!=starting_digest:
            raise NetworkError('Build inputs changed while checking this pair. Submit it again.')
        from .source_lead_analysis import destination, record
        record(net,private,fn,source,header,score,diff.rows,{'family':'explicit-source-header-pair'},
               (time.monotonic()-started)*1000,scope=destination(net,proj,fn))
        if exact:
            net._record(fn,agent,0,source,0,matched=True,via='local-source-pair',
                        effective_settings=result.effective_settings,prepared_header=header,
                        prepared_inputs=expected,prepared_compiler=settings)
            log('Exact source, header and function-only profile saved together.')
        else:
            log(f'Pair scored {score}; working source, header and flags are unchanged.')
        return {'matched':exact,'score':score,'function':function}
    finally:
        net.permuter._let_go(agent,fn)


def recheck_exact(net, proj, log, limit=20):
    """Explicit batch approval: one historical exact bundle per function, each freshly judged."""
    if type(limit) is not int or not 1<=limit<=20:
        raise NetworkError('Recheck between 1 and 20 exact bundles at a time.')
    selected=[];seen=set()
    for item in listing(net,proj):
        if item['reviewable'] and item['function'] not in seen:
            selected.append(item);seen.add(item['function'])
        if len(selected)>=limit:break
    result={'tested':0,'matched':0,'errors':[]}
    for item in selected:
        log(f"Rechecking {item['function']} ({result['tested']+1}/{len(selected)})")
        try:
            outcome=apply(net,net.project_row(proj['slug']),item['id'],log,refresh=True)
            result['matched']+=bool(outcome.get('matched'))
        except NetworkError as error:
            message=item['function']+': '+str(error)
            result['errors'].append(message);log(message)
        result['tested']+=1
        if hasattr(log,'progress'):log.progress({'phase':'Rechecking exact bundles','finished':result['tested'],'total':len(selected)})
    log(f"Rechecked {result['tested']} bundles: {result['matched']} matched, {len(result['errors'])} skipped or failed.")
    return result


INPUT_LABELS = ('compiler/build settings', 'unit', 'target bytes', 'relocations', 'address', 'saved source',
                'header', 'symbols', 'extent', 'name', 'project root', 'build files')


def changed_inputs(expected, current):
    if not isinstance(expected, (list, tuple)) or len(expected) != len(current):
        return ['evidence format']
    return [label for label, a, b in zip(INPUT_LABELS, expected, current) if a != b]


def retry_inputs(expected, current):
    # A different source/header/target or chosen compiler needs a new explicit proposal. Symbol/build changes
    # may retry once because the entire explicit bundle is freshly compiled and byte judged against them.
    changed = changed_inputs(expected, current)
    return bool(changed) and set(changed) <= {'symbols', 'build files'}


def promote_measured(net, proj, fn, source, header, settings, agent, expected, *, preserve_exact=False):
    """At most two full checks; exclusive callers keep their lease, side searches freeze all inputs."""
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Automatic prepared bundles require a local ROM project.')
    for attempt in range(2):
        fresh = net.project_row(proj['slug'])
        current = net._function(fn['name'], proj['slug'])
        claim = net.project_store(proj['id']).one('SELECT agent_id,expires_at FROM claims WHERE function_id=?', current['id'])
        from ..permuter_claims import nonexclusive
        if not agent or (not nonexclusive(agent) and (not claim or claim['agent_id'] != agent.id or claim['expires_at'] <= time.time())):
            raise NetworkError('The prepared bundle claim expired or changed. Claim the function and submit a fresh proposal.')
        actual = prep.inputs(net, fresh, current)
        if actual != expected and retry_inputs(expected, actual):
            expected = actual
        try:
            return _promote_measured(net, fresh, current, source, header, settings, agent, expected, preserve_exact=preserve_exact)
        except NetworkError as error:
            latest = net._function(fn['name'], proj['slug'])
            state = prep.inputs(net, net.project_row(proj['slug']), latest)
            changed = changed_inputs(expected, state)
            if attempt == 0 and latest['status'] == 'open' and retry_inputs(expected, state):
                claim = net.project_store(proj['id']).one('SELECT agent_id,expires_at FROM claims WHERE function_id=?', latest['id'])
                if nonexclusive(agent) or (claim and claim['agent_id'] == agent.id and claim['expires_at'] > time.time()):
                    expected = state
                    continue
            if changed:
                raise NetworkError('Prepared inputs changed: '+', '.join(changed)+'. Evidence retained; submit a fresh proposal.') from error
            raise


def _promote_measured(net, proj, fn, source, header, settings, agent, expected, *, preserve_exact=False):
    """Recompile under frozen inputs; only an explicit ROM byte/data verdict wins."""
    from .compiler_context import acceptable, single_source
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Automatic prepared bundles require a local ROM project.')
    current=net._function(fn['name'],proj['slug']);fresh=net.project_row(proj['slug'])
    if current['status']!='open' or prep.inputs(net,fresh,current)!=expected:
        raise NetworkError('Prepared inputs changed before automatic recheck. Run preparation again.')
    private={**dict(fresh),'config':json.dumps(scoped_config(fresh,current,settings))}
    if preserve_exact:
        from .exact_match_inputs import capture
        exact_inputs=capture(net,private,current,{'header':header},source)
    result=net._compile(private,header,source,current)
    if result.ok and not acceptable(net,proj,fn,result.obj):
        from .function_names import canonical_source
        isolated=single_source(canonical_source(current,source),header,fn['name'],result.obj)
        if isolated:
            source,header=isolated
            result=net._compile(private,header,source,current)
    if not result.ok or not acceptable(net,proj,fn,result.obj):
        from .preparation_diagnostics import compile_error
        detail=compile_error(net,fn,getattr(result,'log','No compiler diagnostic was returned.')) if not result.ok else 'Additional function definitions remain; a separately verified compiler context is required.'
        raise NetworkError('Automatic bundle recheck did not compile as one function. '+detail)
    candidate,verdict,problems=net._evaluate(current,fresh,result.obj)
    if candidate is None or verdict is not True:
        detail='; '.join(problems) or 'Compiled instructions or referenced data differ from the original bytes.'
        raise NetworkError('Automatic bundle recheck failed linked-byte/data verification. '+detail)
    # _record atomically rechecks the claim/inputs and publishes context, profile and source.
    if preserve_exact:
        # Isolation may have recompiled a smaller source/header, under the same frozen target.
        exact_inputs.update(source=source,header=header)
        net._record(current,agent,0,source,0,matched=True,via='room-reconcile-exact',
                    effective_settings=result.effective_settings,exact_inputs=exact_inputs)
    else:
        net._record(current,agent,0,source,0,matched=True,via='local-source-auto-exact',
                    effective_settings=result.effective_settings,prepared_header=header,
                    prepared_inputs=expected,prepared_compiler=settings)
    return True


def auto_existing(net, proj, fn, agent):
    """Rejudge one untried historical exact pair, including budget-limited deep searches."""
    if not prep.settings(net,proj).get('auto_exact'):return None
    rows=net.project_store(proj['id']).all("SELECT signature FROM reuse_trials WHERE function_id=? AND outcome='0' "
                       "AND (signature LIKE 'local-source-profile-%' OR signature LIKE 'local-source-deep-profile-%') "
                       "ORDER BY rowid DESC",fn['id'])
    if not rows:return None
    scope=digest(net,proj,fn)
    checked={r['signature'] for r in net.project_store(proj['id']).all("SELECT signature FROM reuse_trials WHERE function_id=? AND signature LIKE 'local-source-auto-recheck:%'",fn['id'])}
    for row in rows:
        key='local-source-auto-recheck:'+hashlib.sha256((scope+row['signature']).encode()).hexdigest()
        if key in checked:continue
        saved=net.project_store(proj['id']).one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],row['signature'])
        if not saved:continue
        try:item=json.loads(saved['feedback'] or '{}')
        except (ValueError,TypeError):continue
        if not isinstance(item,dict) or not item.get('exact') or not all(k in item for k in ('toolchain','flags','source','header')):continue
        expected=prep.inputs(net,proj,fn)
        try:
            promote_measured(net,proj,fn,item['source'],item['header'],
                             {'toolchain':item['toolchain'],'flags':item['flags']},agent,expected)
            return {'tested':1,'matched':True}
        except NetworkError as error:
            with net.project_store(proj['id']).tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                           (fn['id'],key,'failed',str(error)[:1200]))
            return {'tested':1,'matched':False,'error':str(error)}
    return None
