"""Advisory source/type checks: no inferred identity or SDK body is accepted as a verdict."""
import re

from ..forks.csource import _blank, find


def hints(source, name, asm='', arch='', rows=None):
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    body = clean[span[0]:span[1]]
    from .helper_call_diagnostics import suggestions
    from .macro_repairs import diagnostic
    from .matching_investigations import hints as investigation_hints
    from .permuter_focus import classify
    from .preparation_diagnostics import allocations
    macro_note = diagnostic(source)
    result = ([macro_note] if macro_note else []) + investigation_hints(rows,arch) + suggestions(rows) + allocations(source, rows)
    from .guarded_loop_repairs import hints as loop_hints
    if arch=='mips':result.extend(loop_hints(source,name,rows))
    from .return_contract_repairs import hint as return_hint
    if note:=return_hint(source,name,asm,arch):result.insert(0,note)
    if classify(rows) == 'register':
        result.append('Only register operands differ in the displayed instructions. Prioritise local lifetimes and declaration shapes over constants: try branch_tail_lifetimes, staged_call_arguments or combined_call_lifetimes when offered. Inline helpers can enlarge the frame; check instruction count and frame size before expanding that direction. This is search advice, not an exact verdict.')

    # m2c drafts and saved C receive the same advice; no helper is emitted unconditionally.
    if rows and any(r[0]!=' ' for r in rows) and re.search(r'\bif\s*\(',body) and re.search(r'\b(?:s16|short)\s*\)',body):
        result.append('A branch-specific wide value is narrowed for a later use. Try branch_scalar_lifetime inside the selected arm, even if other parts contain switches or loops. For call-setup ordering, compare embedded_call_assignment and inline_call_count; preserve argument dependencies and exact prototypes. Explicit shift spelling alone may be optimized into the same temporary. These are bounded source hypotheses, not match verdicts.')

    from .target_local_repairs import call_arguments
    if arch == "mips" and call_arguments(source, name, asm):
        result.append("The target supplies a first call argument that a candidate-owned void prototype omits. "
                      "Try the target_call_argument local family; shared declarations stay unchanged.")
    from .hardware_operation_repairs import hints as hardware_hints
    result.extend(hardware_hints(source,asm,arch,rows))
    from .live_return_address_repairs import hint as live_ra_hint
    if note:=live_ra_hint(asm,arch):result.insert(0,note)
    if arch=="mips" and rows and re.search(r"\bmfc1\b",asm or "") and any(r[0]=="i" and re.match(r"(?:b\w*|j)\s",r[1]) for r in rows):
        result.append("Branch destinations near an FP conversion can depend on debug line markers and assembler hazard NOP placement. Try debug_conversion_tail with the original debug profile before flag sweeps. Preserve verified physical source lines through polishing and export; an equivalent one-line temporary may compile differently.")
    original=source[span[0]:span[1]]
    if all(re.search(r"case\s+'"+code+r"'\s*:",original) for code in ('s','f','c')):
        result.append('This resembles a formatting-library routine. Check local SDK/library leads before a broad search. '
                      'Keep format characters separate from integer output lengths, preserve output macros, case order, '
                      'fallthrough groups and flag-loop labels. Source identity and a lower score still need the exact judge.')
    if arch == 'mips':
        from .sdk_io_repairs import hints as io_hints
        result.extend(io_hints(source,asm,rows))
        from ..arch.mips_runtime import entry_context, helper, specialized_calls
        loads = {}
        for row in rows or []:
            if len(row)!=3 or row[0] not in {'>','i','|','o'}:continue
            match=re.match(r'lui\s+\$at,\s*%hi\((D_[0-9A-Fa-f]{8})\)',row[2])
            if match:
                group=int(match[1][2:],16)>>16
                loads[group]=loads.get(group,0)+1
        if any(n>=2 for n in loads.values()):
            result.append('The candidate has repeated extra address loads near global stores. Check whether original '
                          'object definitions were replaced by extern declarations; local definition/layout knowledge '
                          'can change address-load grouping. Try only explicitly selected reference definitions and '
                          'verify data placement during export.')
        if kind := helper(asm):
            result.append('Target instructions suggest an O32 64-bit runtime helper ('+kind+'). '
                          'Check 64-bit argument/return widths and bounded MIPS III -32 -O1 profiles before mutating 32-bit drafts.')
        saved,mixed_float=entry_context(asm)
        if saved or mixed_float:
            result.append('Entry-context lead: '+(', '.join(sorted(saved))+' is read before a local definition. ' if saved else '')+
                          ('Incoming $f12 and an $a0-based pointer are both used. ' if mixed_float else '')+
                          'Inspect callers, function boundaries and original static-helper context before ordinary-ABI permutation. '
                          'This is advisory; no signature or boundary was changed.')
        if rows and any(row[0]!=' ' for row in rows) and all(row[0]==' ' or (row[0]=='<' and row[1].strip()=='nop') for row in rows):
            result.append('Only target-only NOPs remain. Check unreachable code, source line/debug context and boundaries; '
                          'do not insert padding instructions or change the target to force a match.')
        if rows and any(row[0]=='r' for row in rows) and not any(row[0] in {'s','|','>','<'} for row in rows):
            result.append('Register-only differences can come from original record/pointer types and loop structure. '
                          'Prepare the original declarations and control flow before a broad permutation pass; do not invent fields.')
        if specialized_calls(asm):
            result.append('A caller-saved temporary appears live across a call. Whole-unit/static-helper clobber analysis '
                          'may use a specialized calling convention. Check argument registers and helper context; '
                          'isolated ordinary-ABI permutations or adding helper bodies may not reproduce it.')
    if 'M2C_FIELD(' in body or re.search(r'\*\s*\(\s*\([^)]*\*\s*\)\s*\(', body):
        result.append('Check raw offset casts against real record declarations and argument types. '
                      'Use an explicitly configured local source/SDK template before more permutations; preserve its macros, locals and line breaks.')
    if re.search(r'\*[^;{}\n]+\*', body):
        result.append('Check multiplication order, intermediate widths and signedness against the source and assembly. '
                      'Do not reassociate floating-point products or add casts merely to raise similarity.')
    if re.search(r'\bif\s*\(', body):
        result.append('Reference source may differ from retail: verify branch exits, allocation-failure paths and cleanup stores against the ROM. '
                      'Source identity alone is not an exact-match verdict.')
    return result
