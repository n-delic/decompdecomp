"""The repair families that match a machine draft almost for free, judged beside it in autopilot's draft stage.

Measured on a 1,931-function N64 game's Deep Permuter run: 59 functions were matched by these families, each in a few
seconds of search (an MMIO register read written through `volatile` took 4 s for 11 matches), after 490 failed attempts
by autopilot and agents that never tried them. Autopilot's draft stage judged only the raw m2c draft and a short list of
alternatives; the full repair pass (`draft_repairs.variants`) only ran later, in the permuter. A function that one of these
families matches therefore waited for an agent's turns first.

Only the families whose results were cheap and exact are here, each gated by the evidence its module already reads (the
target's assembly, the declared types), so a draft that doesn't show the shape costs nothing. At most MAX_CANDIDATES
extra compiles per function; the raw draft is still tried first and stays the stored seed. `linked_switch_empty_arm` is
not here: it works from a compiled jump table's differences, so it can only run after a compile (the Deep Permuter's
diff-gated step).

A target jump table that sends several entries to one place is the other gate (`_table_copies`): m2c stacks those case
labels over one body, where the game wrote the body per label. The stage is the only automatic lane that returns to an
open function after a library update, and without it a function with 400 recorded trials, all from before
`case_label_copies` existed, never kept that step (2655 -> 800, exact with `symbol_run_pointer` on top). The tables come
from `switch_tables.read`, which reads MIPS ROM games only, so other consoles get nothing here yet (ROADMAP.md).
"""
from decomprepair import (
    case_label_copies,
    frame_padding_repairs,
    global_array_repairs,
    guard_shape_repairs,
    leaf_frame_repairs,
    mmio_repairs,
    stack_object_size_repairs,
    stack_run_array_repairs,
    symbol_run_pointer,
    symbol_width_repairs,
    target_call_arity_repairs,
    unknown_symbol_offset_repairs,
)

from . import library_registry

# Project setting (`cheap_draft_repairs`, default on): off leaves autopilot's draft stage as it was.
SETTING = 'cheap_draft_repairs'
MAX_CANDIDATES = 8  # extra compiles per function, the most one draft may cost
# Declared (registry) repairs that belong here, by module name.
DECLARED = ('rule:abs_idiom', 'loop_argument_inline')  # a JSON rule's module is named `rule:<id>`


def _stages(source, name, arch, asm, header):
    """(family, call) in the order of how often they matched per second of search."""
    yield 'mmio_address', lambda: mmio_repairs.variants(source, name, arch)
    # A leaf whose frame is never stored to had an unused local, which m2c drops. A function whose compiler changed was
    # redrafted, judged as an empty body (200) and left open, with this one child exact.
    yield leaf_frame_repairs.FRAME, lambda: [child for child in leaf_frame_repairs.variants(source, name, asm)
                                             if child[0] == leaf_frame_repairs.FRAME]
    yield 'inferred_frame_reservation', lambda: frame_padding_repairs.variants(source, name, asm)
    yield 'symbol_access_width', lambda: symbol_width_repairs.variants(source, name, asm)
    yield 'stack_run_array', lambda: stack_run_array_repairs.variants(source, name, asm)
    yield 'unknown_symbol_offsets', lambda: unknown_symbol_offset_repairs.variants(source, name, header)
    yield 'stack_object_size', lambda: stack_object_size_repairs.variants(source, name, asm)
    yield 'global_table_index', lambda: global_array_repairs.variants(source, name)
    yield 'guard_shape', lambda: guard_shape_repairs.variants(source, name)
    yield 'target_call_arity', lambda: target_call_arity_repairs.variants(source, name, asm)


def _declared(source, name, arch, asm, header, flags):
    from decomprepair import registry
    context = library_registry.draft_context(source, name, header, arch, asm, flags)
    for repair in library_registry.runnable('draft', arch):
        if repair.module in DECLARED:
            yield repair.module, lambda repair=repair: registry.run(repair.module, **repair.arguments(context))


def _table_copies(draft, name, tables):
    """The draft with a body per stacked case label, then that with its last run of one symbol's accesses read through a
    block pointer (a step worth keeping, then the two together). Only for a target table with a shared destination."""
    if not any(len(set(targets)) < len(targets) for _low, targets in tables):
        return []
    copies = case_label_copies.variants(draft, name)
    if not copies:
        return []
    family, copied = copies[-1]  # every run of stacked labels copied
    out = [(family, copied)]
    pointed = symbol_run_pointer.variants(copied, name)
    if pointed:
        out.append((f'{family}+{pointed[0][0]}', pointed[0][1]))  # the first child is the last run
    return out


def variants(draft: str, name: str, arch: str, asm: str = '', header: str = '', flags=(),
             tables=()) -> list[tuple[str, str]]:
    """[(family, source)] of the cheap families' changes to `draft`, each a separate candidate, at most MAX_CANDIDATES.
    A family that fails on unusual source is skipped: a draft is never lost to a rewrite."""
    asm, header = asm or '', header or ''
    out, seen = [], {draft}
    calls = [(case_label_copies.FAMILY, lambda: _table_copies(draft, name, tables))]
    calls += list(_stages(draft, name, arch, asm, header))
    try:
        calls += list(_declared(draft, name, arch, asm, header, flags))
    except Exception:  # noqa: BLE001 - a registry that can't be asked costs the extras, not the draft
        pass
    for family, call in calls:
        try:
            found = call() or ()
        except Exception:  # noqa: BLE001 - as above
            continue
        for item in found:
            label, text = item if isinstance(item, tuple) else (family, item)
            if isinstance(text, str) and text and text not in seen:
                seen.add(text)
                out.append((label, text))
                if len(out) >= MAX_CANDIDATES:
                    return out
    return out


def enabled(cfg) -> bool:
    return bool((cfg or {}).get(SETTING, True))
