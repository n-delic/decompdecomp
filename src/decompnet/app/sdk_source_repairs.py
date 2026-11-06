"""Candidate-local SDK declarations: linked ROM data already exists at its original address."""

import json
import re
from io import BytesIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ..forks.csource import _blank


def externalize(source, obj):
    """Replace simple public, file-scope object definitions with extern declarations.

    Whole SDK translation units can emit unrelated pointer initializers whose symbols the destination
    does not know. Leave statics, complex initializers and function-local declarations alone. This only
    proposes C: the ordinary compile/link judge must still verify every referenced address and byte.
    """
    try:
        table = ELFFile(BytesIO(obj)).get_section_by_name('.symtab')
        names = {s.name for s in table.iter_symbols() if s['st_info']['type'] == 'STT_OBJECT'
                 and s['st_info']['bind'] == 'STB_GLOBAL' and s['st_shndx'] != 'SHN_UNDEF'} if table else set()
    except (ELFError, ValueError):
        return source
    lines, depth = [], 0
    for line, blank in zip(source.splitlines(True), _blank(source).splitlines(True)):
        # Only complete, simple one-line declarations at file scope. Never rewrite a macro or a body.
        simple = (depth == 0 and not blank.lstrip().startswith('#')
                  and not re.match(r'\s*(?:extern|static|typedef)\b', blank)
                  and not any(c in blank for c in '{}()'))
        if simple:
            for name in sorted(names):
                match = re.fullmatch(r'(\s*[^;=\n]+\b' + re.escape(name) +
                                     r'(?:\s*\[[^\]\n]*\])*)\s*(?:=[^;\n]*)?;\s*', blank)
                if match:
                    # Multiple declarators need their own parser; do not accidentally change their storage.
                    if ',' not in match[1]:
                        line = 'extern ' + match[1].strip() + ';\n'
                    break
        lines.append(line)
        depth += blank.count('{') - blank.count('}')
    return ''.join(lines)


def _variant(net, proj, fn, source, target, job):
    """Bound two compiler probes, then map only identical relocated instruction layouts."""
    from .free_candidates import exhausted
    from .twins import Print, apply, object_prints, renames

    context = net._context(proj['id'], fn['unit'])['header']
    if exhausted(job):
        return None
    job.reuse_attempts += 1
    compiled = net._compile(proj, context, source, fn)
    if not compiled.ok:
        return None
    proposal = externalize(source, compiled.obj)
    if proposal == source or exhausted(job):
        return None
    job.reuse_attempts += 1
    compiled = net._compile(proj, context, proposal, fn)
    if not compiled.ok:
        return None
    try:
        fingerprint = object_prints(compiled.obj).get(fn['name'])
    except (ELFError, ValueError):
        return None
    if not fingerprint or fingerprint.key != target.key:
        return None
    # A label for the middle of a larger data object is not a C identifier. Use its exact ROM address.
    refs = [f'D_{address:08X}' if '+' in name else name for name, address in zip(target.refs, target.addrs)]
    if len(refs) != len(target.refs):
        return None
    names = renames(fingerprint, Print(target.key, refs, target.size, target.addrs), fn['name'], fn['name'])
    return apply(proposal, names) if names is not None else None


def variant(net, proj, fn, source, target, job):
    """Cache proposal probes as well as judge verdicts, invalidating on context or symbol changes."""
    from ..project import compiler_for
    from .free_candidates import exhausted
    from .similar_matches import trial_key

    if exhausted(job):
        return None
    context = net._context(proj['id'], fn['unit'])['header']
    settings = compiler_for(net.cfg(proj), fn['vram'], fn)
    symbols = [(r['name'], r['address']) for r in net.store.all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    key = trial_key(source, context, settings, fn, ['sdk-data-proposal-v1', symbols, target.refs, target.addrs])
    cached = net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
    if cached:
        return json.loads(cached['feedback']).get('source')
    proposal = _variant(net, proj, fn, source, target, job)
    # Cancellation or an exhausted budget must not permanently suppress a probe that did not finish.
    if not exhausted(job):
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'], key, 'proposal', 'SDK data reference probe', json.dumps({'source': proposal})))
    return proposal
