"""Polishing's mechanical work: prepared evidence, remembered failures and verified small-edit patterns.

Patterns are private to a project and never transfer a function's global name. Every
reuse goes through Readability.submit and the destination's normal exact judge.
"""
import difflib
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

from .. import compiler
from ..forks.csource import _blank
from ..forks.project import ForkProject
from ..network import NetworkError
from ..project import compiler_for
from ..seeds import HeaderIndex
from . import polish_edits

BRIEF_BYTES = 12_000
NEIGHBOURS = 8
FAILURES_KEPT = 64
MAX_PATTERN_DONORS = 64
POLISH_REVISION = 1


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def header(net, proj, fn):
    if net.is_rom(proj):
        ctx = net._context(proj['id'], fn['unit'])
        return ctx['header'] if ctx else ''
    # Reading only the unit and its local includes keeps this on-demand work bounded.
    fp = ForkProject(Path(proj['root']))
    try:unit = fp.unit(fn['unit'])
    except NetworkError:return ''
    paths = [unit.source] if unit.source else []
    texts = []
    for rel in paths[:NEIGHBOURS]:
        try:
            text = fp.safe_path(rel).read_text(encoding='utf-8')
        except (OSError, UnicodeError, NetworkError):continue
        texts.append(text)
    for name in re.findall(r'^\s*#\s*include\s+"([^"\n]+)"', '\n'.join(texts), re.M)[:NEIGHBOURS]:
        for base in (Path(unit.source or '').parent, Path('include')):
            try:
                texts.append(fp.safe_path(str(base/name)).read_text(encoding='utf-8'))
                break
            except (OSError, UnicodeError, NetworkError):continue
    return '\n'.join(texts)


def input_key(net, proj, fn, original, candidate=''):
    """Failures are evidence only for the same source and judge inputs, never a matching verdict.

    Project builds can consume arbitrary external dependencies. Their stored failures
    explain prior work, but never suppress a fresh build here; ForkEngine has its own
    repeat-compile check against the complete candidate file and harness.
    """
    config = net.cfg(proj)
    parts = [str(POLISH_REVISION), original, json.dumps(config, sort_keys=True), str(fn['id']), str(fn['target']), str(fn['relocs']),
             str(fn['vram']), str(fn['meta']), str(net._generation.get(proj['id'], 0))]
    if net.is_rom(proj):
        ctx = net._context(proj['id'], fn['unit'])
        parts += [ctx['header'], str(ctx['version'])]
        syms = net.symbols(proj['id'])
        roots = set(polish_edits.IDENT.findall(_blank(original+'\n'+candidate)))
        declarations = net._header_index(proj['id'],ctx,for_m2c=False).used(roots)
        referenced = roots | set(polish_edits.IDENT.findall(_blank('\n'.join(d.text for d in declarations))))
        parts.append(str([(name,syms.resolve(name)) for name in sorted(referenced)]))
    return digest('\0'.join(parts))


def current_key(net, proj, fn, original, source):
    store = net.project_store(proj['id'])
    current = store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    project = store.one('SELECT * FROM projects WHERE id=?',proj['id'])
    return input_key(net,project,current,original,source) if current is not None and project is not None else None


def cacheable_inputs(net, proj, fn, candidate):
    if not net.is_rom(proj):return False
    config = net.cfg(proj)
    if not config.get('compiler'):return False
    own = compiler_for(config,fn['vram'],fn)
    if not own.get('toolchain') or own.get('ro_paths'):return False
    if any(flag.startswith(('-I','-include','-imacros','/I','/FI')) for flag in own.get('flags',[])):return False
    ctx = header(net,proj,fn)
    return not re.search(r'\b__(?:DATE|TIME|TIMESTAMP)__\b',ctx+candidate)


def deterministic_error(log):
    if re.search(r'timed out|out of memory|cannot allocate|internal compiler|not found|no such file|exited with|terminated|resource',log,re.I):return False
    return bool(re.search(r'\berror\b|undeclared|unknown type|^rejected:',log,re.I))


def check_stopped(net, agent, slug):
    activity = getattr(net,'project_activity',None)
    if activity is None:return
    if any(s.agent_name == agent.name and s.slug == slug and s.stop.is_set()
           for s in list(activity.manager.sessions.values())):
        raise NetworkError('Polishing stopped. Verified changes already completed were kept.')


def failure(net, proj, fn, key, candidate):
    if not cacheable_inputs(net,proj,fn,candidate):return None
    row = net.project_store(proj['id']).one(
        'SELECT result FROM polish_failures WHERE project_id=? AND function_id=? AND input_key=? AND candidate_key=? AND cacheable=1',
        proj['id'], fn['id'], key, digest(candidate))
    return ('🔁 This polish already failed with the same source and compiler context; it was not compiled again.\n'+row['result']) if row else None


def remember_failure(net, proj, fn, key, source, result, original, cacheable=False):
    change = '\n'.join(difflib.unified_diff(original.splitlines(),source.splitlines(),n=1))[:1600]
    result = result[:6000]+'\nFailed edit:\n'+change
    store = net.project_store(proj['id'])
    with store.tx() as db:
        db.execute('INSERT OR REPLACE INTO polish_failures VALUES(?,?,?,?,?,?,?)',
                   (proj['id'],fn['id'],key,digest(source),result[:8000],time.time(),int(cacheable)))
        db.execute('DELETE FROM polish_failures WHERE project_id=? AND function_id=? AND rowid NOT IN '
                   '(SELECT rowid FROM polish_failures WHERE project_id=? AND function_id=? ORDER BY created_at DESC LIMIT ?)',
                   (proj['id'],fn['id'],proj['id'],fn['id'],FAILURES_KEPT))


def context_key(context, source, function):
    roots = set(polish_edits.IDENT.findall(_blank(source))) - set(polish_edits.locals_in(source,function,context)) - {function}
    declarations = [d.text for d in HeaderIndex(context).used(roots) if function not in d.names]
    return digest('\n'.join(declarations))


def save_pattern(net, proj, fn, original, source, context, renames, types, comment):
    shape, local = polish_edits.signature(original,fn['name'],context)
    if shape is None or re.search(r'\b'+re.escape(fn['name'])+r'\b',comment):return
    plan = {'names':[renames.get(name,name) for name in local], 'types':types,'comment':comment,
            'context':context_key(context,original,fn['name']), 'result':digest(source)}
    with net.project_store(proj['id']).tx() as db:
        db.execute('INSERT OR REPLACE INTO polish_patterns VALUES(?,?,?,?,?)',
                   (proj['id'],fn['id'],shape,json.dumps(plan),time.time()))


def learn(net, proj, fn, original, source):
    """Existing full rewrites also teach local rename/comment patterns when all C tokens agree."""
    try:
        context = header(net,proj,fn)
        before, old = polish_edits.signature(original,fn['name'],context)
        after, new = polish_edits.signature(source,fn['name'],context)
        if before is None or before != after or len(old) != len(new):return
        comment = re.match(r'\s*/\*([^\r\n]*?)\*/',source)
        comment = comment[1].strip() if comment else ''
        renames = {a:b for a,b in zip(old,new,strict=True) if a != b}
        if not renames and not comment:return
        # Reconstructing exactly the intended tokens is checked again when reused.
        save_pattern(net,proj,fn,original,source,context,renames,{},comment)
    except Exception:  # Learning is optional after a successful, already recorded polish.
        logging.getLogger('decompdecomp').exception('Could not retain a verified polish pattern')


def pattern(net, proj, fn, source, context):
    shape, local = polish_edits.signature(source, fn['name'], context)
    if shape is None:return None
    rows = net.project_store(proj['id']).all(
        'SELECT p.plan,p.function_id,f.name,f.matched_source FROM polish_patterns p JOIN functions f ON f.id=p.function_id '
        "WHERE p.project_id=? AND p.shape=? AND f.status='matched' AND f.readable=1 AND p.function_id!=? "
        'AND f.unit=? ORDER BY p.created_at DESC LIMIT ?', proj['id'], shape, fn['id'],fn['unit'],MAX_PATTERN_DONORS+1)
    if len(rows) > MAX_PATTERN_DONORS:return None
    plans = {}
    expected_context = context_key(context,source,fn['name'])
    for row in rows:
        plan = json.loads(row['plan'])
        if plan.get('context') != expected_context or plan.get('result') != digest(row['matched_source'] or ''):continue
        if len(plan['names']) != len(local):continue
        change = {'renames':{old:new for old,new in zip(local,plan['names'],strict=True) if old != new},
                  'types':plan['types'], 'comment':plan['comment']}
        plans[json.dumps(change,sort_keys=True)] = (row['name'], change)
    # Different human interpretations of the same shape: let the agent decide.
    return next(iter(plans.values())) if len(plans) == 1 else None


def structured(readability, agent, slug, function, renames='{}', comment='', types='{}', reuse=False):
    net = readability.net
    with compiler.cancellable(lambda:check_stopped(net,agent,slug)), net.progress.judging(slug):
        check_stopped(net,agent,slug)
        proj = net._project(slug)
        fn = net._function(function, slug)
        with readability._polish_locks.setdefault(fn['id'], threading.RLock()):
            return _structured_locked(readability,agent,slug,fn,proj,renames,comment,types,reuse)


def _structured_locked(readability, agent, slug, fn, proj, renames, comment, types, reuse):
    net = readability.net
    function = fn['name']
    original = readability._matching_c(proj, fn)
    context = header(net, proj, fn)
    if reuse:
        found = pattern(net, proj, fn, original, context)
        if found is None:raise NetworkError('No single verified polish pattern fits this function. Supply a small edit plan instead.')
        _, plan = found
        renames, comment, types = plan['renames'], plan['comment'], plan['types']
    renames, types = polish_edits.mapping(renames), polish_edits.mapping(types)
    source = polish_edits.apply(original,fn['name'],renames,comment,types,context)
    answer = readability._submit(agent,slug,function,source)
    if answer.startswith('✅'):
        save_pattern(net,proj,fn,original,source,context,renames,types,comment)
    return answer

def reuse(readability, agent, proj, fn):
    """A claimed exact token shape can reuse one unambiguous verified plan, judged afresh."""
    net = readability.net
    check_stopped(net,agent,proj['slug'])
    if proj['slug'] in net.links:return False
    if not net.project_store(proj['id']).one('SELECT 1 FROM polish_patterns WHERE project_id=? LIMIT 1',proj['id']):return False
    original = readability._matching_c(proj,fn)
    try:
        context = header(net,proj,fn)
        found = pattern(net,proj,fn,original,context)
        if found is None:return False
        _, plan = found
        candidate = polish_edits.apply(original,fn['name'],plan['renames'],plan['comment'],plan['types'],context)
        if failure(net,proj,fn,input_key(net,proj,fn,original,candidate),candidate):return False
        return structured(readability,agent,proj['slug'],fn['name'],reuse=True).startswith('✅')
    except NetworkError:
        return False  # unsupported scopes are ordinary polish work, not a queue failure


def brief(net, proj, fn, source):
    """Bounded evidence, without drafts, compiler searches, or opening another project's store."""
    store = net.project_store(proj['id'])
    words = set(polish_edits.IDENT.findall(_blank(source)))
    context = header(net,proj,fn)
    lines = ['\nPrepared polish evidence:']
    declarations = net._header_index(proj['id'], {'header':context, 'version':digest(context), 'unit':fn['unit']}, for_m2c=False).used(words-{fn['name']})
    declarations = [d for d in declarations if fn['name'] not in d.names]
    text = '\n'.join(d.text for d in declarations)
    if text:lines += ['Declarations and types already in use:', text[:5000]]
    if net.is_rom(proj):
        xrefs, syms = net.xrefs(proj), net.symbols(proj['id'])
        for label,key in [('Calls','calls'),('Called by','called_by')]:
            addresses = sorted(xrefs[key].get(fn['vram'],()))[:NEIGHBOURS]
            if addresses:lines.append(label+': '+', '.join(syms.label(a) for a in addresses))
        data = sorted(xrefs['uses'].get(fn['vram'],()))[:NEIGHBOURS]
        for address in data:
            try:lines.append(net.read_data(proj['slug'],syms.name_at(address)[0],32)[:600])
            except NetworkError:continue
    else:
        # No speculative source-tree walk: matched source's incremental word index already exists for renames.
        from .rename_scope import inventory
        indexed = store.read_cache.get(('polish-callers',proj['id']),store.read_version('inputs'),lambda:inventory(net,proj))
        callers = [r['name'] for r,names in indexed if fn['name'] in names and r['id'] != fn['id']]
        if callers:lines.append('Matched callers: '+', '.join(callers[:NEIGHBOURS]))
    callees = store.all('SELECT name,matched_source FROM functions WHERE project_id=? AND name IN '
                       '(SELECT value FROM json_each(?)) AND id!=? ORDER BY name LIMIT ?',
                       proj['id'],json.dumps(sorted(words)),fn['id'],NEIGHBOURS)
    for row in callees:
        body = row['matched_source'] or ''
        comment = re.match(r'\s*(/\*.*?\*/|//[^\n]*)',body,re.S)
        lines.append('Callee '+row['name']+(': '+comment[1][:400] if comment else ' (get_function for its body)'))
    neighbours = store.all("SELECT name FROM functions WHERE project_id=? AND unit=? AND name NOT GLOB 'func_*' "
                           'AND id!=? ORDER BY id LIMIT ?',proj['id'],fn['unit'],fn['id'],NEIGHBOURS)
    if neighbours:lines.append('Nearby naming style: '+', '.join(r['name'] for r in neighbours))
    failures = store.all('SELECT result FROM polish_failures WHERE project_id=? AND function_id=? ORDER BY created_at DESC LIMIT 3',proj['id'],fn['id'])
    if failures:lines += ['Previous failed edits (historical; context may have changed):']+[r['result'][:400] for r in failures]
    found = pattern(net,proj,fn,source,context)
    if found:lines.append('Verified small-edit pattern from '+found[0]+': polish_function(reuse=true) will test it here.')
    return '\n'.join(lines)[:BRIEF_BYTES]+'\n'


def safe_brief(net, proj, fn, source):
    try:return brief(net,proj,fn,source)
    except Exception:  # Optional evidence must not prevent claiming a matched function.
        logging.getLogger('decompdecomp').exception('Could not prepare polish evidence')
        return '\nPrepared evidence unavailable; use get_function/get_context or read_file.\n'


def tidy(readability, agent, slug, path):
    """Conservative declaration/include cleanup through the same judge and rollback as edit_file."""
    net = readability.net
    proj = net._project(slug)
    if net.is_rom(proj):raise NetworkError('File cleanup is for project-mode source files; ROM functions use polish_function.')
    if slug in net.links:raise NetworkError('Run file cleanup on the room host.')
    fp = ForkProject(Path(proj['root']))
    p = fp.safe_path(path,write=True)
    original = p.read_text(encoding='utf-8')
    guarded = set()
    for name in re.findall(r'^\s*#\s*include\s+"([^"\n]+)"', original, re.M):
        for rel in (str(p.parent.relative_to(fp.root)/name), str(Path('include')/name)):
            try:
                contents = fp.safe_path(rel).read_text(encoding='utf-8')
            except (OSError, UnicodeError, NetworkError):continue
            if polish_edits.guarded_header(contents):guarded.add('"'+name+'"')
            break
    updated = polish_edits.cleanup(original, guarded)
    if updated == original:return 'No duplicate declarations or includes to remove. Semantic tidying still needs review.'
    return net.forks.edit_file(agent,slug,path,original,updated,'Remove duplicate declarations and includes')
