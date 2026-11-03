"""Bounded real mapped compiler units; the judge, never this discovery, proves their layout."""
import re

from ..forks.csource import _blank, find, is_stub
from ..network import NetworkError
from .source_next_steps import called

MAX_BODIES = 4
MAX_SOURCE = 128 * 1024


def variants(reader, cfg, mapping, aliases, source, header):
    """Same authorized file/revision, actual call neighbours or up to three predecessors."""
    from .pattern_repairs import close
    from .source_conditions import select
    from .source_match_context import macro_context
    from .source_preparation import mappings, rename
    from .source_type_context import referenced
    if not cfg.get('_compiler_units'):return []
    _, values = select(header, reader.overrides, reader.overrides)
    original, _ = select(reader.file(reader.root / mapping[2]), values, reader.overrides)
    names = {symbol for _, symbol, _ in mappings(cfg.get('mappings', ''))}
    names.add(mapping[1])
    known = set(cfg.get('_known_functions', ()))
    bodies = {}
    for name in sorted(names):
        if aliases.get(name, name) not in known:continue
        span = find(original, name)
        if not span or is_stub(original[span[0]:span[1]]) or find(original[span[1]:], name):continue
        code = original[span[0]:span[1]]
        head = _blank(code).split('{', 1)[0]
        if ';' in head or len(code) > MAX_SOURCE:continue  # no invented K&R prototypes
        bodies[name] = span
    entry = mapping[1]
    if entry not in bodies:return []
    ordered = sorted(bodies, key=lambda name:bodies[name][0])
    edges = {n:called(original[a:b]) & bodies.keys() - {n} for n,(a,b) in bodies.items()}
    groups = []
    # Grow the actual caller/callee component, preserving original definition order.
    selected = {entry}
    for _ in range(MAX_BODIES - 1):
        neighbours = set().union(*(edges[n] for n in selected)) | {n for n in bodies if edges[n] & selected}
        available = neighbours - selected
        if not available:break
        selected.add(min(available, key=lambda n:abs(bodies[n][0] - bodies[entry][0])))
        groups.append(('cpu-private-source-unit', set(selected)))
    at = ordered.index(entry)
    for count in range(1, min(3, at) + 1):
        groups.append(('cpu-source-predecessors', set(ordered[at-count:at+1])))
    result = []
    for family, group in groups:
        text = ''; private = header
        for name in ordered:
            if name not in group:continue
            a,b = bodies[name];code = original[a:b]
            if name == entry:
                head = _blank(code).split('{', 1)[0]
                code = re.sub(r'\bstatic\b', lambda m:' ' * len(m[0]), code[:len(head)]) + code[len(head):]
            text += f'\n#line {original[:a].count(chr(10))+1} "reference.c"\n' + rename(code, aliases) + '\n'
        if len(text.encode()) > MAX_SOURCE:continue
        # Preserve selected file-scope objects from preparation, but not its singleton body.
        span = find(source, mapping[0])
        if not span:continue
        prefix = source[:span[0]]
        prefix = re.sub(r'(?m)^\s*#line[^\n]*', '', prefix)
        text = prefix + text + source[span[1]:]
        for name in group:
            dest = aliases.get(name, name)
            span = find(private, dest)
            if span:private = private[:span[0]] + '\n' * private[span[0]:span[1]].count('\n') + private[span[1]:]
            # Remove conflicting singleton prototypes, then restore the real declaration.
            clean = _blank(private);edits = []
            pattern = r'(?m)^[ \t]*(?:(?:extern|static|inline)\s+)?(?:[A-Za-z_]\w*[ \t*]+)+'+re.escape(dest)+r'\s*\('
            for hit in re.finditer(pattern, clean):
                end = close(clean, hit.end()-1)
                tail = re.match(r'\s*;', clean[end:]) if end else None
                if tail:edits.append((hit.start(), end+tail.end()))
            for a,b in reversed(edits):private = private[:a] + '\n'*private[a:b].count('\n') + private[b:]
        try:
            private += rename(referenced(original, text, private), aliases)
            reference = original
            if '#include' in original:
                reference, _ = select(reader.flatten(reader.root / mapping[2]), values, reader.overrides)
            private += macro_context(rename(reference, aliases), text, private)
        except NetworkError as error:
            reader.advisories.append('Compiler unit context unavailable: '+str(error));continue
        for name in ordered:
            if name in group:
                a,b = bodies[name];code=original[a:b];head = code[:_blank(code).index('{')]
                # A preceding static prototype controls linkage even when the definition
                # omits static. Preserve it rather than silently globalizing a private helper.
                prototypes=[];clean=_blank(original)
                pattern=r'(?m)^[ \t]*(?:(?:extern|static|inline)\s+)?(?:[A-Za-z_]\w*[ \t*]+)+'+re.escape(name)+r'\s*\('
                for hit in re.finditer(pattern,clean):
                    end=close(clean,hit.end()-1)
                    tail=re.match(r'\s*;',clean[end:]) if end else None
                    if tail:prototypes.append(original[hit.start():end+tail.end()].rstrip().removesuffix(';'))
                for declaration in prototypes or [head]:
                    if name == entry:declaration=re.sub(r'\bstatic\b','      ',declaration)
                    private += '\n' + rename(declaration, aliases) + ';\n'
        # Real unselected static inline helpers may disappear or leave a compiler-only
        # placeholder. Keep their actual bodies; any unknown emitted code still fails the judge.
        from .reference_context_recovery import static_helpers
        helper_reference=original
        for name in sorted(group,key=lambda n:bodies[n][0],reverse=True):
            a,b=bodies[name];helper_reference=helper_reference[:a]+'\n'*helper_reference[a:b].count('\n')+helper_reference[b:]
        helper_pairs=static_helpers(text,private,rename(helper_reference,aliases))
        if helper_pairs:private=helper_pairs[0][2]
        from .cpu_source_context import declarations as call_declarations
        from .source_match_context import macros
        actual=call_declarations(rename(reference,aliases),called(text))
        current=call_declarations(private,set(actual))
        for name,declaration in actual.items():
            if name not in current and name not in macros(private) and not find(text,name) and not find(private,name):
                private+='\n'+declaration+'\n'
        # Context bodies can reach file-scope tables that the singleton never used.
        # Carry only actual referenced definitions; the existing data judge owns placement.
        from . import source_definitions
        from .source_match_context import dependencies
        from .source_preparation import declarations
        used=dependencies(text,private)
        used |= {name for name,target in aliases.items() if target in used}
        definitions=[]
        for name,definition in source_definitions.related(declarations(original),used-set(group)):
            destination=aliases.get(name,name)
            try:source_definitions.extract(declarations(text),destination)
            except NetworkError:definitions.append(rename(source_definitions.externalize(definition),aliases))
        if definitions:text='\n'.join(definitions)+'\n'+text
        if len(private.encode()) > 1024*1024:continue
        pair = (family, text, private)
        if pair not in result:result.append(pair)
        if len(result) >= 6:break
    return result
