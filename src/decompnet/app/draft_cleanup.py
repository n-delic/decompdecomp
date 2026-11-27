"""Cleaned copies of a machine draft, judged beside it so a function starts from the better source.

Measured on 90 matched functions of an N64 game (docs/learnings/finding/draft-cleanup-measurement.md): of the
cleanups tried, only the record views of raw `*(T *)((u8 *)p + K)` accesses and the display-list stores as gbi
macros lowered the judge's score on average without hurting many (b+i: 6 better, 1 worse, mean -138). The rest
either never fired on m2c's output or made drafts worse on average (call arity, goto loops, the phase-storage
union), so they stay out of this stage; the permuter still tries them later on its own terms.

Since then (draft-start-candidates.md): m2c's byte steps on typed pointers (`pointer_byte_step`, read from the
target's immediates: 84 of 94 drafts it touched started better, 1 worse) join the chain, and the chained copy with
its table cursors as the counter's index (`table_cursor_index`) is a second candidate: game-wide 142 drafts start
better instead of 69, 8 exact instead of 4, none worse than before. All of a draft's byte steps are then fixed in
one copy (`pointer_byte_step_all`): on 15 drafts with two or more sites, 14 scored better than with one site, 1 worse.

A cleaned draft is only a candidate: the ordinary compiler and judge score it like any other, the raw draft is
still tried first and stays the function's stored seed, and a best is only ever replaced by a better one.
"""
from decomprepair import (
    field_struct_view_repairs,
    gbi_macro_commands,
    pointer_scale_all_repairs,
    pointer_scale_repairs,
    raw_address_repairs,
    record_slot_repairs,
    table_cursor_index,
)

# Project setting (`draft_cleanup`, default on): off leaves machine drafts exactly as m2c and its fixes made them.
SETTING = 'draft_cleanup'


def _first(found, source):
    for item in found or ():
        text = item[1] if isinstance(item, tuple) else item
        if isinstance(text, str) and text and text != source:
            return text
    return None


def _stages(name, arch, asm, header):
    yield lambda s: (field_struct_view_repairs.variants(s, name) or raw_address_repairs.variants(s, name, arch, asm, header)
                     or record_slot_repairs.variants(s, name))
    yield lambda s: gbi_macro_commands.variants(s, name, header)
    # m2c's byte steps on typed pointers, as the target's immediates say (draft-start-candidates.md); every site at once,
    # since a draft often writes the mistake twice and one fix alone left a function one instruction off
    yield lambda s: (pointer_scale_all_repairs.variants(s, name, asm)
                     or pointer_scale_repairs.variants(s, name, asm)) if asm else []


def cleaned(draft: str, name: str, arch: str, asm: str = '', header: str = '') -> list[tuple[str, str]]:
    """The draft with every enabled cleanup applied in turn, as [('draft_cleanup', source)], then that copy with
    its table cursors written as the counter's index (`table_cursor_index`) as a second candidate: it helps less
    often (19 of 34 better, 11 worse), so it never replaces the first. [] when nothing applies. A cleanup that fails
    on unusual source is skipped: a draft is never lost to a rewrite."""
    current = draft
    for stage in _stages(name, arch, asm or '', header or ''):
        try:
            current = _first(stage(current), current) or current
        except Exception:  # noqa: BLE001 - a rewrite's parser failing must not cost the draft itself
            continue
    out = [('draft_cleanup', current)] if current != draft else []
    try:
        indexed = _first(table_cursor_index.variants(current, name, asm or '', header or ''), current)
    except Exception:  # noqa: BLE001 - as above
        indexed = None
    if indexed:
        out.append(('draft_cleanup_index', indexed))
    return out


def enabled(cfg) -> bool:
    return bool((cfg or {}).get(SETTING, True))
