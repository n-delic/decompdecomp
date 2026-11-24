"""The library's self-declared repairs, run by every lane with no wiring in the app.

A decomp-repair module that declares `REPAIR` (decomprepair.registry) is run here with the context it says it needs:
the draft pass (`draft_repairs`, which the Deep Permuter's beam also asks with no limit) and the Deep Permuter's local
lane iterate `registry.all()` instead of one hand-written call per module, so a new module needs a library release and
the pin bump, and nothing in the app (tests/test_library_family_wiring.py checks that every declaration can run).
Modules wired by hand before the registry (about 700, listed in the library's tests/registry_legacy.txt) keep their calls
in `draft_repairs` and `deep_permuter`; `EXPLICIT` lists any declared module that is still wired by hand so the registry
skips it (a module is never run twice)."""
from decomprepair import registry

EXPLICIT = frozenset()   # declared modules that stay wired by hand because the order of their call matters


def runnable(lane, arch=None, unlimited=False, directives=False):
    """The declared repairs for `lane` ('draft' or 'local'), in registry order. A draft pass with no limit is the beam's.
    `directives`: only the repairs that read the function's own `#` lines (the registry's `directives` flag, a library newer
    than the one that added it has it), which the draft pass runs on a function it otherwise masks."""
    lanes = (lane, 'beam') if lane == 'draft' and unlimited else (lane,)
    return [repair for repair in registry.all()
            if repair.module not in EXPLICIT and any(repair.runs_in(item, arch) for item in lanes)
            and (not directives or getattr(repair, 'directives', False))]


def families():
    """Every family name the declared repairs label children with: what the agents are told they may ask for."""
    return tuple(family for repair in registry.all() for family in repair.families)


def draft_context(source, name, header, arch, asm, flags, words=None, callees='', tables=None):
    """What the declared repairs may ask for. `words`: {symbol: the 32-bit word at its address} for the function's float
    constants (float_literal_words.read), the game data the library can't read itself. `callees`: the matched C of the
    function's callees (capture_followup_repairs.callee_evidence), a callee's own parameters for a family that changes a
    call (missing_call_argument_local); a library older than the registry's `callees` key never asks for it."""
    return dict(source=source, name=name, header=header or '', arch=arch, asm=asm or '', flags=tuple(flags or ()),
                words=dict(words or {}), callees=callees or '',
                tables=[[low, list(targets)] for low, targets in tables or ()])


def local_calls(context):
    """The Deep Permuter's local lane calls (name, args, kwargs) for the declared repairs: the subprocess runs
    `registry.run`, which labels what each returns. `context` holds what the lane has (see `draft_context`)."""
    arch = context.get('arch')
    return [('decomprepair.registry.run', (repair.module,), {key: list(value) if isinstance(value, tuple) else value
                                                              for key, value in repair.arguments(context).items()})
            for repair in runnable('local', arch)]
