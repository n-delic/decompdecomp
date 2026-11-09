"""Acyclic, dominated live regions for an unescaped scalar, including branch-local reads."""
import re

LIMIT = 64


def region(flow, index, variable, dominators):
    """Stop at dead edges; reject cycles, redefinitions and entries bypassing the update.

    A join is allowed only when the selected update dominates it. Unknown updates are reads in
    the shared CFG, so check their spelling too rather than assuming an unrecognized write kills.
    """
    visited, active = set(), set()
    def walk(current):
        if current in active or len(visited)>=LIMIT or index not in dominators.get(current,set()):
            return False
        if current in visited:
            return True
        node = flow.nodes[current]
        if current!=index:
            text = flow.part.clean[node.start:node.end]
            if variable in node.defines or re.search(r'\b'+re.escape(variable)+r'\s*(?:[+\-*/%&|^]=|\+\+|--)|(?:\+\+|--)\s*\b'+re.escape(variable)+r'\b',text):
                return False
        visited.add(current);active.add(current)
        for child in node.successors:
            if variable in flow.nodes[child].live and not walk(child):
                return False
        active.remove(current)
        return True
    return sorted(visited,key=lambda n:flow.nodes[n].start) if walk(index) else []
