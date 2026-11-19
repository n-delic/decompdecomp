"""Several candidates of one function compiled in one compiler run, each judged from its own copy of the object.

A candidate's compile is mostly the cost of starting the compiler's programs (an old GCC: cpp, cc1, as): eight
candidates in one run took 1.4 ms each instead of 9.8 ms, and each function's bytes were the ones it gets alone, as
old compilers compile a file one function at a time. Each candidate is renamed apart (`name__b3`) and starts with
`#line 1 "func.c"`, so line numbers, and macros that read them, are what it sees alone. Its copy of the object has
its own function renamed back, so the judge links and scores it exactly as it would a single compile.

What could differ is never taken from a batch: candidates share a run only when their declarations above the
function compile the same (written once); a definition with a preprocessor line compiles alone, and so does a
function whose code refers to anything local to the file (constants, strings, data, static helpers), since its
place there differs from a file of its own. A batch that doesn't compile is compiled one by one. The deep search
compiles an improvement alone again before keeping it (permute._score_work).
"""
from __future__ import annotations

import io
import re

from . import compile_sessions, compiler

MAX = 8  # candidates per run: most of the saving, and a failing candidate costs the others little


def _definition(source: str, name: str):
    """(everything before the definition of `name`, the definition and what follows, the line it starts on), or
    None: the first line naming `name(` whose text reaches a `{` before a `;` (a prototype ends at `;`)."""
    lines = source.split('\n')
    pattern = re.compile(rf'\b{re.escape(name)}\s*\(')
    for at, line in enumerate(lines):
        if pattern.search(line) and not line.lstrip().startswith(('#', '//', '/*', '*')):
            rest = '\n'.join(lines[at:])
            brace, semicolon = rest.find('{'), rest.find(';')
            if brace != -1 and (semicolon == -1 or brace < semicolon):
                return '\n'.join(lines[:at]), rest, at
    return None


def _code(prefix: str) -> tuple:
    """What a prefix compiles as: its lines without comments or blank lines (a draft's provenance comment)."""
    return tuple(line.strip() for line in prefix.splitlines()
                 if line.strip() and not line.lstrip().startswith(('//', '/*', '*')))


def eligible(body: str, name: str) -> bool:
    if '__LINE__' in body or '__COUNTER__' in body or not re.search(rf'\b{re.escape(name)}\b', body):
        return False
    return not any(line.lstrip().startswith('#') for line in body.splitlines())


def _renamed(name: str, index: int) -> str:
    return f'{name}__b{index}'


def compile_batch(cfg: compiler.CompilerConfig, header: str, sources: list[str], name: str):
    """[CompileResult or None] for `sources`, None for one that must compile alone; None for all when nothing can
    be batched (no compile session, C++, no two candidates with the same declarations)."""
    session = compile_sessions.current()
    if session is None or cfg.cplusplus or len(sources) < 2:
        return None
    # Candidates share a run only with others whose declarations above the function compile the same (siblings
    # may differ there on purpose: a global's type). That prefix is written once; each definition then starts at
    # the line it has in a file of its own, so line numbers are the same as alone.
    groups = {}
    for index, source in enumerate(sources):
        found = _definition(source, name)
        if found is None or not eligible(found[1], name):
            continue
        groups.setdefault(_code(found[0]), []).append((index, found))
    results = [None] * len(sources)
    runs = [members for members in groups.values() if len(members) > 1]
    if not runs:
        compiler.compile_timing.add('batch_refused', 1)
        return None
    work = session.prepare(cfg, header)
    if work is None:
        return None
    pattern = re.compile(rf'\b{re.escape(name)}\b')
    root = compiler._root(cfg, work)
    for members in runs:
        for attempt in range(2):
            prefix = members[0][1][0]
            parts = [prefix] if prefix else []
            for slot, (index, (_, body, line)) in enumerate(members):
                parts.append(f'#line {line + 1} "func.c"')
                parts.append(pattern.sub(_renamed(name, slot), body))
            compiler.compile_timing.add('requests', 1)
            compiler.compile_timing.add('batched_candidates', len(members))
            result = compiler._compile_as(cfg, work, root, '\n'.join(parts), 'c')
            if result.ok and result.obj:
                for (index, _), candidate in zip(members, _split(result, [sources[i] for i, _ in members], name)):
                    results[index] = candidate
                break
            compiler.compile_timing.add('batch_failed', 1)
            # One candidate that doesn't compile fails its run: GCC names the function it complains about, so the
            # rest run once more without it, and it compiles alone (reporting its own error, as ever).
            failing = {int(m) for m in re.findall(rf"In function `{re.escape(name)}__b(\d+)'", result.log or '')}
            members = [member for slot, member in enumerate(members) if slot not in failing]
            if not failing or len(members) < 2:
                break
    return results


def _split(result, sources, name):
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection

    from .inline_helpers import check as check_inline_helpers
    data = result.obj
    elf = ELFFile(io.BytesIO(data))
    symtab = elf.get_section_by_name('.symtab')
    if symtab is None:
        return None
    strtab = elf.get_section(symtab['sh_link'])
    symbols = list(symtab.iter_symbols())
    if any(s.name == name and s['st_shndx'] != 'SHN_UNDEF' for s in symbols):
        return None  # (the name already defined: renaming back would make two)
    found = {s.name: s for s in symbols if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    relocations = {}
    for section in elf.iter_sections():
        if isinstance(section, RelocationSection):
            relocations.setdefault(section['sh_info'], []).append(section)
    results = []
    for index, source in enumerate(sources):
        symbol = found.get(_renamed(name, index))
        if symbol is None or not symbol['st_size']:
            compiler.compile_timing.add('batch_alone_missing', 1)
            results.append(None)
            continue
        start, end = symbol['st_value'], symbol['st_value'] + symbol['st_size']
        local = False
        for section in relocations.get(symbol['st_shndx'], ()):
            refs = elf.get_section(section['sh_link'])
            for rel in section.iter_relocations():
                if start <= rel['r_offset'] < end:
                    target = refs.get_symbol(rel['r_info_sym'])
                    if (target['st_info']['bind'] == 'STB_LOCAL' or target['st_info']['type'] == 'STT_SECTION'):
                        local = True  # its constants, strings or helpers sit elsewhere in a file of its own
                        break
            if local:
                break
        if local:
            compiler.compile_timing.add('batch_alone_local', 1)
            results.append(None)
            continue
        copy = bytearray(data)
        at = strtab['sh_offset'] + symbol['st_name']
        copy[at:at + len(name) + 1] = name.encode() + b'\0'
        candidate = compiler.CompileResult(True, bytes(copy), result.log, 'c')
        if reason := check_inline_helpers(source, candidate.obj):
            candidate = compiler.CompileResult(False, None, reason)
        candidate.batched = True
        results.append(candidate)
    return results
