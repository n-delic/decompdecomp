"""Parent-specific output feedback: defer ineffective siblings, never discard their source shapes."""
import hashlib


def key(node):
    if not node.get('parent_source') or not node.get('path'):
        return None
    return hashlib.sha256(repr((node['parent_source'], node['path'][-1])).encode()).hexdigest()


def observe(state, node, output):
    scope = key(node)
    if scope is None or output is None or node.get('parent_output') is None:
        return
    history = state.setdefault('family_no_effect', {})
    history[scope] = history.get(scope, 0)+1 if output == node['parent_output'] else 0
    while len(history) > 256:
        del history[next(iter(history))]


def rotate(state):
    """After two unchanged builds, reserve one in three slots for deferred exploration.

    Only already queued siblings of this exact parent are affected. Persist the count with the
    frontier, whose outer scope includes compiler, flags, target, headers and repair rules.
    """
    history = state.get('family_no_effect', {})
    pending = state['pending']
    ordinary = [n for n in pending if history.get(key(n), 0) < 2]
    deferred = [n for n in pending if history.get(key(n), 0) >= 2]
    if not ordinary or not deferred:
        return
    # Advance fairness only for measured trials, not repeated proposal generation.
    slot = state.get('counts', {}).get('matched', 0)+state.get('counts', {}).get('scored', 0)+state.get('counts', {}).get('compile_failed', 0)
    first = deferred if slot % 3 == 2 else ordinary
    other = ordinary if first is deferred else deferred
    state['pending'] = first[:1]+other+first[1:] if first is deferred else ordinary+deferred
