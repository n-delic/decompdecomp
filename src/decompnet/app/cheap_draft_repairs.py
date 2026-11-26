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
"""
from decomprepair import (
    frame_padding_repairs,
    global_array_repairs,
    guard_shape_repairs,
    mmio_repairs,
    stack_object_size_repairs,
    stack_run_array_repairs,
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


def variants(draft: str, name: str, arch: str, asm: str = '', header: str = '', flags=()) -> list[tuple[str, str]]:
    """[(family, source)] of the cheap families' changes to `draft`, each a separate candidate, at most MAX_CANDIDATES.
    A family that fails on unusual source is skipped: a draft is never lost to a rewrite."""
    asm, header = asm or '', header or ''
    out, seen = [], {draft}
    calls = list(_stages(draft, name, arch, asm, header))
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
