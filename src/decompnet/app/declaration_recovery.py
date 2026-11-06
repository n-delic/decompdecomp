"""Recover only unambiguous existing declarations; never guess a global's type from its address."""
import re

from ..forks.csource import _blank

MISSING = re.compile(r"[‘'`]([A-Za-z_]\w*)[’'`] (?:undeclared|was not declared)|unknown type name [‘'`]([A-Za-z_]\w*)[’'`]")
DECL = re.compile(r'^\s*(?:extern\s+[^;{}#]+|typedef\s+[^;{}#]+);\s*$', re.M)


def recover(source, error, known):
    missing = {a or b for a, b in MISSING.findall(error)}
    if not missing or len(missing) > 12:
        return None
    selected = []
    for name in sorted(missing):
        definitions = {m[0].strip() for text in known for m in DECL.finditer(text)
                       if re.search(r'\b'+re.escape(name)+r'\s*(?:\[[^;]*\])?\s*[;(]', _blank(m[0]))}
        # Conflicting historical declarations require an agent; don't pick the newest arbitrary type.
        if len(definitions) != 1:
            return None
        declaration = next(iter(definitions))
        if declaration not in source:
            selected.append(declaration)
    return '\n'.join(sorted(dict.fromkeys(selected), key=lambda line: not line.startswith('typedef'))) + '\n'+source if selected else None


def preflight(net, agent, slug, fn, source):
    from . import agent_evidence
    report = agent_evidence.preflight(net, agent, slug, fn, source)
    if not report or report.get('score') is not None:
        return source, report
    known = [r['source'] for r in net.store.all('SELECT source FROM attempts WHERE function_id=? AND score IS NOT NULL ORDER BY id DESC LIMIT 8', fn['id']) if r['source']]
    proj = net.project_row(slug)
    if not net.fork_slug(slug):
        context = net._context(proj['id'], fn['unit'])
        if context:
            known.append(context['header'])
    repaired = recover(source, report.get('error', ''), known)
    if not repaired:
        return source, report
    verified = agent_evidence.preflight(net, agent, slug, fn, repaired)
    if not verified or verified.get('score') is None:
        return source, report
    return repaired, dict(verified, declaration_recovery=True)
