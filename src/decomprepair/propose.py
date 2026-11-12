"""One entry point over every repair family: `propose(source, name, ...)` returns each family's variant sources.

Each `*_repairs` module has a `variants(...)` function, but they take different inputs (some only the C, some the
header, the processor, the target's assembly or the compiler flags) and return either plain sources or
`(family, source)` pairs. This module calls each one with the inputs it asks for by name, skips the ones whose
required inputs the caller didn't give (say so in the report), and returns one shape: `Proposal(family, module,
source)`, without duplicates or the unchanged input.

Nothing here compiles or judges. A proposal is a guess: compile it with the game's own compiler and keep it only
when the bytes match. The report times each family, so a caller can tell how much of a repair run is proposing
and how much is compiling.
"""

import importlib
import inspect
import pkgutil
import time
from dataclasses import dataclass, field

# The inputs a family may ask for by parameter name; anything else required makes the family skip.
INPUTS = ('source', 'name', 'header', 'arch', 'asm', 'flags', 'callees', 'language', 'int_bits')


@dataclass(frozen=True)
class Proposal:
    family: str  # the repair's own label (several modules give several)
    module: str  # the module that proposed it, e.g. 'branch_layout_repairs'
    source: str  # the whole C source with the change


@dataclass
class Timing:
    module: str
    ms: float = 0.0
    proposals: int = 0
    skipped: str = ''  # why the family didn't run (a missing input), empty when it ran
    error: str = ''  # the exception a family raised, so a broken family is visible rather than silent


@dataclass
class Report:
    proposals: list = field(default_factory=list)
    timings: list = field(default_factory=list)

    @property
    def ms(self):
        return sum(t.ms for t in self.timings)


def modules():
    """Every repair family module's name, in a stable order."""
    import decomprepair
    return sorted(m.name for m in pkgutil.iter_modules(decomprepair.__path__) if m.name.endswith('_repairs'))


def _arguments(function, given):
    """The keyword arguments `function` asks for out of `given`, or the name of a required one that's missing.

    Everything goes by name: no family takes positional-only parameters, and an optional input left out keeps
    the family's own default."""
    kwargs = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if given.get(parameter.name) is not None:
            kwargs[parameter.name] = given[parameter.name]
        elif parameter.default is parameter.empty:
            return None, parameter.name
    return kwargs, None


def propose(source, name, *, header='', arch=None, asm=None, flags=None, callees=None, language=None,
            int_bits=None, only=None):
    """Every family's proposals for function `name` in `source`, with a timing per family.

    `arch` is the processor ('mips', 'ppc', 'x86', 'arm'); `asm` the target function's assembly text; `flags` the
    compiler flags. Families that need an input you leave out are skipped and say which. `only` limits the run to
    those module names.
    """
    given = dict(source=source, name=name, header=header, arch=arch, asm=asm, flags=flags, callees=callees,
                 language=language, int_bits=int_bits)
    report, seen = Report(), {source}
    for module_name in modules():
        if only is not None and module_name not in only:
            continue
        timing = Timing(module_name)
        report.timings.append(timing)
        module = importlib.import_module(f'decomprepair.{module_name}')
        function = getattr(module, 'variants', None)
        if function is None:
            timing.skipped = 'no variants()'
            continue
        kwargs, missing = _arguments(function, given)
        if kwargs is None:
            timing.skipped = f'needs {missing}'
            continue
        family = getattr(module, 'FAMILY', module_name.removesuffix('_repairs'))
        started = time.perf_counter()
        try:
            result = list(function(**kwargs) or ())
        except Exception as error:  # one family's bug must not hide every other family's proposals
            timing.error = f'{type(error).__name__}: {error}'
            result = []
        timing.ms = (time.perf_counter() - started) * 1000
        for value in result:
            label, text = value if isinstance(value, (tuple, list)) and len(value) == 2 else (family, value)
            if isinstance(text, str) and text not in seen:
                seen.add(text)
                report.proposals.append(Proposal(str(label), module_name, text))
                timing.proposals += 1
    return report
