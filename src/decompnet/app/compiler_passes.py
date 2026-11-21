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
    header = net._context(proj['id'], fn['unit'])['header']
    profile = compiler_for(net.cfg(proj), fn['vram'], fn)
    result = compiler_diagnostics.diagnose(profile, header, source, fn['name'], root=proj['root'], excerpts=passes)
    if result.get('skipped'):
        raise NetworkError(f"{result['skipped']} compiler_passes needs a pinned, installed GCC-family compiler "
                           f"(this function's is {profile.get('toolchain') or 'a custom command'}); use compile_variants "
                           "and guide(\"near-match\") instead.")
    return render(result, passes, revision)


def render(result, passes, revision):
    out = [f"Compiler passes for revision {revision} ({result.get('toolchain')}, flags "
           f"{' '.join(result.get('original_flags') or [])} -da; diagnostic only, nothing recorded)."]
    if not result.get('output_unchanged'):
        out.append("Warning: the dump flags changed the generated code; compare passes only between dumps of this kind.")
    table = (result.get('allocation') or {}).get('values') or []
    if table:
        out += ["", "Register allocation, in the order GCC allocated (priority = floor_log2(refs)*refs/live insns):"]
        for v in table[:TABLE_ROWS]:
            names = ','.join(v.get('aliases') or []) or 'temp'
            weight = v.get('legacy_weight')
            out.append(f"{v['allocation_rank'] + 1:>3}. pseudo {v['pseudo']} ({names}): {v['references']} refs, "
                       f"{v['live_insns']} live, priority {weight:.3f} -> hard reg {v['hard_register']}"
                       if weight is not None else
                       f"{v['allocation_rank'] + 1:>3}. pseudo {v['pseudo']} ({names}) -> hard reg {v['hard_register']}")
    for phase in passes:
        row = (result.get('phases') or {}).get(phase)
        if not row:
            out += ["", f"## {phase}: not written by this compiler build"]
            continue
        out += ["", f"## {phase} ({row['bytes']} bytes{', excerpt cut' if row.get('excerpt_cut') else ''})",
                row.get('excerpt', '').rstrip()]
    return '\n'.join(out)
