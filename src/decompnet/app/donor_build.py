"""Private cross-game trials using a donor file's declared IDO build settings."""

import json
import re
import shlex
import time
from pathlib import Path

from .. import arch, toolchains
from ..network import NetworkError
from ..permuter_claims import nonexclusive
from . import donor_sources, donor_unit, exact_match_inputs, prepared_evidence, source_symbol_recovery
from .compiler_context import acceptable, single_source

_ASSIGNMENT = re.compile(r'([A-Za-z_]\w*)\s*(:=|\?=|\+=|=)\s*(.*)')
_REFERENCE = re.compile(r'\$\(([A-Za-z_]\w*)\)')
_IDO = re.compile(r'(?:^|/)ido-static-recomp/build/(5\.3|7\.1)/out/cc$')
_FLAG = re.compile(r'-(?:O[0-3]|mips[1-4]|32|non_shared|Xcpluscomm|Wab,-r4300_mul|Wo,-zcopy:[01]|'
                   r'g[0-3]?|G\d+|D[A-Za-z_]\w*(?:=[\w.+-]+)?|U[A-Za-z_]\w*)$')
_DEFINE = re.compile(r'(?<!\S)(-[DU][A-Za-z_]\w*(?:=[\w.+-]+)?)(?!\S)')
_RULE_TARGET = re.compile(r'[A-Za-z0-9_./%-]+\.o$')
_FLAG_VARIABLES = ('CFLAGS', 'OPT_FLAGS', 'CC_ONLY_FLAGS', 'MIPSISET', 'MIPSBIT', 'CPPFLAGS')


class Settings:
    """Read literal Make declarations and exact object overrides without running a donor build."""

    def __init__(self, project):
        self.headers = {}
        self.root = project.root
        self.variables = {}
        self.overrides = {}
        self.replacements = {}
        self.recipe_defines = {}
        path = project.root / 'Makefile'
        if not path.is_file() or not path.resolve().is_relative_to(project.root.resolve()) or path.stat().st_size > 2**20:
            return
        depth = 0
        recipe_targets = ()
        for line in path.read_text(encoding='utf-8', errors='replace').replace('\\\n', ' ').splitlines():
            if line.startswith('\t'):
                if line.lstrip().startswith('$(CC) -c '):
                    defines = tuple(_DEFINE.findall(line))
                    for target in recipe_targets:
                        self.recipe_defines.setdefault(target, set()).add(defines)
                continue
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            target, colon, value = line.partition(':')
            recipe_targets = tuple(name for name in target.split() if _RULE_TARGET.fullmatch(name)) if colon else ()
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
                    for token in assignment[3].split():
                        symbol, equals, obj = token.partition('=')
                        if equals and re.fullmatch(r'[A-Za-z_]\w*', symbol):
                            entries[symbol] = obj
            if colon and assignment and assignment[1] in ('CC', *_FLAG_VARIABLES):
                for name in target.split():
                    self.overrides.setdefault(name, []).append(assignment.groups())

    @staticmethod
    def _assign(variables, assignment):
        name, op, value = assignment
        if op == '+=':
            variables[name] = (variables.get(name, '') + ' ' + value).strip()
        elif op != '?=' or name not in variables:
            variables[name] = value

    def for_unit(self, unit):
        if not unit.source or not unit.source.endswith('.c'):
            return None
        scratch = unit.scratch or {}
        declared = re.fullmatch(r'ido[- ]?(5\.3|7\.1)', str(scratch.get('compiler', '')), re.I)
        target = (unit.base.relative_to(self.root).as_posix()
                  if unit.base and unit.base.is_relative_to(self.root) else '')
        variables = self.variables.copy()
        for assignment in self.overrides.get(target, ()):
            self._assign(variables, assignment)

        def expand(value, seen=()):
            def substitute(match):
                key = match[1]
                if key in seen or len(seen) == 16:
                    return match[0]
                return expand(variables.get(key, match[0]), (*seen, key))
            return _REFERENCE.sub(substitute, value)

        groups = [expand(variables.get(group, '')) for group in _FLAG_VARIABLES]
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
        match = _IDO.search(expand(variables.get('CC', '')).strip())
        if not match or not target:
            return None
        return self._profile(match[1], [*groups, ' '.join(literal)])

    def replacement_source(self, unit, symbol):
        if not unit.base or not unit.base.is_relative_to(self.root):
            return None
        target = unit.base.relative_to(self.root).as_posix()
        artifact = self.replacements.get(target, {}).get(symbol, '')
        match = re.fullmatch(r'build/(?:non_matching/)?(src/[A-Za-z_0-9./-]+\.c)\.o(?:\.linked\.elf\.bound\.elf)?', artifact)
        if not match or '..' in Path(match[1]).parts:
            return None
        path = (self.root / match[1]).resolve()
        return path if path.is_relative_to(self.root.resolve()) and path.is_file() else None

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


def verify(net, proj, fn, other, donor, names, settings, agent, job, cache, *, replacement=None):
    """Compile in private and retain this exact header and profile only with a fresh byte verdict."""

    if not settings or toolchains.installed(settings['toolchain']) is None:
        return 'skip', 'The donor file compiler is unavailable.'
    if not net.permuter._hold(agent, fn, count_turn=False, seconds=120):
        return 'skip', 'The function is being worked on.'
    try:
        store = net.project_store(proj['id'])
        current = store.one('SELECT * FROM projects WHERE id=?', proj['id'])
        private = {**dict(current), 'config': json.dumps(prepared_evidence.scoped_config(current, fn, settings))}
        reason = 'Donor source did not reproduce the original bytes.'
        whole_reason = ''
        if proj['arch'] == 'mips':
            try:
                _, invocation = net._invocation(private, fn)
                if replacement is not None:
                    lead = donor_unit.replacement(other, donor, fn['name'], invocation, replacement)
                    if lead:
                        accepted, whole_reason = _try(net, current, private, fn, *lead, agent, job)
                        if accepted:
                            return 'match', 'Verified from the donor build’s replacement C source.'
                whole = donor_unit.candidate(other, donor, {**names, donor['name']: fn['name']}, invocation, cache)
            except (NetworkError, OSError, ValueError):
                whole = None
            if whole:
                accepted, reason = _try(net, current, private, fn, *whole, agent, job)
                whole_reason = whole_reason or reason
                if accepted:
                    return 'match', 'Verified from a saved donor translation unit.'
        for body, header, _ in donor_sources.imported(net, other, donor, cache):
            if job.stop.is_set():
                return 'skip', 'Search stopped.'
            aliases = {**names, donor['name']: fn['name']}
            source = source_symbol_recovery.rename(body, aliases)
            header = source_symbol_recovery.rename(header, aliases)
            accepted, reason = _try(net, current, private, fn, source, header, agent, job)
            if accepted:
                return 'match', 'Verified with donor header and per-file compiler settings.'
        return 'miss', whole_reason or reason
    finally:
        net.permuter._let_go(agent, fn)


def _try(net, proj, private, fn, source, header, agent, job):
    """Offer one reduced translation unit and one relocation-based retry when the object supports it."""
    reduced = rebound = False
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
        variants = []
        if not rebound:
            if net.console(proj).export_overlay(net._rom_target(fn)) is not None:
                aliases = source_symbol_recovery.relocation_aliases(candidate, net._target_obj(fn, proj))
                if aliases:
                    variants = [('overlay-symbols', source_symbol_recovery.rename(source, aliases),
                                 source_symbol_recovery.rename(header, aliases))]
                else:
                    from . import overlay_donor_aliases

                    def measure(probe, context, private=private, fn=fn, proj=proj):
                        built = net._compile(private, context, probe, fn)
                        return net._evaluate(fn, proj, built.obj)[0] if built.ok else None

                    rewritten = overlay_donor_aliases.retry(
                        source, header, fn['name'], candidate, net._target_obj(fn, proj), measure, job.stop)
                    if rewritten:
                        variants = [('overlay-call-sites', *rewritten)]
            else:
                recover = arch.get(proj['arch']).source_symbol_bindings
                bindings = (recover(result.obj, fn['name'], fn['target'], fn['vram'], net.symbols(proj['id']).resolve)
                            if recover else [])
                variants = source_symbol_recovery.variants(source, header, {'symbol_bindings': bindings})
        if not variants:
            return False, '; '.join(problems) or 'Donor source did not reproduce the original bytes.'
        _, source, header = variants[0]
        rebound = True
    return False, 'Search stopped.'
