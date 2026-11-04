"""Read-only, opt-in local source seeds. A mapping is a hypothesis; only the ordinary judge is progress.

Paths and mappings live in machine settings, not the portable project's config. No source build script runs.
ROM candidates get a private per-function header only on improvement. Imported projects keep their own build
headers: candidates must compile in that build, so an incompatible reference header is a reported limitation.
"""
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from .. import scoring
from ..forks import csource
from ..network import NetworkError
from ..repair_feedback import trial as repair_trial

log = logging.getLogger('decompdecomp')
MAX_FILES, MAX_BYTES = 512, 16 * 1024 * 1024
MAX_HEADER = 1024 * 1024
_IDENT = r'[A-Za-z_]\w*'
_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\b' + _IDENT + r'\b', re.S)
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"\n]+)[>"]\s*$', re.M)


def settings(net, proj):
    row = net.store.one('SELECT value FROM settings WHERE key=?', f'source-preparation:{proj["id"]}')
    return json.loads(row['value']) if row else {}


def mappings(text):
    result, targets, aliases = [], set(), {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*({_IDENT})\s*@\s*(.+?)\s*', line)
        if not match:
            raise NetworkError('Use one mapping per line: destination = source_symbol @ relative/file.c')
        destination, source, file = match.groups()
        if destination in targets or (source in aliases and aliases[source] != destination):
            raise NetworkError('Each destination and source symbol must have one unambiguous mapping.')
        targets.add(destination)
        aliases[source] = destination
        result.append((destination, source, file))
        if len(result) > MAX_FILES:
            raise NetworkError('Use at most 512 source mappings per project.')
    return result


def overrides(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*(-?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*)\s*', line)
        if not match:
            raise NetworkError('Macro overrides must be numeric, for example OBJECT_LIMIT=128.')
        result[match[1]] = match[2]
    return result


def symbol_aliases(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*({_IDENT})\s*', line)
        if not match or match[1] in result:
            raise NetworkError('Use one unambiguous symbol alias per line: reference_name = destination_name.')
        result[match[1]] = match[2]
    return result


def contexts(text):
    """Explicit coherent source/header alternatives, never cross-product SDK revisions."""
    result=[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        parts=line.split('|')
        if len(parts)!=2 or not parts[1].strip():
            raise NetworkError('Use destination = source_symbol @ relative/file.c | relative/header.h for each context alternative.')
        items=mappings(parts[0])
        if len(items)!=1:raise NetworkError('Each context alternative needs one function mapping.')
        result.append((*items[0],parts[1].strip()))
        if len(result)>16:raise NetworkError('Select at most 16 coherent source/header alternatives.')
    return result


def configure(net, proj, data):
    cfg = {key: str(data.get(key, '')).strip() for key in ('root', 'header', 'include_roots', 'mappings', 'overrides', 'aliases', 'definitions', 'contexts', 'helpers', 'assembly_header', 'assembly_mappings')}
    cfg['enabled'] = bool(data.get('enabled'))
    if type(data.get('auto_exact',False)) is not bool:
        raise NetworkError('Choose the automatic exact-bundle option with its checkbox.')
    cfg['auto_exact'] = data.get('auto_exact', False)
    if any(len(value) > 128_000 for value in cfg.values() if isinstance(value, str)):
        raise NetworkError('Source preparation settings are too large; choose a smaller mapping.')
    from . import source_definitions
    source_definitions.mappings(cfg['definitions'])
    source_definitions.mappings(cfg['helpers'])
    contexts(cfg['contexts'])
    mappings(cfg['mappings'])
    assembly=mappings(cfg['assembly_mappings'])
    for _,_,file in assembly:
        if Path(file).suffix.lower() not in ('.s','.asm'):raise NetworkError('Assembly mappings need .s, .S or .asm source files.')
    overrides(cfg['overrides'])
    bindings=symbol_aliases(cfg['aliases'])
    for dest,symbol,_ in mappings(cfg['mappings'])+assembly:
        if symbol in bindings and bindings[symbol]!=dest:raise NetworkError('Source and assembly aliases must agree on each symbol destination.')
        bindings[symbol]=dest
    if cfg['enabled']:
        reader = Reader(cfg)
        for _, _, file in mappings(cfg['mappings'])+assembly:
            reader.file(reader.root / file)
        for _, file in source_definitions.mappings(cfg['definitions']):
            reader.file(reader.root / file)
        for _, file in source_definitions.mappings(cfg['helpers']):
            reader.file(reader.root / file)
        for _, _, file, header in contexts(cfg['contexts']):
            reader.file(reader.root / file)
            reader.file(reader.root / header)
        for key in ('header','assembly_header'):
            if cfg[key]:reader.file(Path(cfg[key]).expanduser())
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   (f'source-preparation:{proj["id"]}', json.dumps(cfg)))
    return cfg


class Reader:
    """Bound all reads to the explicitly selected roots, including symlink targets and recursive includes."""
    def __init__(self, cfg):
        if not cfg.get('root'):
            raise NetworkError('Choose a local reference source folder first.')
        self.root = Path(cfg['root']).expanduser().resolve()
        self.roots = [self.root] + [Path(p).expanduser().resolve() for p in cfg.get('include_roots', '').splitlines() if p.strip()]
        if not all(p.is_dir() for p in self.roots):
            raise NetworkError('The reference folder and each include root must be existing folders.')
        self.overrides = overrides(cfg.get('overrides', ''))
        self.cache, self.bytes = {}, 0
        self.hashes = {}
        self.advisories = []

    def file(self, path):
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise NetworkError('A reference file leaves the selected source/include folders. Add its folder explicitly.')
        if path.suffix.lower() not in {'.c', '.h', '.cc', '.cpp', '.hpp', '.inc', '.s', '.asm'} or not path.is_file():
            raise NetworkError('Choose an existing C/C++ or assembly source, or declarations header.')
        if path not in self.cache:
            if len(self.cache) >= MAX_FILES or path.stat().st_size + self.bytes > MAX_BYTES:
                raise NetworkError('Reference reads exceed 512 files / 16 MB. Choose a smaller source subset.')
            text = path.read_bytes()
            self.bytes += len(text)
            self.hashes[path] = hashlib.sha256(text).hexdigest()
            self.cache[path] = text.decode('utf-8', errors='replace').replace('\r\n', '\n')
        return self.cache[path]

    def flatten(self, path, seen=None):
        seen = set() if seen is None else seen
        path = path.resolve()
        if path in seen:
            return ''  # guards/cycles do not warrant an unbounded include walk
        if len(seen) >= 64:
            raise NetworkError('Reference includes exceed 64 levels; supply a prepared declarations header.')
        seen.add(path)
        text = self.file(path)
        def include(match):
            name = match[1]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise NetworkError('Use relative includes within the selected reference folders.')
            candidates = [path.parent / name] + [root / name for root in self.roots]
            found = next((p for p in candidates if p.is_file()), None)
            if found is None:
                raise NetworkError(f'Include {name!r} was not found. Add its SDK folder as an include root or supply a prepared declarations header.')
            return self.flatten(found, seen)
        text = _INCLUDE.sub(include, text)
        # Apply retail values where they are defined, before any dependent struct declarations.
        for name, value in self.overrides.items():
            pattern = re.compile(r'^(\s*#\s*define\s+' + re.escape(name) + r')[ \t]+(?:[^\n\\]|\\\n)*', re.M)
            text = pattern.sub(lambda m, value=value: m[1] + ' ' + value, text)
        if len(text.encode()) > MAX_HEADER:
            raise NetworkError('Flattened declarations exceed 1 MB; supply a smaller declarations header.')
        seen.remove(path)
        return text


def rename(text, aliases):
    return _TOKEN.sub(lambda m: aliases.get(m[0], m[0]), text)


def declarations(text):
    """Remove function bodies without collapsing their original line breaks. This is deliberately conservative."""
    clean, out, start, depth, at = csource._code_only(text), [], None, 0, 0
    for i, char in enumerate(clean):
        if char == '{':
            if depth == 0 and clean[:i].rstrip().endswith(')'):
                start = i
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0 and start is not None:
                out.extend((text[at:start], ';' + re.sub(r'[^\n]', ' ', text[start + 1:i + 1])))
                at, start = i + 1, None
    return ''.join(out) + text[at:]


def prepare(reader, cfg, mapping, aliases, fallback):
    destination, symbol, file = mapping
    path = reader.root / file
    original = reader.file(path)
    from . import source_conditions, source_definitions
    initial = reader.overrides
    if cfg.get('header'):
        header, macros = source_conditions.select(reader.flatten(Path(cfg['header']).expanduser()), initial, reader.overrides)
    elif '#include' in original:
        flattened, macros = source_conditions.select(reader.flatten(path.resolve()), initial, reader.overrides)
        header = declarations(flattened)
    else:
        header = fallback
        _, macros = source_conditions.select(fallback, initial, reader.overrides)
    original, _ = source_conditions.select(original, macros, reader.overrides)
    span = csource.find(original, symbol)
    if not span or csource.is_stub(original[span[0]:span[1]]):
        raise NetworkError(f'No C definition of {symbol!r} in its mapped file. Check the mapping.')
    if csource.find(original[span[1]:], symbol):
        raise NetworkError(f'More than one definition of {symbol!r} is present. Supply a source subset with the intended version.')
    # Keep the exact body formatting and its original line number, including __LINE__ uses.
    body = original[span[0]:span[1]]
    # An unused static definition can disappear entirely when compiled alone. Expose only this mapped
    # entry point; retain columns/line breaks and let the normal destination judge settle the result.
    clean = csource._blank(body)
    name_at = clean.find(symbol)
    if name_at >= 0:
        for match in reversed(list(re.finditer(r'\bstatic\b', clean[:name_at]))):
            body = body[:match.start()] + ' ' * len(match[0]) + body[match.end():]
    source = f'#line {original[:span[0]].count(chr(10)) + 1} "reference.c"\n' + rename(body, aliases)
    from .source_type_context import referenced
    header += '\n' + referenced(original, body, header)
    from .source_context import compatibility, helpers, prototypes
    reader.advisories.extend(compatibility(original, body, header))
    from .source_next_steps import prototype_hints
    reader.advisories.extend(prototype_hints(original, body, header))
    from .source_storage import scope_hints
    reader.advisories.extend(scope_hints(rename(body,aliases),source,rename(header,aliases),destination))
    from .source_match_context import aggregate_hints
    reader.advisories.extend(aggregate_hints(body, symbol, header))
    helper_context=helpers(reader, cfg, body, header, macros)
    header += helper_context
    from .source_match_context import macro_context
    reference_macros = original
    if cfg.get('header') and '#include' in original:
        # Read only the mapped file's authorized include tree; retain macros, not its functions/data.
        try:
            reference_macros, _ = source_conditions.select(reader.flatten(path.resolve()), macros, reader.overrides)
        except NetworkError as error:
            reader.advisories.append('Reference include macros could not be read; retaining the explicit private header. Add the matching SDK include roots to recover missing macros. '+str(error))
    header += macro_context(reference_macros, body+helper_context, header)
    from .source_declarations import referenced as object_declarations
    # Includes and file-local enums/objects can be absent from an explicit SDK header.
    # Keep declarations only; initializer placement remains a separate judged trial.
    references = reference_macros
    extra = object_declarations(declarations(references), body+helper_context, header)
    header += '\n'+referenced(references, body+helper_context+extra, header)+'\n'+extra
    header += prototypes(original, body, header, symbol)
    # Include trees may contain declarations absent from an explicitly selected header.
    # Append actual signatures only; unknown types fail compilation without shared edits.
    from .cpu_source_context import declarations as reference_declarations
    from .source_match_context import macros as declared_macros
    from .source_next_steps import called
    available=reference_declarations(reference_macros,called(body)-{symbol})
    current=reference_declarations(header,set(available))
    for name,declaration in available.items():
        if name not in current and name not in declared_macros(header) and not re.search(r'\(\s*\*\s*'+re.escape(name)+r'\b',csource._blank(header)):header += "\n"+declaration+"\n"
    selected = []
    from .source_match_context import dependencies
    used = dependencies(source+helper_context, header)
    for name, file in source_definitions.mappings(cfg.get('definitions', '')):
        if name not in used and aliases.get(name,name) not in used:
            continue
        text, _ = source_conditions.select(reader.flatten(reader.root / file), macros, reader.overrides)
        definition = source_definitions.extract(declarations(text), name)
        selected.append(rename(source_definitions.externalize(definition), aliases))
    if cfg.get('_same_file_definitions'):
        explicit={name for name,_ in source_definitions.mappings(cfg.get('definitions',''))}
        reference_used=used | {name for name,target in aliases.items() if target in used}
        for name,definition in source_definitions.related(declarations(original),reference_used-explicit-{symbol}):
            # A header's actual definition already supplies layout knowledge; never duplicate it.
            try:source_definitions.extract(declarations(header),name)
            except NetworkError:
                selected.append(rename(source_definitions.externalize(definition),aliases))
                reader.advisories.append(f'Trying original mapped-file object {name}; its initializer/layout is unchanged and standalone placement still needs verification.')
    if selected:
        source = '\n'.join(selected) + '\n' + source
    return source, rename(header, aliases)


def prepare_variants(reader, cfg, mapping, aliases, fallback):
    """Prefer authorized definitions, keeping the exact source identity for each coherent context."""
    result=[]
    reader.origins={}
    def add(pair, origin):
        if pair not in result:result.append(pair)
        item={'symbol':origin[1],'file':origin[2]}
        existing=reader.origins.setdefault(pair,{**item,'family':'original-context','alternatives':[]})
        if item not in existing['alternatives']:existing['alternatives'].append(item)
    for variant_cfg in ({**cfg,'_same_file_definitions':True},cfg,*([{**cfg,'definitions':''}] if cfg.get('definitions') else [])):
        try:add(prepare(reader,variant_cfg,mapping,aliases,fallback),mapping)
        except NetworkError as error:reader.advisories.append('Base source context unavailable: '+str(error))
    count=0
    for dest,symbol,file,header in contexts(cfg.get('contexts','')):
        if dest!=mapping[0]:continue
        origin=(dest,symbol,file)
        try:add(prepare(reader,{**cfg,'header':str(reader.root/header)},origin,{**aliases,symbol:dest},fallback),origin)
        except NetworkError as error:reader.advisories.append('Alternative source/header unavailable: '+str(error))
        count+=1
        if count>=16:break
    if not result:raise NetworkError('No coherent source/header candidate could be prepared. Check the mapping and include folders.')
    from .reference_context_recovery import configurations
    for family,s,h in configurations(reader,cfg,mapping,aliases,fallback,prepare):
        add((s,h),mapping);reader.origins[(s,h)]['family']=family
    from . import source_conditions
    from .source_predicates import variants as predicate_contexts
    from .source_signedness import variants as signed_contexts
    for pair in list(result):
        origin=reader.origins[pair]
        origin_mapping=(mapping[0],origin['symbol'],origin['file'])
        pair_aliases={**aliases,origin['symbol']:mapping[0]}
        reference=''
        try:
            _,macrovalues=source_conditions.select(pair[1],reader.overrides,reader.overrides)
            reference,_=source_conditions.select(reader.file(reader.root/origin_mapping[2]),macrovalues,reader.overrides)
            reference=rename(reference,pair_aliases)
        except NetworkError as error:
            reader.advisories.append('Predicate helper context unavailable; retaining the original candidate. '+str(error))
        from .cpu_source_context import variants as reference_trials
        hypotheses=[(family,(s,h)) for family,s,h in reference_trials(pair[0], pair[1], reference, cfg.get('_target_calls',[]))] if reference else []
        if cfg.get('header') and '#include' in reader.file(reader.root/origin_mapping[2]):
            try:
                included,_=source_conditions.select(reader.flatten(reader.root/origin_mapping[2]),macrovalues,reader.overrides)
                included=rename(included,pair_aliases)
                hypotheses += [(family,(s,h)) for family,s,h in reference_trials(pair[0],pair[1],included,cfg.get('_target_calls',[]))]
            except NetworkError as error:
                reader.advisories.append('Include ABI recovery unavailable: '+str(error))
        from .reference_stage_repairs import stages
        hypotheses += [(family,(s,h)) for family,s,h in stages(pair[0],mapping[0],pair[1],reference,cfg.get('_target_calls',[]))]
        from .reference_context_recovery import static_helpers
        hypotheses += [(family,(s,h)) for family,s,h in static_helpers(pair[0],pair[1],reference)]
        from .retail_source_repairs import commented_calls
        hypotheses += [(family,(s,h)) for family,s,h in commented_calls(pair[0],pair[1],cfg.get('_target_calls',[]))]
        hypotheses += [('predicate-helper-context',candidate) for candidate in predicate_contexts(pair[0],mapping[0],pair[1],reference)]
        hypotheses += [('mask-signedness-context',candidate) for candidate in signed_contexts(*pair)]
        for family,candidate in hypotheses:
            if cfg.get('_build_owned') and candidate[1]!=pair[1]:continue
            if candidate in result:continue
            add(candidate,origin_mapping)
            reader.origins[candidate]['family']=family
    if cfg.get('_compiler_units'):
        from .source_units import variants as compiler_units
        for pair in list(result):
            origin=reader.origins[pair]
            if origin.get('family')!='original-context':continue
            origin_mapping=(mapping[0],origin['symbol'],origin['file'])
            for family,s,h in compiler_units(reader,cfg,origin_mapping,{**aliases,origin['symbol']:mapping[0]},*pair):
                if (s,h) not in result:
                    add((s,h),origin_mapping);reader.origins[(s,h)]['family']=family
    from .helper_expansion import variants as expand_helpers
    for pair in list(result)[:2]:
        for _, expanded in expand_helpers(pair[0],mapping[0],pair[1]):
            add((expanded,pair[1]),mapping)
    return result


def inputs(net, proj, fn):
    """The destination state that a private-header compile was judged against, checked again before promotion."""
    ctx = net._context(proj['id'], fn['unit'])
    symbols = [tuple(r) for r in net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    from .permuter_inputs import build_state
    cfg = json.loads(proj['config'])
    from ..project import compiler_for
    own = compiler_for(cfg, fn['vram'], fn) if 'compiler' in cfg else None
    for key in ('compiler_ranges', 'function_compilers', 'boundaries', 'suggestions', 'knowledge_auto'):
        cfg.pop(key, None)
    cfg['compiler'] = own
    return (json.dumps(cfg, sort_keys=True), fn['unit'], fn['target'], fn['relocs'], fn['vram'], fn['best_source'], ctx['version'], symbols,
            fn['size'],fn['name'],proj['root'],build_state(proj))


def promote_context(net, db, fn, expected, header, agent):
    """Called inside _record's transaction, after an ordinary successful compile/link, before writing the attempt."""
    proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
    claim = db.execute('SELECT agent_id,expires_at FROM claims WHERE function_id=?', (fn['id'],)).fetchone()
    if not claim or claim['agent_id'] != agent.id or claim['expires_at'] <= time.time():
        raise NetworkError('The function claim changed during preparation. Run the pass again.')
    if fn['status'] != 'open' or inputs(net, proj, fn) != expected:
        raise NetworkError('The function, header or build inputs changed during preparation. Run the pass again.')
    version = net._next_version(db, proj['id'])
    unit = f'local_source_{fn["id"]}'
    db.execute('INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) VALUES(?,?,?,?,?,?,?)',
               (proj['id'], version, header, agent.id, 'Judge-verified local reference declarations', time.time(), unit))
    db.execute('UPDATE functions SET unit=?, permuted=0 WHERE id=?', (unit, fn['id']))
    net._event(db, proj['id'], agent.id, 'context', f'Local reference declarations for {fn["name"]}')
    return version


def attempt(permuter, proj, fn, source, header, agent, fork, diagnostic=None, lead=None, measurement=None):
    net = permuter.net
    if fork:
        # The existing build must supply its own compatible types. Never edit its build setup or headers.
        started=time.monotonic()
        score=net.forks.attempt(agent, fork, fn['name'], source, keep=False, quiet=fn['best_score']).get('score')
        if hasattr(net,'store'):
            from .source_lead_analysis import record
            record(net,proj,fn,source,net.forks.permuter_header(fork,fn['name']),score,[],lead,(time.monotonic()-started)*1000)
        return score
    from .source_lead_analysis import destination, record
    started=time.monotonic();scope=destination(net,proj,fn)
    expected = inputs(net, proj, fn)
    result = net._compile(proj, header, source, fn)
    if not result.ok:
        from .preparation_diagnostics import compile_error
        record(net,proj,fn,source,header,None,[],lead,(time.monotonic()-started)*1000,error=result.log,scope=scope)
        raise NetworkError('Prepared C did not compile: ' + compile_error(net, fn, result.log))
    from .source_context import compiler_hints
    for hint in compiler_hints(result.log):
        log.warning('source preparation %s: %s', fn['name'], hint)
        if diagnostic:diagnostic(fn['name'] + ': ' + hint)
    if measurement is not None and net.is_rom(proj):
        from .source_symbol_recovery import helpers
        aliases = helpers(net,proj,fn,result.obj)
        if aliases:measurement['helper_aliases'] = aliases
    from .compiler_context import acceptable
    if net.is_rom(proj) and not acceptable(net,proj,fn,result.obj):
        record(net,proj,fn,source,header,None,[],lead,(time.monotonic()-started)*1000,error='Extra emitted function bodies',scope=scope)
        raise NetworkError('Prepared declarations emitted extra function bodies. Supply a declarations-only header.')
    candidate, verdict, problems = net._evaluate(fn, proj, result.obj)
    if candidate is None:
        record(net,proj,fn,source,header,None,[],lead,(time.monotonic()-started)*1000,error='; '.join(problems),scope=scope)
        raise NetworkError('Prepared C could not be linked: ' + '; '.join(problems)[:1200])
    diff = scoring.diff(net._target_obj(fn, proj), candidate)
    if verdict is not None:
        diff = scoring.DiffResult(0 if verdict else max(1, diff.score), diff.max_score, diff.rows, verdict)
    from .cpu_source_repairs import locations
    source_locations = locations(result.obj,fn['name'],diff.rows,source) if not diff.bytes_match else {}
    if measurement is not None and not diff.bytes_match:
        from .. import arch as arches
        recover = arches.get(proj['arch']).source_symbol_bindings
        if recover and net.is_rom(proj):
            measurement['symbol_bindings'] = recover(result.obj,fn['name'],fn['target'],fn['vram'],net.symbols(proj['id']).resolve)
    record(net,proj,fn,source,header,0 if diff.bytes_match else max(1,diff.score),diff.rows,lead,
           (time.monotonic()-started)*1000,scope=scope,source_locations=source_locations)
    if diagnostic and dict(proj).get('arch')=='mips':
        from .sdk_io_repairs import hints
        for hint in hints(source,dict(fn).get('asm',''),diff.rows):diagnostic(fn['name']+': '+hint)
    if diagnostic:
        from .source_context_diagnostics import hints as context_hints
        from .source_lead_analysis import compare
        for hint in context_hints(source,compare(diff.rows,proj['arch']),fn['asm'] or '',diff.rows,proj['arch'],source_locations):diagnostic(fn['name']+': '+hint)
        from .source_storage import object_hints, typed_object_hints
        for hint in object_hints(diff.rows,header)+typed_object_hints(diff.rows,source,header):diagnostic(fn['name']+': '+hint)
        if dict(proj).get('arch')=='mips':
            from .source_storage import wide_argument_hints
            for hint in wide_argument_hints(fn['asm'] or '',header):diagnostic(fn['name']+': '+hint)
    if diagnostic and hasattr(net,'reader'):
        from .switch_tables import hints, read
        for hint in hints(source,fn['name'],read(net,proj,fn),header):diagnostic(fn['name']+': '+hint)
    score = 0 if diff.bytes_match else max(1, diff.score)
    if measurement is not None and score:
        measurement.update(rows=[list(r) for r in diff.rows[:512]],lines=source_locations)
    assembly=fn['status']=='asm'
    if (not assembly or diff.bytes_match) and (fn['best_score'] is None or score < fn['best_score'] or assembly and diff.bytes_match):
        net._record(fn, agent, score, source, 0, matched=diff.bytes_match, via='local-source',
                    effective_settings=getattr(result, 'effective_settings', None),
                    prepared_header=header, prepared_inputs=expected, prepared_assembly=assembly, restoring_exact=assembly)
        net.keep_diff(agent.id, fn['id'], diff.rows)
    return score


def run(permuter, proj, st, log_line=lambda line: None, budget=8, seconds=20, only=None, include_assembly=False, agent=None):
    """A bounded, cached preparation round before ordinary deterministic/random permutations."""
    net = permuter.net
    cfg = settings(net, proj)
    report = {'tested': 0, 'improved': 0, 'matched': 0, 'errors': []}
    if not cfg.get('enabled') or proj['slug'] in net.links:
        return report
    try:
        reader = Reader(cfg)
        mapped = mappings(cfg.get('mappings', ''))
        aliases = symbol_aliases(cfg.get('aliases', ''))
        for dest, source, _ in mapped:
            if source in aliases and aliases[source] != dest:
                raise NetworkError('A function mapping conflicts with a symbol alias. Keep one destination per source symbol.')
            aliases[source] = dest
        if agent is None:agent, _ = net.local_agent('autopilot')
        st.preparation_agent=agent
        fork = net.fork_slug(proj['slug'])
        deadline = time.monotonic() + seconds
        if cfg.get('assembly_mappings') and not fork:
            from .source_assembly import run as assemble
            assembly=assemble(net,proj,cfg,log_line,lambda:st.stop.is_set() or time.monotonic()>=deadline,budget,only,agent)
            report['assembly_verified']=assembly['verified']+assembly['cached'];report['tested']+=assembly['tested'];report['errors']+=assembly['errors']
            st.add(variants=assembly['tested'])
        for mapping in mapped:
            if only is not None and mapping[0] not in only:
                continue
            if st.stop.is_set() or report['tested'] >= budget or time.monotonic() >= deadline:
                break
            fn = net.store.one("SELECT * FROM functions WHERE project_id=? AND name=? AND (status='open' OR (? AND status='asm'))", proj['id'], mapping[0],bool(include_assembly and not fork))
            if not fn or not permuter._hold(agent, fn, count_turn=False, seconds=seconds):
                continue
            st.holding(fn['name'], True)
            cache_key = None
            improved_source = False
            try:
                fresh = net.project_row(proj['slug'])
                fn = net.store.one('SELECT * FROM functions WHERE id=?', fn['id'])
                if fn['status'] != 'open' and not (include_assembly and fn['status']=='asm'):
                    continue
                if not fork and fn['status']=='open' and cfg.get('auto_exact'):
                    from .prepared_evidence import auto_existing
                    checked=auto_existing(net,fresh,fn,agent)
                    if checked:
                        report['tested']+=checked['tested'];st.add(variants=checked['tested'])
                        if checked['matched']:
                            report['matched']+=1;report['improved']+=1;st.add(matched=1,improved=1)
                            log_line(fn['name']+': historical exact bundle reverified and saved automatically.')
                            continue
                        log_line(fn['name']+': automatic bundle recheck failed; trying current source. '+checked.get('error',''))
                        if report['tested']>=budget or st.stop.is_set() or time.monotonic()>=deadline:break
                fallback = net.forks.permuter_header(fork, fn['name']) if fork else net._context(proj['id'], fn['unit'])['header']
                reader.advisories.clear()
                log_line(fn['name'] + ': reading and preparing selected source context…')
                from .source_lead_analysis import features
                target_calls=features([line.split('*/',1)[-1].strip() for line in (fn['asm'] or '').splitlines()], fresh['arch'])['calls']
                unit_cfg={'_compiler_units':not fork and fresh['arch']=='mips','_helper_units':not fork and fresh['arch']=='mips','_known_functions':[r['name'] for r in net.store.all('SELECT name FROM functions WHERE project_id=?',proj['id'])],
                          '_matched_functions':[r['name'] for r in net.store.all("SELECT name FROM functions WHERE project_id=? AND status='matched'",proj['id'])]}
                variants = prepare_variants(reader, {**cfg,**unit_cfg,'_build_owned':bool(fork),'_target_calls':target_calls}, mapping, aliases, fallback)
                if time.monotonic() >= deadline:
                    log_line(fn['name'] + ': source preparation used this round’s time allowance before compilation. Use a smaller coherent header before retrying.')
                    break
                if not fork:
                    from .cpu_source_context import variants as call_contexts
                    from .source_lead_analysis import features
                    target_calls=features([line.split('*/',1)[-1].strip() for line in (fn['asm'] or '').splitlines()], fresh['arch'])['calls']
                    for body, context in list(variants)[:2]:
                        for family,s,h in call_contexts(body,context,'',target_calls):
                            pair=(s,h)
                            if pair not in variants:
                                variants.append(pair);reader.origins[pair]={**reader.origins[(body,context)],'family':family}
                    from .source_units import matched_owners
                    for body,context in list(variants)[:1]:
                        for family,s,h in matched_owners(net,fresh,fn,body,context):
                            if (s,h) not in variants:
                                variants.append((s,h));reader.origins[(s,h)]={**reader.origins[(body,context)],'family':family}
                    from .reference_context_recovery import neighbours
                    for body,context in list(variants)[:2]:
                        for family,s,h in neighbours(net,fresh,fn,body,context):
                            pair=(s,h)
                            if pair not in variants:
                                variants.append(pair);reader.origins[pair]={**reader.origins[(body,context)],'family':family}
                    from .related_source_context import candidates as related_contexts
                    for body, context in list(variants):
                        for related in related_contexts(net, fresh, fn, body, context):
                            pair=(body,related['header'])
                            if pair not in variants:
                                variants.append(pair)
                                reader.origins[pair]={**reader.origins[(body,context)],'family':'verified-neighbour-context',
                                                      'context_from':related['function'],'shared_types':related['types'],
                                                      'shared_calls':related['calls']}
                                log_line(fn['name']+': trying private context from exact '+related['function']+'; each candidate is judged separately.')
                if not fork and hasattr(net, 'reader'):
                    from .named_array_context import variants as named_arrays
                    for body, context in list(variants):
                        extra=named_arrays(body,fn['name'],context,net.reader(fresh,fn),net.symbols(proj['id']).resolve,
                                           'big' if json.loads(fresh['config']).get('big_endian',True) else 'little')
                        for pair in extra:reader.origins[pair]={**reader.origins[(body,context)],'family':'named-array'}
                        variants.extend(extra)
                    from .source_lead_analysis import rank_pairs
                    variants=rank_pairs(net,fresh,fn,variants,{p:m['family'] for p,m in reader.origins.items()})
                    # Reserve early compile budget for the baseline and a real callback owner.
                    address=[p for p in variants if reader.origins.get(p,{}).get('family')=='cpu-address-source-unit']
                    if address:variants=variants[:1]+address+[p for p in variants[1:] if p not in address]
                for hint in dict.fromkeys(reader.advisories):log_line(fn['name'] + ': ' + hint)
                retried = False
                for source, header in variants:
                    from .source_context import context_hints
                    for hint in context_hints(source, header):log_line(fn['name'] + ': ' + hint)
                    if st.stop.is_set() or report['tested'] >= budget or time.monotonic() >= deadline:
                        break
                    fresh = net.project_row(proj['slug'])
                    fn = net.store.one('SELECT * FROM functions WHERE id=?', fn['id'])
                    if fn['status'] != 'open' and not (include_assembly and fn['status']=='asm'):
                        break
                    # Imported build files can change independently: retry on a new explicit pass/session.
                    from .permuter_inputs import build_state
                    state = (st.started_at, fresh['config'], build_state(fresh)) if fork else inputs(net, fresh, fn)
                    from .cpu_source_recovery import pending, trial_key
                    cache_key = trial_key(state,source,header,cfg)
                    lead=reader.origins.get((source,header),{'symbol':mapping[1],'file':mapping[2],'family':'original-context'})
                    if not fork:
                        from ..project import compiler_for
                        from .source_lead_analysis import destination, rejected
                        if rejected(net,fn,destination(net,fresh,fn),source,header,lead,compiler_for(json.loads(fresh['config']),fn['vram'], fn)):
                            log_line(fn['name']+': retained rejected source lead; unchanged trial skipped.')
                            continue
                    cached=net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], cache_key)
                    if cached:
                        if not fork:
                            from .cpu_source_recovery import extend
                            extend(net,fresh,fn,variants,reader.origins,source,header,json.loads(cached['feedback'] or '{}').get('cpu',{}),lead)
                            if not pending(net,fn,state,cfg,variants,reader.origins,(source,header)):
                                private_profiles(net, fresh, fn, source, header, st, report, log_line, budget, deadline, reader.origins.get((source,header)))
                        continue
                    report['tested'] += 1
                    st.add(variants=1, repairs=1)
                    log_line(f'Preparing {fn["name"]} ({report["tested"]}/{budget})')
                    measurement={}
                    with repair_trial({'kind': 'local source preparation', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                       'header_sha256': hashlib.sha256(header.encode()).hexdigest()}):
                        try:
                            score = attempt(permuter, fresh, fn, source, header, agent, fork, log_line, lead, measurement)
                        except NetworkError as exc:
                            if (str(exc).startswith('The function, header or build inputs changed') and not fork
                                    and not retried and not st.stop.is_set() and time.monotonic()<deadline
                                    and report['tested']<budget):
                                retried=True
                                current_fn=net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
                                fallback=net._context(proj['id'],current_fn['unit'])['header']
                                rebuilt=prepare_variants(reader,{**cfg,'_build_owned':bool(fork)},mapping,aliases,fallback)
                                variants.append(next((pair for pair in rebuilt if pair[0]==source),rebuilt[0]))
                                log_line(fn['name'] + ': inputs changed; retrying once against fresh destination state.')
                                continue
                            if not fork and measurement.get('helper_aliases'):
                                from .cpu_source_recovery import extend
                                extend(net,fresh,fn,variants,reader.origins,source,header,measurement,lead)
                            if not measurement.get('helper_aliases') and not fork and lead.get('family') in ('cpu-private-source-unit','cpu-source-predecessors','cpu-address-source-unit','cpu-helper-source-unit','cpu-helper-symbol-context') and str(exc).startswith('Prepared declarations emitted extra function bodies'):
                                # A valid real unit can fail the all-body guard under the wrong optimization.
                                # Try bounded installed profiles; do not weaken that guard to score a partial unit.
                                private_profiles(net,fresh,fn,source,header,st,report,log_line,min(budget,report['tested']+3),deadline,lead)
                            report['errors'].append(f'{fn["name"]}: {exc}')
                            log_line(report['errors'][-1])
                            if not str(exc).startswith(('The function, header or build inputs changed', 'The function claim changed')):
                                with net.store.tx() as db:
                                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                               (fn['id'], cache_key, 'error', str(exc)[:1200]))
                                    db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',
                                               (json.dumps(trial_feedback(fresh,fn,source,header,error=str(exc),measurement=measurement)),fn['id'],cache_key))
                            continue
                    if not fork and measurement:
                        st.source_measurement={'pair':(source,header),**measurement}
                        from .cpu_source_recovery import extend
                        extend(net,fresh,fn,variants,reader.origins,source,header,measurement,lead)
                    if score is not None and (fn['status']=='open' or score==0) and (fn['best_score'] is None or score < fn['best_score'] or fn['status']=='asm' and score==0):
                        improved_source = True
                        report['improved'] += 1
                        st.add(improved=1)
                        if score == 0:
                            report['matched'] += 1
                            st.add(matched=1)
                    if (not fork and score is not None and score != 0
                            and not pending(net,fn,state,cfg,variants,reader.origins,(source,header))):
                        private_profiles(net, fresh, fn, source, header, st, report, log_line, budget, deadline, lead)
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                   (fn['id'], cache_key, str(score), 'local source preparation'))
                        db.execute('UPDATE reuse_trials SET feedback=? WHERE function_id=? AND signature=?',
                                   (json.dumps(trial_feedback(fresh,fn,source,header,score,measurement=measurement)),fn['id'],cache_key))
            except (NetworkError, OSError, ValueError) as exc:
                message = f'{fn["name"]}: {exc}'
                report['errors'].append(message)
                log_line(message)
                # Compile failures are cached too, but a changed destination remains retryable.
                if cache_key and not str(exc).startswith(('The function, header or build inputs changed', 'The function claim changed')):
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                   (fn['id'], cache_key, 'error', str(exc)[:1200]))
            finally:
                st.holding(fn['name'], False)
                permuter._let_go(agent, fn)
            if improved_source and not fork and hasattr(net, '_comparison_digest'):
                followup(net, proj, fn, st, report, log_line, budget, deadline)
                if hasattr(net,'_candidate_checks') and net._function(fn['name'],proj['slug'])['status']=='matched':
                    from .export_source_recovery import run as repair_export
                    result=repair_export(net,proj,{fn['name']},st,log_line,
                                         budget=max(0,budget-report['tested']),deadline=deadline)
                    report['tested']+=result['tested']
    except (NetworkError, OSError, ValueError) as exc:
        report['errors'].append(str(exc))
        log_line(str(exc))
    for message in report['errors']:
        log.info('permuter preparation: %s', message)
    return report


def followup(net, proj, fn, st, report, log_line, budget, deadline):
    """New prepared context deserves bounded flags before another source mutation; no claim is held here."""
    from .matching_preflight import run as profiles
    remaining=budget-report['tested']
    if remaining<=0 or st.stop.is_set() or time.monotonic()>=deadline:return
    fresh=net.project_row(proj['slug'])
    current=net._function(fn['name'],proj['slug'])
    if current['status']!='open':return
    before=current['best_score']
    result=profiles(net,fresh,current,log_line,
                    lambda:not st.stop.is_set() and time.monotonic()<deadline,
                    max_profiles=min(2,remaining))
    report['tested']+=result['tested'];st.add(variants=result['tested'])
    after=net._function(fn['name'],proj['slug'])
    if before is not None and after['best_score'] is not None and after['best_score']<before:
        report['improved']+=1;st.add(improved=1)
        if after['status']=='matched':report['matched']+=1;st.add(matched=1)


def private_profiles(net, proj, fn, source, header, st, report, log_line, budget, deadline, lead=None):
    """Probe unpromoted context too; retain evidence without bypassing profile/context approval gates."""
    from .. import toolchains
    from ..project import compiler_for
    from .compiler_context import acceptable
    from .profile_hypotheses import captured, neighbours, prioritized
    if not hasattr(net, 'is_rom') or not net.is_rom(proj):return
    # An unsupported identity should be remapped before broad profile search. This
    # uses compiled evidence cached for this exact pair, never a source-name guess.
    from .source_context_diagnostics import identity
    from .source_lead_analysis import compare
    current_measurement=getattr(st,'source_measurement',{})
    if current_measurement.get('pair')==(source,header) and identity(compare(current_measurement.get('rows',[]),proj['arch'])):return
    cfg=json.loads(proj['config'])
    own=compiler_for(cfg,fn['vram'], fn)
    if own.get('cmd') or not own.get('toolchain') or toolchains.installed(own['toolchain']) is None:return
    expected=inputs(net,proj,fn)
    choices=[]
    for flags in captured(net,proj,own)+prioritized(proj,fn,own)+(neighbours(net,proj,fn,own) if hasattr(net,'cfg') else [])+net.flag_variants(own['toolchain'],list(own['flags'])):
        if list(flags)!=list(own['flags']) and list(flags) not in choices:choices.append(list(flags))
    from .source_next_steps import untested_profiles
    parent = hashlib.sha256(repr((source,header,expected)).encode()).hexdigest()
    def signature(flags, body=source, context=header):
        return 'local-source-profile-v3:'+hashlib.sha256(repr((expected,body,context,own['toolchain'],flags)).encode()).hexdigest()
    choices=untested_profiles(choices,own['flags'],signature,
        lambda key:net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key))
    trials = [(flags,source,header,0) for flags in choices]
    if hasattr(net.store,'all'):
        for row in net.store.all('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature LIKE ? ORDER BY rowid DESC LIMIT 128',fn['id'],'local-source-profile-v3:%'):
            saved = json.loads(row['feedback'] or '{}')
            retry = saved.get('cpu_retry')
            if retry and saved.get('cpu_parent') == parent:
                trials.insert(0,(saved['flags'],retry['source'],retry['header'],retry['depth']))
    while trials:
        flags,source,header,depth = trials.pop(0)
        if st.stop.is_set() or report['tested']>=budget or time.monotonic()>=deadline:return
        key=signature(flags,source,header)
        if net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?',fn['id'],key):continue
        private={**dict(proj),'config':json.dumps({**cfg,'compiler':{**own,'flags':flags},'compiler_ranges':[],'function_compilers':{}})}
        from .source_lead_analysis import destination, record
        scope=destination(net,proj,fn);started=time.monotonic()
        report['tested']+=1;st.add(variants=1)
        result=net._compile(private,header,source,fn)
        retry = None
        if result.ok and depth < 2 and dict(fn).get('target'):
            from .source_symbol_recovery import helpers
            from .source_symbol_recovery import variants as symbol_trials
            measurement = {'helper_aliases':helpers(net,private,fn,result.obj)}
            from .. import arch as arches
            recover = arches.get(proj['arch']).source_symbol_bindings
            if recover and not measurement['helper_aliases']:
                measurement['symbol_bindings'] = recover(result.obj,fn['name'],fn['target'],fn['vram'],net.symbols(proj['id']).resolve)
            alternatives = symbol_trials(source,header,measurement)
            if alternatives:
                _,body,context = alternatives[0]
                retry = {'source':body,'header':context,'depth':depth+1}
                trials.insert(0,(flags,body,context,depth+1))
        retry_evidence = {'cpu_parent':parent,'cpu_retry':retry,'toolchain':own['toolchain'],'flags':flags}
        if not result.ok or not acceptable(net,private,fn,result.obj):
            record(net,private,fn,source,header,None,[],{**(lead or {}),'family':'compiler-profile'},
                   (time.monotonic()-started)*1000,error=str(result.log),scope=scope)
            feedback={**retry_evidence,'score':None,'exact':False,
                      'category':'compile_or_extra_body','diagnostic':str(result.log)[:2000]}
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                           (fn['id'],key,'failed','private-context compile failure',json.dumps(feedback)))
            continue
        candidate,verdict,problems=net._evaluate(fn,proj,result.obj)
        if candidate is None:
            record(net,private,fn,source,header,None,[],{**(lead or {}),'family':'compiler-profile'},
                   (time.monotonic()-started)*1000,error='; '.join(problems),scope=scope)
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                           (fn['id'],key,'failed','private-context link failure',json.dumps({**retry_evidence,'category':'link_error','diagnostic':'; '.join(problems),'exact':False})))
            continue
        diff=scoring.diff(net._target_obj(fn,proj),candidate)
        exact=verdict is True
        score=0 if exact else max(1,diff.score)
        current=net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
        if inputs(net,net.project_row(proj['slug']),current)!=expected:return
        record(net,private,fn,source,header,score,diff.rows,{**(lead or {}),'family':'compiler-profile'},
               (time.monotonic()-started)*1000,scope=scope)
        evidence={**retry_evidence,'source':source,'header':header,
                  'score':score,'exact':exact,'problems':problems,'review_required':True}
        from .prepared_evidence import digest
        evidence['inputs']=digest(net,proj,fn)
        evidence['category']='exact' if exact else ('data' if diff.bytes_match else 'instructions')
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'],key,str(score),'private-context compiler probe; review required',json.dumps(evidence)))
        log_line(f"{fn['name']}: prepared context with {' '.join(flags)} scored {score}; private source/header/profile evidence saved for review. Working settings unchanged.")
        if exact:
            if settings(net,proj).get('auto_exact'):
                from .prepared_evidence import promote_measured
                agent=getattr(st,'preparation_agent',None)
                if agent is None:agent,_=net.local_agent('autopilot')
                if fn['status']!='open':return  # exact assembly evidence needs explicit queue approval
                if st.stop.is_set() or report['tested']>=budget or time.monotonic()>=deadline:return
                report['tested']+=1;st.add(variants=1)
                promote_measured(net,proj,fn,source,header,{'toolchain':own['toolchain'],'flags':flags},agent,expected)
                report['matched']+=1;report['improved']+=1;st.add(matched=1,improved=1)
                log_line(fn['name']+': exact private bundle reverified and saved automatically.')
            return


def trial_feedback(proj, fn, source, header, score=None, error='', measurement=None):
    """Precise local evidence so unchanged source/context/profile misses are not rediscovered."""
    from ..project import compiler_for
    own=compiler_for(json.loads(proj['config']),fn['vram'], fn)
    return {'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'header_sha256':hashlib.sha256(header.encode()).hexdigest(),
            'toolchain':own.get('toolchain'),'flags':own.get('flags',[]),'score':score,
            'category':('compile_or_link_error' if error else 'exact' if score==0 else 'scored_miss'),
            'diagnostic':error[:2000],'exact':score==0,'cpu':measurement or {}}
