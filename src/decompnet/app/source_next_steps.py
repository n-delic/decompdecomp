"""Explain reference/draft differences and helper declarations without inventing retail source edits."""
import re

from ..forks.csource import _blank

IDENT = r'[A-Za-z_]\w*'
CONTROL = {'if', 'for', 'while', 'switch', 'sizeof', 'return', 'defined'}


def called(source):
    return set(re.findall(r'\b(' + IDENT + r')\s*\(', _blank(source))) - CONTROL


def signatures(text, names):
    """ANSI declarations only; complex/K&R signatures need human review, not an inferred ABI."""
    clean = _blank(text)
    result = {}
    for name in names:
        pattern = r'(?:^|[;{}\n])[ \t]*((?:(?:extern|static|inline)[ \t]+)?(?:' + IDENT + r'[ \t*]+)+)' + re.escape(name) + r'\s*\(([^(){};]*)\)\s*(?:;|\{)'
        found = list(re.finditer(pattern, clean))
        if found:
            match = found[-1]
            params = []
            for parameter in match[2].split(','):
                parameter = parameter.strip()
                # Names do not affect an ANSI scalar/pointer signature. Keep typedef-only
                # parameters and unknown syntax intact rather than guessing their types.
                named = re.fullmatch(r'(.+?[\s*])(' + IDENT + r')', parameter)
                if named and named[1].strip() not in {'const','volatile','struct','union','enum'} and named[2] not in {'int','long','short','char','float','double','void'}:
                    parameter = named[1].strip()
                params.append(parameter)
            result[name] = ' '.join(re.sub(r'\b(?:extern|static|inline)\b', '', match[1]).split()) + ' (' + ', '.join(params) + ')'
    return result


def prototype_hints(original, source, header):
    """Compare only explicit declarations. A disagreement is a review lead, never a replacement."""
    names = called(source)
    reference = signatures(original, names)
    prepared = signatures(header, names)
    return [f'Helper {name}: reference declares {reference[name]}, prepared context declares {prepared[name]}. Check return signedness, float/integer arguments and original callers before body permutation; declarations were not changed.'
            for name in sorted(reference.keys() & prepared.keys()) if reference[name] != prepared[name]][:8]


def recommendations(source, saved, comparison):
    """Retain a better draft and highlight concrete call/control discrepancies for version review."""
    result = []
    if saved:
        added = sorted(called(source) - called(saved))
        absent = sorted(called(saved) - called(source))
        if added or absent:
            result.append('Reference versus saved draft calls: reference-only ' + (', '.join(added[:12]) or 'none') +
                          '; saved-only ' + (', '.join(absent[:12]) or 'none') +
                          '. Possible SDK/retail revision difference; preserve the better draft and inspect target calls before replaying older source.')
    if saved:
        def tests(text):
            return set(' '.join(m[0].split()) for m in re.finditer(r'\b'+IDENT+r'(?:\s*(?:->|\.)\s*'+IDENT+r')*\s*(?:==|!=|<=|>=|<|>)\s*-?(?:0[xX][0-9a-fA-F]+|\d+)\b',_blank(text)))
        reference_tests,saved_tests=tests(source),tests(saved)
        if reference_tests!=saved_tests:
            result.append('Source revision review: reference-only comparisons '+(', '.join(sorted(reference_tests-saved_tests)[:6]) or 'none')+
                          '; saved-only '+(', '.join(sorted(saved_tests-reference_tests)[:6]) or 'none')+
                          '. Check these constants and branch conditions against retail instructions; source differences alone do not authorize a patch.')
    missing = comparison.get('missing_calls', {})
    extra = comparison.get('extra_calls', {})
    target = comparison.get('target', {})
    candidate = comparison.get('candidate', {})
    if missing or extra or target.get('branches') != candidate.get('branches') or target.get('exits') != candidate.get('exits'):
        result.append('Compiled call/control mismatch: inspect missing/extra calls and branch/exit rows. Separate a version change from inlining, compiler flags or ABI effects before proposing a source edit. No branches were patched.')
    target_constants=set(map(tuple,target.get('immediates',[])));candidate_constants=set(map(tuple,candidate.get('immediates',[])))
    if target_constants!=candidate_constants:
        result.append('Compiled immediate differences: target-only '+str(sorted(target_constants-candidate_constants)[:8])+
                      '; candidate-only '+str(sorted(candidate_constants-target_constants)[:8])+
                      '. Relate these instructions to retail comparisons/call arguments before changing archived source; immediates alone do not identify semantics.')
    if comparison.get('register_only'):
        result.append('Register-only miss: check explicit helper return and argument types (especially float zero arguments), original declaration order/lifetimes and caller context before broad expression permutation.')
    return result


def untested_profiles(choices, current, signature, seen, limit=2):
    """Budget new measurements, not already cached choices; keep every input in the caller's key."""
    result = []
    unique = set()
    for flags in choices:
        flags = list(flags)
        key = tuple(flags)
        if flags == list(current) or key in unique:
            continue
        unique.add(key)
        if seen(signature(flags)):
            continue
        result.append(flags)
        if len(result) >= limit:
            break
    return result
