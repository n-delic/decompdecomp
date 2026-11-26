"""compiler_passes: an agent reads the compiler's own stages for its candidate (guide "near-match").

Helpers stuck on near-matches wrote variant after variant at one score, where a person dumps the compiler's passes
and finds the stage that decides the difference, and reads the register-allocation priorities for register-only
rows. This runs the isolated diagnostic compile compiler_diagnostics already makes for the permuter (GCC-family
compilers, pinned and installed, with the project's flags plus -da, under the compiler's own sandbox and timeout) and
returns bounded excerpts of the passes asked for and the allocation table. Diagnostic only: no attempt, no score,
no move-on count; the judge still decides.

Project mode (a public decomp's own build) has no such tool yet: its units compile through the project's build
system rather than the app's compiler adapter, and most of those compilers (Metrowerks, MSVC) print no pass dumps
(ROADMAP.md)."""
import re

from ..network import NetworkError
from ..project import compiler_for
from . import agent_tokens, compiler_diagnostics

PASS_NAMES = compiler_diagnostics.PHASE_ORDER
DEFAULT_PASSES = ('jump', 'greg', 'dbr')
MAX_PASSES = 4
TABLE_ROWS = 16


def run(net, agent, slug, function, revision, passes=None):
    passes = [str(p).strip().lower().lstrip('.') for p in (passes or DEFAULT_PASSES)]
    unknown = [p for p in passes if p not in compiler_diagnostics.PHASES]
    if unknown or not 1 <= len(passes) <= MAX_PASSES:
        raise NetworkError(f"Ask for 1-{MAX_PASSES} passes from: {', '.join(PASS_NAMES)}"
                           + (f" (not {', '.join(unknown)})" if unknown else '') + '.')
    fn = net._function(function, slug)
    if net.fork_slug(slug):
        raise NetworkError("compiler_passes works in ROM projects only for now: this project compiles through its own "
                           "build. Compare compile_variants results instead, and read guide(\"near-match\").")
    source, actual = agent_tokens.candidate(net, agent, slug, function)
    if actual != revision:
        raise agent_tokens.stale(net, agent, fn, revision, source, actual)
    proj = net._project(slug)
    result, profile = diagnose(net, proj, fn, source, passes)
    return render(result, passes, revision) + source_line(result.get('toolchain') or profile.get('toolchain'))


def diagnose(net, proj, fn, source, passes, excerpt_chars=compiler_diagnostics.EXCERPT_CHARS, extra_flags=(), keep_files=False,
             profile_flags=()):
    """The isolated diagnostic compile of `source` under the function's own profile (the tool's and the skill's
    passes.py share it): (result, profile). `profile_flags` are added to the profile's flags for the baseline and the
    dump compile alike (the skill's `--flags`, as fast_judge.py takes them: `-mfp32` probes a function whose profile is
    `-mfp64` without changing it), where `extra_flags` go on the dump compile only."""
    header = net._context(proj['id'], fn['unit'])['header']
    profile = compiler_for(net.cfg(proj), fn['vram'], fn)
    if profile_flags:
        profile = dict(profile, flags=[*(profile.get('flags') or []), *profile_flags])
    result = compiler_diagnostics.diagnose(profile, header, source, fn['name'], root=proj['root'], excerpts=passes,
                                           excerpt_chars=excerpt_chars, extra_flags=extra_flags, keep_files=keep_files)
    if result.get('skipped'):
        raise NetworkError(f"{result['skipped']} compiler_passes needs a pinned, installed GCC-family compiler "
                           f"(this function's is {profile.get('toolchain') or 'a custom command'}); use compile_variants "
                           "and guide(\"near-match\") instead.")
    return result, profile


SKILL_PASSES = ('greg', 'sched', 'sched2', 'loop')
LOOP_NOTE_LINES = 80
SKILL_EXCERPT_CHARS = 60000  # a whole function's dump: the skill's passes.py reads orders, not a bounded excerpt


def preferences(result):
    """The register each pseudo asked for (greg's `;; N preferences: R` lines: an argument or a return value), by the
    names the allocation table gives it: what the allocator tries first for it."""
    text = ((result.get('phases') or {}).get('greg') or {}).get('excerpt', '')
    values = {v['pseudo']: v for v in (result.get('allocation') or {}).get('values') or []}
    lines = []
    for pseudo, hard in re.findall(r'^;; (\d+) preferences: ([\d ]+?)\s*$', text, re.M):
        got = values.get(int(pseudo), {}).get('hard_register')
        names = ','.join(values.get(int(pseudo), {}).get('aliases') or []) or 'temp'
        lines.append(f"pseudo {pseudo} ({names}) prefers hard reg {hard.strip()}"
                     + (f", got {got}" if got is not None else ''))
    return lines


def conflicts(result):
    """The hard registers each global pseudo is live alongside (greg's `;; N conflicts:` lines minus the other
    pseudos): the registers the allocator cannot give it, so what a preference can and cannot win. A pseudo whose
    preferred register is absent here has that register free; one that still lands elsewhere lost it to an earlier
    pseudo or to a stronger (copy) preference (global.c find_reg)."""
    text = ((result.get('phases') or {}).get('greg') or {}).get('excerpt', '')
    order = re.search(r'^;; \d+ regs to allocate:\s*([\d ]*)$', text, re.M)
    pseudos = set(order[1].split()) if order else set()
    values = {v['pseudo']: v for v in (result.get('allocation') or {}).get('values') or []}
    lines = []
    for pseudo, rest in re.findall(r'^;; (\d+) conflicts:([\d ]*)$', text, re.M):
        hard = [n for n in rest.split() if n not in pseudos]
        names = ','.join(values.get(int(pseudo), {}).get('aliases') or []) or 'temp'
        lines.append(f"pseudo {pseudo} ({names}) is live with hard reg{'s' if len(hard) != 1 else ''} {' '.join(hard) or 'none'}")
    return lines


def schedule_order(result, phase):
    """A scheduling pass's instruction order, `uid pattern` per insn (the pass's dump lists them in the order it
    chose): compare `sched` (first scheduler) with `sched2` and with the target's order."""
    text = ((result.get('phases') or {}).get(phase) or {}).get('excerpt', '')
    return [f"{uid} {pattern}" for uid, pattern in
            re.findall(r'^\((?:insn|jump_insn|call_insn) (\d+) .*?\{(\w+)\}', text, re.M | re.S)]


def verbose_schedule(result):
    """The first scheduler's notes (the `;;` lines the pass writes into the sched dump: each ready list with its
    launch priorities, the insns launched, the set-count rule), apart from the insns themselves. The pinned KMC cc1
    writes them with plain `-dS` and refuses `-fsched-verbose-2` ("Invalid option", so the whole dump failed and
    passes.py --sched-verbose answered "did not accept phase capture"): no flag is added."""
    text = ((result.get('phases') or {}).get('sched') or {}).get('excerpt', '')
    return [line for line in text.splitlines() if line.startswith(';;')]


def loop_decisions(result):
    """The loop optimizer's own notes from the `loop` dump (loop.c writes them between the RTL: which insns it hoisted
    out of a loop and why, the induction variables it found, which givs it combined or reduced and what it refused):
    the lines that are prose, not RTL. An SN or KMC GCC loop that is hoisted or strength-reduced differently from the
    target shows here first."""
    text = ((result.get('phases') or {}).get('loop') or {}).get('excerpt', '')
    lines = [line.rstrip() for line in text.splitlines() if line[:1].isalpha()]
    return lines[:LOOP_NOTE_LINES] + ([f'... {len(lines) - LOOP_NOTE_LINES} more in the loop dump'] if len(lines) > LOOP_NOTE_LINES else [])


def report(net, proj, fn, source, passes=SKILL_PASSES, sched_verbose=False, files=None, profile_flags=()):
    """What the skill's passes.py prints: the allocation priority table, register preferences, scheduling orders and
    where the compiler's source is. Diagnostic only, like the tool: nothing is recorded or judged. `sched_verbose`
    makes sure the first scheduler's dump is taken and prints its notes (the launch priorities). `files`, a dict, is
    filled with every file the diagnostic compile left (the preprocessed C, the final assembly, each pass dump)."""
    if sched_verbose and 'sched' not in passes:
        passes = (*passes, 'sched')
    result, profile = diagnose(net, proj, fn, source, passes, SKILL_EXCERPT_CHARS,
                               **({'keep_files': True} if files is not None else {}),
                               **({'profile_flags': tuple(profile_flags)} if profile_flags else {}))
    if files is not None:
        files.update(result.get('files') or {})
    out = [render(result, (), 'candidate', rows=None)]
    if prefs := preferences(result):
        out += ['', 'Register preferences (greg):', *prefs]
    if lines := conflicts(result):
        out += ['', 'Hard registers each pseudo is live with (it cannot take these):', *lines]
    if 'loop' in passes and (notes := loop_decisions(result)):
        out += ['', 'Loop optimizer decisions (hoists, induction variables, givs; the `loop` dump):', *('  ' + n for n in notes)]
    for phase in ('sched', 'sched2'):
        if phase in passes:
            order = schedule_order(result, phase)
            out += ['', f"Scheduling order, {phase} ({'first' if phase == 'sched' else 'second'} scheduler), "
                        f"{len(order)} insns:", '  ' + '  '.join(order) if order else '  (not written by this compiler build)']
    if sched_verbose:
        notes = verbose_schedule(result)
        out += ['', f'First scheduler notes ({len(notes)} lines):',
                *(notes or ['  (this compiler build wrote no verbose notes into its sched dump)'])]
    return '\n'.join(out) + source_line(result.get('toolchain') or profile.get('toolchain'))


def source_line(compiler):
    """Where to read the passes themselves: the compiler's source, installed with it (toolchains.source_for)."""
    from .. import toolchains
    if not compiler or not toolchains.source_id(compiler):
        return "\n\nNo public source for this compiler's passes."
    where = toolchains.source_for(compiler)
    if where is None:
        return (f"\n\nIts source ({toolchains.source_id(compiler)}) isn't downloaded yet: installing the compiler again "
                "in Compilers & tools fetches it.")
    return (f"\n\nIts source, to read the pass that decides this (sched.c, local-alloc.c, global.c, reorg.c, jump.c, "
            f"cse.c, loop.c, combine.c): {where} ({toolchains.get(toolchains.source_id(compiler)).description})")


def hard_name(hard):
    """`hard reg N`, or `spilled to the stack` for a pseudo the allocator gave no register (the dump's -1)."""
    return 'spilled to the stack' if hard is None or hard < 0 else f'hard reg {hard}'


def render(result, passes, revision, rows=TABLE_ROWS):
    out = [f"Compiler passes for revision {revision} ({result.get('toolchain')}, flags "
           f"{' '.join(result.get('original_flags') or [])} -da; diagnostic only, nothing recorded)."]
    if not result.get('output_unchanged'):
        out.append("Warning: the dump flags changed the generated code; compare passes only between dumps of this kind.")
    table = (result.get('allocation') or {}).get('values') or []
    if table:
        out += ["", "Register allocation, in the order GCC allocated (priority = floor_log2(refs)*refs/live insns):"]
        # #match-function: the skill's report names every pseudo (rows=None): the tail of the table is where a value
        # that lost a saved register shows up, and 16 rows hid the pseudo that explained a stack-slot difference.
        for v in table[:rows]:
            names = ','.join(v.get('aliases') or []) or 'temp'
            weight = v.get('legacy_weight')
            out.append(f"{v['allocation_rank'] + 1:>3}. pseudo {v['pseudo']} ({names}): {v['references']} refs, "
                       f"{v['live_insns']} live, priority {weight:.3f} -> {hard_name(v['hard_register'])}"
                       if weight is not None else
                       f"{v['allocation_rank'] + 1:>3}. pseudo {v['pseudo']} ({names}) -> {hard_name(v['hard_register'])}")
    for phase in passes:
        row = (result.get('phases') or {}).get(phase)
        if not row:
            out += ["", f"## {phase}: not written by this compiler build"]
            continue
        out += ["", f"## {phase} ({row['bytes']} bytes{', excerpt cut' if row.get('excerpt_cut') else ''})",
                row.get('excerpt', '').rstrip()]
    return '\n'.join(out)
