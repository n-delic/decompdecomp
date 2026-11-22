"""Private cross-game trials using a donor file's declared IDO build settings."""

import json
import re
import shlex
import time
from pathlib import Path

from .. import arch, toolchains
from ..candidate_work import trial
from ..compiler import donor_declaration_cache
from ..network import NetworkError
from ..permuter_claims import nonexclusive
from . import (
    donor_relocations,
    donor_sources,
    donor_unit,
    exact_match_inputs,
    prepared_evidence,
    source_symbol_recovery,
)
from .compiler_context import acceptable, single_source

_ASSIGNMENT = re.compile(r'([A-Za-z_]\w*)\s*(:=|\?=|\+=|=)\s*(.*)')
_REFERENCE = re.compile(r'\$\(([A-Za-z_]\w*)\)')
_IDO = re.compile(r'(?:^|/)ido-static-recomp/build/(5\.3|7\.1)/out/cc$')
_NCC = re.compile(r'(?:^|/)ido-static-recomp/build/7\.1/out/NCC$')
_CC_RECIPE = re.compile(r'\$\(CC\) -c (.+) -o \$@ (?:build/(?:non_matching/)?)?\$<$')
_NCC_RECIPE = re.compile(r'\$\(NCC\) -c (.+) -o \$@ \$<$')
_FLAG = re.compile(r'-(?:O[0-3]|mips[1-4]|32|non_shared|Xcpluscomm|Wab,-r4300_mul|Wo,-zcopy:[01]|'
                   r'g[0-3]?|G\d+|D[A-Za-z_]\w*(?:=[\w.+-]+)?|U[A-Za-z_]\w*)$')
_DEFINE = re.compile(r'(?<!\S)(-[DU][A-Za-z_]\w*(?:=[\w.+-]+)?)(?!\S)')
_RULE_TARGET = re.compile(r'[A-Za-z0-9_./%-]+\.o$')
_FLAG_VARIABLES = ('CFLAGS', 'OPT_FLAGS', 'CC_ONLY_FLAGS', 'MIPSISET', 'MIPSBIT', 'CPPFLAGS')


class Settings:
    """Read literal Make declarations and exact object overrides without running a donor build."""

    def __init__(self, project):
        self.headers = donor_unit.PreparedSources()
        self.root = project.root
        self.variables = {}
        self.overrides = {}
        self.replacements = {}
        self.recipe_defines = {}
        self.cpp_recipes = {}
        self.c_recipes = {}
        path = project.root / 'Makefile'
        if not path.is_file() or not path.resolve().is_relative_to(project.root.resolve()) or path.stat().st_size > 2**20:
            return
        depth = 0
        recipe_targets = ()
        for line in path.read_text(encoding='utf-8', errors='replace').replace('\\\n', ' ').splitlines():
            if line.startswith('\t'):
                recipe = _NCC_RECIPE.fullmatch(line.strip()) if not depth else None
                if recipe:
                    for target in recipe_targets:
                        self.cpp_recipes[target] = recipe[1]
                recipe = _CC_RECIPE.fullmatch(line.strip()) if not depth else None
                if recipe:
                    for target in recipe_targets:
                        if '%' not in target:
                            self.c_recipes.setdefault(target, set()).add(recipe[1])
                if line.lstrip().startswith('$(CC) -c '):
                    defines = tuple(_DEFINE.findall(line))
                    for target in recipe_targets:
                        self.recipe_defines.setdefault(target, set()).add(defines)
                continue
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            target, colon, value = line.partition(':')
            expanded_target = self._expand(target, self.variables)
            recipe_targets = (tuple(name for name in expanded_target.split() if _RULE_TARGET.fullmatch(name))
                              if colon else ())
            if re.match(r'if(?:eq|neq|def|ndef)\b', line):
                depth += 1
                continue
            if line == 'endif':
                depth = max(0, depth - 1)
                continue
            if depth:
                continue
            assignment = _ASSIGNMENT.fullmatch(line)
            if assignment:
                self._assign(self.variables, assignment.groups())
                continue
            assignment = _ASSIGNMENT.fullmatch(value.strip().removeprefix('private ').lstrip())
            if colon and assignment and assignment[1] == 'REPLACE_FUNC_BODY':
                for name in recipe_targets:
                    entries = self.replacements.setdefault(name, {})
                    if assignment[2] in (':=', '='):
                        entries.clear()
                    entries.update(self._replacement_entries(assignment[3]))
            if colon and assignment and assignment[1] in ('CC', *_FLAG_VARIABLES):
                for name in recipe_targets:
                    self.overrides.setdefault(name, []).append(assignment.groups())

    def _replacement_entries(self, value):
        # Accept a common declarative shorthand for mapping a list of symbols to one object.
        group = re.fullmatch(r'\$\(foreach\s+(\w+),\$\((\w+)\),\$\(\1\)=(\$\(\w+\))\)', value)
        if group:
            symbols = self._expand(self.variables.get(group[2], ''), self.variables).split()
            return {symbol: group[3] for symbol in symbols if re.fullmatch(r'[A-Za-z_]\w*', symbol)}
        entries = {}
        for token in value.split():
            symbol, equals, obj = token.partition('=')
            if equals and re.fullmatch(r'[A-Za-z_]\w*', symbol):
                entries[symbol] = obj
        return entries

    @staticmethod
    def _assign(variables, assignment):
        name, op, value = assignment
        if op == '+=':
            variables[name] = (variables.get(name, '') + ' ' + value).strip()
        elif op != '?=' or name not in variables:
            variables[name] = value

    def for_unit(self, unit, *, prefer_recipe=True):
        if not unit.source or not unit.source.endswith('.c'):
            return None
        scratch = unit.scratch or {}
        declared = re.fullmatch(r'ido[- ]?(5\.3|7\.1)', str(scratch.get('compiler', '')), re.I)
        target = (unit.base.relative_to(self.root).as_posix()
                  if unit.base and unit.base.is_relative_to(self.root) else '')
        variables = self.variables.copy()
        for assignment in self.overrides.get(target, ()):
            self._assign(variables, assignment)

        exact = self._c_recipe_profile(target, variables) if prefer_recipe else None
        if exact:
            return exact

        groups = [self._expand(variables.get(group, ''), variables) for group in _FLAG_VARIABLES]
        recipe = self.recipe_defines.get(target)
        if recipe is None:
            recipes = [defines for pattern, defines in self.recipe_defines.items() if '%' in pattern
                       and re.fullmatch(re.escape(pattern).replace('%', '.+'), target)]
            recipe = set().union(*recipes) if recipes else set()
        literal = list(next(iter(recipe))) if len(recipe) == 1 else []
        if declared and isinstance(scratch.get('c_flags'), str):
            profile = self._profile(declared[1], [scratch['c_flags']])
            if profile is not None:
                defines = [*_DEFINE.findall(' '.join(groups)), *literal]
                profile['flags'].extend(defines)
            return profile
        match = _IDO.search(self._expand(variables.get('CC', ''), variables).strip())
        if not match or not target:
            return None
        return self._profile(match[1], [*groups, ' '.join(literal)])

    def fallback_for_unit(self, unit, primary):
        if not unit.base or not unit.base.is_relative_to(self.root):
            return None
        if unit.base.relative_to(self.root).as_posix() not in self.c_recipes:
            return None
        settings = self.for_unit(unit, prefer_recipe=False)
        return settings if settings != primary else None

    def _c_recipe_profile(self, target, variables):
        recipes = self.c_recipes.get(target, ())
        if len(recipes) != 1:
            return None
        match = _IDO.search(self._expand(variables.get('CC', ''), variables).strip())
        flags = self._expand(next(iter(recipes)), variables)
        if not match or not flags or '$' in flags:
            return None
        return self._profile(match[1], [flags])

    @staticmethod
    def _expand(value, variables):
        # Only expand single variable references. Bound the work even for repeated references.
        for _ in range(16):
            if len(value) > 8192:
                return ''
            expanded = _REFERENCE.sub(lambda match: variables.get(match[1], match[0]), value)
            if expanded == value:
                return value
            value = expanded
        return ''

    def replacement_source(self, unit, symbol):
        if not unit.base or not unit.base.is_relative_to(self.root):
            return None
        target = unit.base.relative_to(self.root).as_posix()
        artifact = self._expand(self.replacements.get(target, {}).get(symbol, ''), self.variables)
        match = re.fullmatch(
            r'build/(?:non_matching/)?(src/[A-Za-z_0-9./-]+\.(?:c|cpp))\.o(?:\.linked\.elf\.bound\.elf)?', artifact)
        if not match or '..' in Path(match[1]).parts:
            return None
        path = (self.root / match[1]).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            return None
        if path.suffix == '.c':
            target = artifact.removesuffix('.linked.elf.bound.elf')
            variables = self.variables.copy()
            for assignment in self.overrides.get(target, ()):
                self._assign(variables, assignment)
            return path, self._c_recipe_profile(target, variables)
        driver = self._expand(self.variables.get('NCC', ''), self.variables)
        recipe = self.cpp_recipes.get(artifact)
        if recipe is None:
            patterns = {value for pattern, value in self.cpp_recipes.items() if '%' in pattern
                        and re.fullmatch(re.escape(pattern).replace('%', '.+'), artifact)}
            if len(patterns) == 1:
                recipe = patterns.pop()
        if recipe is None or not _NCC.search(driver):
            return None
        flags = self._expand(recipe, self.variables)
        if not flags or '$' in flags:
            return None
        profile = self._profile('7.1-ncc', [flags])
        return (path, profile) if profile else None

    @staticmethod
    def _profile(version, groups):
        flags = []
        for group in groups:
            try:
                tokens = iter(shlex.split(group))
            except ValueError:
                return None
            for flag in tokens:
                if flag == '-I':
                    next(tokens, None)  # includes are flattened inside the checkout
                elif flag in ('-G', '-woff'):
                    argument = next(tokens, '')
                    if re.fullmatch(r'\d+(?:,\d+)*', argument):
                        flags.extend((flag, argument))
                elif _FLAG.fullmatch(flag):
                    flags.append(flag)
        return {'toolchain': 'ido-' + version, 'flags': flags}


def verify(net, proj, fn, other, donor, names, settings, agent, job, cache, *, replacement=None, adapt=None, compiled_literals=False):
    """Compile in private and retain this exact header and profile only with a fresh byte verdict."""

    if not replacement and (not settings or toolchains.installed(settings['toolchain']) is None):
        return 'skip', 'The donor file compiler is unavailable.'
    if not net.permuter._hold(agent, fn, count_turn=False, seconds=120):
        return 'skip', 'The function is being worked on.'
    def replay(current, trial, source, header):
        if adapt is not None:
            source = adapt(source)
            if source is None:
                return False, 'The donor has no unambiguous literal to transfer.'
        return _try(net, current, trial, fn, source, header, agent, job, compiled_literals=compiled_literals)

    def retry_declarations(current, trial, candidate, why):
        if job.stop.is_set() or "redeclaration of '" not in why:
            return False
        # A full translation unit can have conflicting declarations unrelated
        # to this entry. Keep only the entry and its declaration closure.
        source, header = candidate
        try:
            body, context = donor_sources.compact(header + '\n' + source, fn['name'])
        except NetworkError:
            return False
        accepted, _ = replay(current, trial, body, context)
        return accepted

    try:
        store = net.project_store(proj['id'])
        current = store.one('SELECT * FROM projects WHERE id=?', proj['id'])
        reason = 'Donor source did not reproduce the original bytes.'
        whole_reason = ''
        if replacement is not None:
            path, replacement_settings = replacement
            profiles = [replacement_settings or settings]
            if path.suffix == '.c' and settings and settings != profiles[0]:
                profiles.append(settings)
            for profile in profiles:
                if not profile or toolchains.installed(profile['toolchain']) is None or job.stop.is_set():
                    continue
                trial = {**dict(current), 'config': json.dumps(prepared_evidence.scoped_config(current, fn, profile))}
                try:
                    _, invocation = net._invocation(trial, fn)
                    lead = donor_unit.replacement(
                        other, donor, fn['name'], invocation, path, getattr(cache, 'replacements', None))
                    if lead:
                        accepted, why = replay(current, trial, *lead)
                        whole_reason = whole_reason or why
                        if accepted:
                            return 'match', 'Verified from the donor build’s replacement source.'
                except (NetworkError, OSError, ValueError):
                    pass
        if not settings or toolchains.installed(settings['toolchain']) is None:
            return 'skip', whole_reason or 'The donor file compiler is unavailable.'
        private = {**dict(current), 'config': json.dumps(prepared_evidence.scoped_config(current, fn, settings))}
        if proj['arch'] == 'mips':
            try:
                _, invocation = net._invocation(private, fn)
                whole = donor_unit.candidate(other, donor, {**names, donor['name']: fn['name']}, invocation, cache)
            except (NetworkError, OSError, ValueError):
                whole = None
            if whole:
                accepted, reason = replay(current, private, *whole)
                whole_reason = whole_reason or reason
                if accepted:
                    return 'match', 'Verified from a saved donor translation unit.'
                original = None
                original_reason = reason
                if (names and not job.stop.is_set()
                        and re.search(r'redeclar|redefin|conflicting (?:types?|declarations?)', reason, re.I)):
                    # The donor's names can collide when several declarations acquire
                    # the same destination spelling. Let emitted relocations guide the retry.
                    original = donor_unit.candidate(other, donor, {donor['name']: fn['name']}, invocation, cache)
                    if original and original != whole:
                        accepted, original_reason = replay(current, private, *original)
                        if accepted:
                            return 'match', 'Verified using compiled donor bindings.'
                if (retry_declarations(current, private, whole, reason)
                        or (original and original != whole
                            and retry_declarations(current, private, original, original_reason))):
                    return 'match', 'Verified after selecting the required declarations.'
        for body, header, _ in donor_sources.imported(net, other, donor, cache):
            if job.stop.is_set():
                return 'skip', 'Search stopped.'
            aliases = {**names, donor['name']: fn['name']}
            source = source_symbol_recovery.rename(body, aliases)
            header = source_symbol_recovery.rename(header, aliases)
            accepted, reason = replay(current, private, source, header)
            if accepted:
                return 'match', 'Verified with donor header and per-file compiler settings.'
        return 'miss', whole_reason or reason
    finally:
        net.permuter._let_go(agent, fn)


@trial
@donor_declaration_cache()
def _try(net, proj, private, fn, source, header, agent, job, *, compiled_literals=False):
    """Offer one reduced translation unit and one relocation-based retry when the object supports it."""
    reduced = rebound = localized = literal_changed = False
    evidence = exact_match_inputs.capture(net, private, fn, {'header': header}, source)
    while not job.stop.is_set():
        result = net._compile(private, header, source, fn)
        if not result.ok:
            lines = result.log.splitlines()
            detail = next((line for line in lines if 'error' in line.lower()), None)
            return False, detail or (result.log or 'Donor source did not build.').strip()[-300:]
        if not donor_unit.is_unit(header, proj) and not acceptable(net, proj, fn, result.obj):
            smaller = single_source(source, header, fn['name'], result.obj) if not reduced else None
            if smaller is None:
                return False, 'Donor context did not emit a standalone function.'
            source, header = smaller
            reduced = True
            continue
        candidate, exact, problems = net._evaluate(fn, proj, result.obj)
        if candidate is not None and exact is True:
            if job.stop.is_set():
                break
            def still_claimed():
                claim = net.project_store(proj['id']).one(
                    'SELECT agent_id,expires_at FROM claims WHERE function_id=?', fn['id'])
                if job.stop.is_set() or (not nonexclusive(agent) and
                        (not claim or claim['agent_id'] != agent.id or claim['expires_at'] <= time.time())):
                    raise NetworkError('The donor trial stopped or lost its claim before saving.')

            with net.progress.judging(proj['slug'], synchronize=False):
                net._record(fn, agent, 0, source, 0, matched=True, via='cross-game',
                            effective_settings=result.effective_settings,
                            exact_inputs={**evidence, 'source': source, 'header': header},
                            before_record=still_claimed)
            return True, ''
        if compiled_literals and not literal_changed and candidate is not None:
            from . import imported_literals

            changed = imported_literals.adapt_compiled(source, fn['name'], candidate, net._target_obj(fn, proj))
            if changed is not None:
                source = changed
                literal_changed = True
                continue
        variants = []
        if not rebound:
            if net.console(proj).export_overlay(net._rom_target(fn)) is not None:
                from . import overlay_donor_aliases

                expected = net._target_obj(fn, proj)
                def separate(aliases):
                    return donor_relocations.separate_overlay_references(net.console(proj), aliases)

                rebound_data = overlay_donor_aliases.offset_retry(
                    source, header, fn['name'], candidate, expected, separate=separate)
                aliases = source_symbol_recovery.relocation_aliases(candidate, expected)
                if rebound_data:
                    variants = [('overlay-data-offsets', *rebound_data)]
                elif not localized and (local := overlay_donor_aliases.local_functions(
                        source, header, fn['name'], result.obj, candidate, expected)):
                    variants = [('overlay-local-functions', *local)]
                elif aliases:
                    aliases = separate(aliases)
                    variants = [('overlay-symbols', source_symbol_recovery.rename(source, aliases),
                                 source_symbol_recovery.rename(header, aliases))]
                else:
                    def measure(probe, context, private=private, fn=fn, proj=proj):
                        built = net._compile(private, context, probe, fn)
                        return net._evaluate(fn, proj, built.obj)[0] if built.ok else None

                    rewritten = overlay_donor_aliases.retry(
                        source, header, fn['name'], candidate, expected, measure, job.stop)
                    if rewritten:
                        variants = [('overlay-call-sites', *rewritten)]
            else:
                recover = arch.get(proj['arch']).source_symbol_bindings
                bindings = (recover(result.obj, fn['name'], fn['target'], fn['vram'], net.symbols(proj['id']).resolve)
                            if recover else [])
                variants = source_symbol_recovery.variants(source, header, {'symbol_bindings': bindings})
        if not variants:
            return False, '; '.join(problems) or 'Donor source did not reproduce the original bytes.'
        family, source, header = variants[0]
        if family == 'overlay-local-functions':
            localized = True
        else:
            rebound = True
    return False, 'Search stopped.'
