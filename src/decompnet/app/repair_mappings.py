"""Donor provenance for candidate-local repairs, never inferred from a comment alone."""

import hashlib
import json
import re


def digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def stamp(fn, donor=False):
    return digest(json.dumps([(fn['target'] or b'').hex(), fn['relocs'], fn['vram'],
                              fn['matched_source'] if donor else None], sort_keys=True))


def remember(net, a, b, source, names):
    fid = a['function_id'] if 'function_id' in a.keys() else a['id']
    current = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", fid)
    if current is None or ('matched_source' in a.keys() and current['matched_source'] != a['matched_source']):
        return
    a = current
    if a['project_id'] == b['project_id'] or a['status'] != 'matched':
        return
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO repair_origins VALUES(?,?,?,?,?,?)',
                   (b['id'], digest(source), a['id'], stamp(a, True), stamp(b), json.dumps(names, sort_keys=True)))


def origins(net, fn, source):
    rows = net.store.all('SELECT * FROM repair_origins WHERE function_id=? AND source_hash=?', fn['id'], digest(source))
    valid = []
    for row in rows:
        a = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", row['source_function_id'])
        if a and a['project_id'] != fn['project_id'] and stamp(a, True) == row['source_stamp'] and stamp(fn) == row['target_stamp']:
            valid.append((row, a))
    # Ambiguous provenance must not teach a particular donor's regional mapping.
    return valid if len(valid) == 1 else []


def recover(twins, proj, fn, source):
    """Backfill old misses only by regenerating the donor's actual adapted C and comparing it exactly."""
    from .similar_matches import literal_variants, relocations
    from .twins import _declared, apply, rom_renames

    net = twins.net
    if origins(net, fn, source):
        return
    # The app's explanatory prefix is not evidence; the donor must occur in the recorded transfer history
    # and regenerate the same C. Restrict this compatibility path to its precise generated prefix.
    prefix = r'/\* (?:identical to|adapted from similar verified C in) [^\n]* with its names changed by decompdecomp \*/\n'
    bare = re.sub('^' + prefix, '', source, count=1)
    if bare == source:
        return
    donors = net.store.all('SELECT DISTINCT f.* FROM cross_game_attempts x JOIN functions f ON f.id=x.source_function_id '
                          "WHERE x.function_id=? AND f.status='matched' AND f.project_id!=? ORDER BY f.id LIMIT 16",
                          fn['id'], proj['id'])
    header = net._context(proj['id'], fn['unit'])['header']
    target_print = twins.prints(proj).get(fn['id'])
    if target_print is None:
        return
    for a in donors:
        other = net.store.one('SELECT * FROM projects WHERE id=?', a['project_id'])
        if not other or not net.is_rom(other) or (other['arch'], other['big_endian']) != (proj['arch'], proj['big_endian']):
            continue
        if [(o,t) for o,t,_ in relocations(a)] != [(o,t) for o,t,_ in relocations(fn)]:
            continue
        donor_print = twins.prints(other).get(a['id'])
        if donor_print is None:
            continue
        names = rom_renames(net.symbols(other['id']), a['matched_source'] or '', donor_print, target_print,
                            a['name'], fn['name'], net.symbols(proj['id']))
        if names is None:
            continue
        words = set(re.findall(r'\b[A-Za-z_]\w*\b', a['matched_source'] or ''))
        names = {old:new for old,new in names.items() if old in words}
        prelude = _declared(net._context(other['id'])['header'], header, names, a['name'])
        body = apply(a['matched_source'] or '', names)
        if bare in [prelude + v for v in [body, *literal_variants(body, a, fn, bool(proj['big_endian']))]]:
            remember(net, a, fn, source, names)


def propagate(net, fn, seed, candidate):
    """Keep provenance through identifier-preserving local edits; refuse ambiguous token rewrites."""
    from .similar_matches import TOKENS

    evidence = origins(net, fn, seed)
    if not evidence:
        return
    identifiers = lambda text: [t.group() for t in TOKENS.finditer(text) if re.fullmatch(r'[A-Za-z_]\w*', t.group())]
    left, right = identifiers(seed), identifiers(candidate)
    if len(left) != len(right):
        return
    changes = {}
    for old,new in zip(left,right):
        changes.setdefault(old,set()).add(new)
    if any(len(v) != 1 for v in changes.values()):
        return
    changes = {old:next(iter(v)) for old,v in changes.items()}
    row,a = evidence[0]
    bindings = json.loads(row['names'])
    bindings = {old:changes.get(current,current) for old,current in bindings.items()}
    words = set(identifiers(a['matched_source'] or ''))
    for old,new in changes.items():
        if old != new and old in words and old not in bindings:
            bindings[old] = new
    remember(net, a, fn, candidate, bindings)


def accepted(net, fn, seed):
    for row, a in origins(net, fn, seed):
        b = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", fn['id'])
        if b is None or stamp(b) != row['target_stamp']:
            continue
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repaired_pairs VALUES(?,?,?,?)',
                       (b['id'], a['id'], row['source_stamp'], row['target_stamp']))


def pairs(net, proj, other):
    rows = net.store.all('SELECT r.* FROM repaired_pairs r JOIN functions a ON a.id=r.source_function_id '
                         'JOIN functions b ON b.id=r.function_id WHERE a.project_id=? AND b.project_id=?', other['id'], proj['id'])
    valid = []
    for row in rows:
        a = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", row['source_function_id'])
        b = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", row['function_id'])
        if a and b and stamp(a, True) == row['source_stamp'] and stamp(b) == row['target_stamp']:
            valid.append(row)
    return valid


def source_symbols(syms, source):
    """Automatic C identifiers may name exact addresses absent from the splitter's label table."""
    from ..rom.symbols import AUTO_NAME, Symbol, SymbolTable
    from .similar_matches import TOKENS

    extra = []
    for token in TOKENS.finditer(source):
        m = AUTO_NAME.fullmatch(token.group())
        if m and token.group() not in syms.by_name:
            extra.append(Symbol(token.group(), int(m[1],16)))
    return SymbolTable([*syms.by_name.values(), *extra]) if extra else syms


def variant(twins, proj, fn, source):
    from .similar_matches import TOKENS, regional_names

    evidence = origins(twins.net, fn, source)
    if not evidence:
        return None
    row, a = evidence[0]
    donor = twins.net.store.one('SELECT * FROM projects WHERE id=?', a['project_id'])
    learned = regional_names(twins, proj, donor)
    bindings = json.loads(row['names'])
    votes = {}
    for old, new in learned.items():
        votes.setdefault(bindings.get(old, old), set()).add(new)
    names = {old:next(iter(values)) for old,values in votes.items() if len(values)==1}
    result = TOKENS.sub(lambda token: names.get(token.group(), token.group()), source)
    return result if result != source else None


def symbol_only_change(before, after):
    """Only identifiers may differ; changed literals, operators or definitions cannot teach this shortcut."""
    from .similar_matches import TOKENS
    def tokens(text):
        return [m.group() for m in TOKENS.finditer(text)
                if not m.group().startswith(('/*', '//')) and not m.group().isspace()]
    left, right = tokens(before), tokens(after)
    if len(left) != len(right):
        return False
    return all(a == b or (re.fullmatch(r'[A-Za-z_]\w*', a) and re.fullmatch(r'[A-Za-z_]\w*', b))
               for a, b in zip(left, right))


def harvest(twins, proj, function_id):
    """A normally verified agent success can complete an existing, unambiguous donor transfer.

    Comments alone never identify a donor. Reuse the same origin/target stamps and independent-pair
    requirements as automatic repairs; changed or reopened donors cannot supply mappings.
    """
    net = twins.net
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        return
    fn = net.store.one("SELECT * FROM functions WHERE id=? AND status='matched'", function_id)
    if not fn or not fn['matched_source']:
        return
    source = fn['matched_source']
    if origins(net, fn, source):
        accepted(net, fn, source)
        return
    if not net.store.one('SELECT 1 FROM cross_game_attempts WHERE function_id=? LIMIT 1', function_id):
        return
    seeds = net.store.all('SELECT source FROM attempts WHERE function_id=? AND score>0 AND compiled=1 '
                         'ORDER BY id DESC LIMIT 8', function_id)
    for row in seeds:
        seed = row['source'] or ''
        if not symbol_only_change(seed, source):
            continue
        from .similar_matches import TOKENS
        syms = net.symbols(proj['id'])
        words = lambda text: [m.group() for m in TOKENS.finditer(text)
                              if not m.group().startswith(('/*', '//')) and not m.group().isspace()]
        changes = [(a,b) for a,b in zip(words(seed),words(source)) if a != b]
        if not changes or any(syms.resolve(a) is None or syms.resolve(b) is None for a,b in changes):
            continue
        if not origins(net, fn, seed):
            recover(twins, proj, fn, seed)
        if origins(net, fn, seed):
            propagate(net, fn, seed, source)
            accepted(net, fn, source)
            return
