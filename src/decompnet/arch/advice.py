"""Small processor-specific observations for agents, separate from matching and source edits."""

import re

from .. import scoring
from . import get, mips_regions

MAX_ROWS = 8192
_MIPS_REGISTER = re.compile(r'(?<![\w$])(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)(?!\w)')
_DESTINATION = re.compile(r'(0x[0-9a-fA-F]+|\d+)\s*$')
_BRANCHES = mips_regions.CONDITIONAL | {'b', 'j'}


def _objdiff_mips(text, address=None):
    """objdiff prints bare register names, and branch destinations as offsets in the object's section; the ROM
    readers expect dollar-prefixed registers and destinations from the function's start (a branch of a function past
    the section's first read as one leaving it, and no block or return path was observed)."""
    text = _MIPS_REGISTER.sub(r'$\1', text)
    if address and text.split(None, 1)[0:1] and text.split(None, 1)[0] in _BRANCHES:
        if (dest := _DESTINATION.search(text)) and (offset := int(dest[1], 0) - address) >= 0:
            text = text[:dest.start()] + hex(offset)
    return text


def _objdiff_row(mark, target, candidate, addresses=(None, None)):
    """An objdiff row in the ROM judge's terms: bare registers get their dollar, and an operand mismatch ("i", all
    objdiff says) becomes "r" or "s" when only registers or stack offsets differ, as scoring marks ROM rows; the
    register-chain and stack-frame readers look for those (#246 review: they never fired on project diffs)."""
    target, candidate = _objdiff_mips(target, addresses[0]), _objdiff_mips(candidate, addresses[1])
    if mark == 'i' and target != candidate:
        (op_a, _, ops_a), (op_b, _, ops_b) = target.partition(' '), candidate.partition(' ')
        if op_a == op_b:
            mark = scoring.operand_marker(ops_a.strip(), ops_b.strip())
    return mark, target, candidate


def diff_lines(processor, rows, *, candidate=None, target=None, relocated=(), big_endian=True, objdiff=False,
               addresses=(None, None)):
    """Bounded observations from a complete scored diff. Missing readers simply add no advice. An objdiff diff
    (project mode) passes `addresses`: where the target's and the build's functions start in their sections."""
    reader = get(processor)
    if (not rows or len(rows) > MAX_ROWS
            or any(not isinstance(row, (list, tuple)) or len(row) != 3
                   or any(not isinstance(cell, str) for cell in row) for row in rows)
            or not any(row[0] != ' ' for row in rows)):
        return []
    if objdiff and processor == 'mips':
        rows = [_objdiff_row(*row, addresses) for row in rows]
    lines = []
    if reader.stack_layout:
        stack = reader.stack_layout(rows)
        if stack['frame_delta']:
            lines.append(f"Stack frame: target {stack['target_frame']} bytes, candidate {stack['candidate_frame']} bytes; "
                         "saved-register slots and local operands may have different shifts.")
    if reader.stack_frame_growth and (growth := reader.stack_frame_growth(rows)):
        lines.append(f"Frame adjustments and saved-register offsets consistently differ by {growth} bytes.")
    if reader.repair_search_hints:
        hints = reader.repair_search_hints(rows)
        for chain in hints['register_chains']:
            lines.append("Register mismatch chain: " + " → ".join(chain) + ".")
        if hints['branch_target']:
            lines.append("Branch destinations differ; inspect control flow before changing register allocation.")
        if hints['delay_slot']:
            lines.append("A changed instruction occupies a transfer's delay slot; inspect it with that transfer.")
    if reader.instruction_regions:
        graph = reader.instruction_regions([row[1] for row in rows if row[1]])
        if graph and len(graph['successors']) > 1:
            lines.append(f"Target partitions into {len(graph['successors'])} control-flow blocks "
                         f"with {len(graph['dependencies'])} explicit within-block register dependencies.")
    if reader.argument_register_evidence and candidate is not None and target is not None:
        evidence = reader.argument_register_evidence(candidate, target, relocated, big_endian)
        if evidence:
            kind, shift, positions = evidence
            lines.append(f"Observed {kind} argument-register positions {', '.join(str(p) for p in sorted(positions))} "
                         f"shift by {shift} register(s) from candidate to target; verify the calling convention.")
    return (["", "Processor observations (advisory; these do not prove a source rewrite):"]
            + ["- " + line for line in lines[:8]]) if lines else []


def assembly_lines(processor, assembly, *, objdiff=False, address=None):
    """Return-path observations from a function's target assembly; objdiff's (project mode) with `address`, where
    the function starts in its section."""
    reader = get(processor)
    if not reader.source_return_paths or not isinstance(assembly, str) or not assembly or len(assembly) > 2_000_000:
        return []
    if objdiff and processor == 'mips':
        # the ROM readers' /* offset vram word */ comments, the vram counted from the function's start like the
        # rebased branch destinations
        code = [line for line in assembly.splitlines() if line.strip()]
        assembly = '\n'.join(f'/* {i * 4:08X} {i * 4:08X} 00000000 */ {_objdiff_mips(line, address)}'
                              for i, line in enumerate(code))
    paths = reader.source_return_paths(assembly)
    if not paths:
        return []
    exits = paths['exits']
    known = sum(bool(not item['ambiguous'] and item['definitions'] and item['definitions'][0] >= 0) for item in exits)
    lines = [f"Observed {len(exits)} return site(s); {known} have a single explicit reaching definition of $v0."]
    if paths['truncated']:
        lines.append("The displayed return-path evidence is truncated.")
    if reader.source_return_contract and (result := reader.source_return_contract(assembly)):
        lines.append("A straight-line value reaches $v0 at return; check callers before inferring the C return type.")
        if result.get('requires_caller'):
            lines.append("That value comes from a load and may be scratch state rather than a consumed return value.")
    return ["", "### Processor observations (advisory)", *lines,
            "Register state alone does not establish a C return contract or authorize changing a prototype."]
