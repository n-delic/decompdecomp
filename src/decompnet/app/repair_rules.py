"""add_repair_rule: agents write the library's declarative repair rules (decomprepair.rules) to disk.

A rule is a JSON file (the format is in the library's rules.py and guide("repair-rules")): a C shape to find, what must
hold of the target, and what to write in its place. The library lists every valid rule file in the folder named by
`DECOMP_REPAIR_RULES` beside its own modules, so the draft pass, the beam and the Deep Permuter's local lane run an
agent's rule the next time they ask, with no database and no restart. This module:

- points that variable at `<home>/.decomp/repair-rules` when an app opens its home (`configure`); the Deep Permuter's
  worker is a subprocess and inherits it. A folder the user already named in the variable stays, after ours;
- checks a rule an agent submits, runs it on the function's current C, and saves it only when it makes at least one
  child there (a rule that finds nothing in the function it was written for is nearly always a wrong pattern, and a
  folder of such rules would only slow every pass), replacing the same agent id's earlier file so it can be corrected.

The rules shipped in the library keep their ids (a user file can't shadow them). Promoting a rule that proved itself is
copying its file into the library's `rules/`: the format is the same on both sides."""
import difflib
import json
import os
from pathlib import Path

from ..network import NetworkError
from . import agent_tokens

try:
    from decomprepair import rules
except ImportError:  # a library older than 0.5.1061 has no rules: the tool says so
    rules = None

SUBFOLDER = ('.decomp', 'repair-rules')
MAX_BYTES = 20_000
MAX_FILES = 200
MAX_SHOWN = 3
SHOWN_LINES = 8
_configured = ''   # the folder `configure` last added to the environment


def folder(home):
    return Path(home).joinpath(*SUBFOLDER)


def configure(home):
    """Put `home`'s rules folder first in DECOMP_REPAIR_RULES (it needn't exist yet) and leave the others as they were."""
    global _configured
    if rules is None:
        return None
    path = str(folder(home))
    kept = [item for item in os.environ.get(rules.ENVIRONMENT, '').split(os.pathsep) if item and item not in (path, _configured)]
    os.environ[rules.ENVIRONMENT] = os.pathsep.join([path, *kept])
    _configured = path
    return path


def _parse(rule):
    if isinstance(rule, dict):
        return rule
    if not isinstance(rule, str) or not rule.strip():
        raise NetworkError('Give the rule as a JSON object (a string or an object): guide("repair-rules") has the format.')
    if len(rule) > MAX_BYTES:
        raise NetworkError(f'A rule is at most {MAX_BYTES:,} characters.')
    try:
        return json.loads(rule)
    except ValueError as error:
        raise NetworkError(f'The rule is not valid JSON ({error}). Escape every backslash in a regular expression as \\\\.') from None


def _header(net, slug, proj, fn):
    if net.fork_slug(slug):
        return net.forks.permuter_header(slug, fn['name'])
    context = net._context(proj['id'], fn['unit'])
    return context['header'] if context else ''


def _shown(source, text):
    lines = [line for line in difflib.unified_diff(source.splitlines(), text.splitlines(), lineterm='', n=0)
             if not line.startswith(('---', '+++', '@@'))]
    more = f'\n  … {len(lines) - SHOWN_LINES} more changed lines' if len(lines) > SHOWN_LINES else ''
    return '\n'.join('  ' + line for line in lines[:SHOWN_LINES]) + more


def run(net, agent, slug, function, rule):
    if rules is None:
        raise NetworkError('This copy of the app has a repair library without rules (it needs decomprepair 0.5.1061 or '
                           'newer). Update the app, then add the rule again.')
    parsed = _parse(rule)
    found = rules.problems(parsed)
    if found:
        raise NetworkError('That is not a valid repair rule, nothing was saved: ' + '; '.join(found)
                           + '. guide("repair-rules") has the format and a worked example.')
    compiled = rules.compile_rule(parsed)
    shipped = {path.stem for path in rules.PACKAGED.glob('*.json')}
    if compiled.id in shipped:
        raise NetworkError(f'"{compiled.id}" is a rule shipped with the library; pick another id.')
    fn = net._function(function, slug)
    proj = net._project(slug)
    if compiled.archs and proj['arch'] not in compiled.archs:
        raise NetworkError(f'The rule is for {", ".join(compiled.archs)}; {function} is {proj["arch"]}. '
                           'Remove "archs" or give the rule the right one.')
    source, _ = agent_tokens.candidate(net, agent, slug, function)
    if not source:
        raise NetworkError(f'{function} has no C yet to try the rule on: draft or compile something first.')
    children = rules.apply(compiled, source, fn['name'], fn['asm'] or '', _header(net, slug, proj, fn))
    if not children:
        raise NetworkError(
            f'The rule is valid but makes nothing on {function}\'s current C, so it was not saved. Compare its "match" with '
            f'the text in get_candidate (the match runs on the function body with comments and strings blanked, '
            f'`re.M`), check its "gate" against get_assembly, then add it again.')
    path = folder(net.workspace) / f'{compiled.id}.json'
    existing = list(path.parent.glob('*.json')) if path.parent.is_dir() else []
    if path not in existing and len(existing) >= MAX_FILES:
        raise NetworkError(f'The rules folder already holds {MAX_FILES} rules. Remove the ones that never help first.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(parsed, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    configure(net.workspace)
    reason = rules.skipped().get(str(path))
    if reason:   # the library refused the file after all (an id another folder's rule already has)
        path.unlink(missing_ok=True)
        raise NetworkError(f'The library did not accept the rule, nothing was saved: {reason}')
    shown = []
    for family, text in children[:MAX_SHOWN]:
        shown.append(f'- {family}:\n{_shown(source, text)}')
    more = f'\n(and {len(children) - MAX_SHOWN} more children)' if len(children) > MAX_SHOWN else ''
    return (f'Saved repair rule "{compiled.id}" ({", ".join(compiled.families)}). On {function}\'s current C it makes '
            f'{len(children)} child{"" if len(children) == 1 else "ren"}:\n' + '\n'.join(shown) + more + '\n\n'
            'The draft pass, the beam and the Deep Permuter run it from now on, on every function of this game '
            '(a rule is judged like any other repair: it can only propose C). Try its children now with '
            'compile_variants. Its family names are not in try_local_repairs/prove_chain\'s lists until the app restarts. '
            'Writing the same id again replaces the rule.')

