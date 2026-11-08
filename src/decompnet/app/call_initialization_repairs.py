"""Place an unescaped constant local reset in an existing call argument, preserving every path.

The CFG must reach a direct call on every path before any read or different write to that local.
Arguments which refer to it, loops, unknown syntax and escaped locals give no candidate.
"""
import re

from . import local_flow
from .integer_repair_support import IDENT, NUMBER, edit, split_args
from .pattern_repairs import close

LIMIT = 8


def assignment(flow, node, name):
    return re.fullmatch(rf'\s*{re.escape(name)}\s*=\s*({NUMBER})\s*;\s*', flow.part.clean[node.start:node.end])


def call(flow, index, variable):
    node = flow.nodes[index]
    text = flow.part.clean[node.start:node.end]
    match = re.fullmatch(rf'\s*({IDENT})\s*\((.*)\)\s*;\s*', text, re.S)
    if not match or match[1] in {'if', 'while', 'for', 'switch', 'return', 'sizeof'} or variable in node.uses:
        return None
    opening = flow.part.clean.find('(', node.start, node.end)
    end = close(flow.part.clean, opening)
    if end != node.end-1 or re.search(rf'\b{variable}\b', text):
        return None
    args = split_args(flow.part.clean[opening+1:end-1])
    return (match[1], opening+1+args[0][0], opening+1+args[0][1]) if args else None


def variants(source, name):
    """Move a reset into each reached call's first argument, optionally removing proven repeated resets."""
    flow = local_flow.analyze(source, name)
    if not flow:
        return []
    result = []
    predecessors = {i: set() for i in range(len(flow.nodes))}
    for i, node in enumerate(flow.nodes):
        for successor in node.successors:
            predecessors[successor].add(i)
    for decl in flow.locals:
        variable = decl['name']
        for index, node in enumerate(flow.nodes):
            initial = assignment(flow, node, variable)
            if not initial:
                continue
            prefix = flow.part.clean[:node.start].rstrip()
            if prefix[-1:] not in {';', '{', '}'}:
                continue
            value = initial[1]
            pending, seen, targets, valid = list(node.successors), set(), {}, True
            while pending and valid:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                following = flow.nodes[current]
                if len(seen) > 48 or not node.end <= following.start <= node.end+1600:
                    valid = False
                elif target := call(flow, current, variable):
                    targets[current] = target
                elif variable in following.uses or variable in following.defines or not following.successors:
                    valid = False
                else:
                    pending.extend(following.successors)
            if not valid or not targets or len({target[0] for target in targets.values()}) != 1:
                continue
            edits = [(node.start, node.end, '')]
            for _, start, end in targets.values():
                edits.append((start, end, f'({variable} = {value}, {source[start:end]})'))
            result.append(('call_initialization', edit(source, edits)))
            # Remove a repeated reset only when all incoming definitions already set this constant.
            for other, later in enumerate(flow.nodes):
                reset = assignment(flow, later, variable)
                if other == index or not reset or reset[1] != value or later.start <= node.end:
                    continue
                memo = {}
                def proves(previous, visiting, memo=memo, variable=variable, value=value):
                    if previous in memo:
                        return memo[previous]
                    if previous in visiting or len(memo)+len(visiting) > 64:
                        return False
                    prior = flow.nodes[previous]
                    if variable in prior.defines:
                        definition = assignment(flow, prior, variable)
                        result = bool(definition and definition[1] == value)
                    else:
                        result = bool(predecessors[previous]) and all(
                            proves(parent, visiting | {previous}) for parent in predecessors[previous])
                    memo[previous] = result
                    return result
                proven = bool(predecessors[other]) and all(proves(parent, set()) for parent in predecessors[other])
                if proven:
                    result.append(('call_initialization', edit(source, edits+[(later.start, later.end, '')])))
            if len(result) >= LIMIT:
                return list(dict.fromkeys(result))[:LIMIT]
    return list(dict.fromkeys(result))[:LIMIT]
