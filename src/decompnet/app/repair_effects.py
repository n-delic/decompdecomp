"""Parent-specific output feedback: defer ineffective siblings, never discard their source shapes."""
import hashlib

DEFER_INTERVAL = 8
HISTORY_LIMIT = 256
REDIRECT = {"return_contract","plain_float_slots","narrow_expression_inline"}


def key(node):
    if not node.get('parent_source') or not node.get('path'):
        return None
    return hashlib.sha256(repr((node['parent_source'], node['path'][-1])).encode()).hexdigest()


def output_scope(node):
    """Equivalent compiler outputs share feedback, while changed build/source output starts fresh."""
    if node.get('parent_output') is None or not node.get('path'):return None
    return hashlib.sha256(repr((node['parent_output'],node['path'][-1])).encode()).hexdigest()


def observe(state, node, output):
    scope = key(node)
    if scope is None or output is None or node.get('parent_output') is None:
        return
    stalls=state.setdefault('output_stalls',{})
    parent=hashlib.sha256(repr(node['parent_output']).encode()).hexdigest()
    stalls[parent]=stalls.get(parent,0)+1 if output==node['parent_output'] else 0
    while len(stalls)>HISTORY_LIMIT:del stalls[next(iter(stalls))]
    history = state.setdefault('family_no_effect', {})
    history[scope] = history.get(scope, 0)+1 if output == node['parent_output'] else 0
    shared=state.setdefault('output_no_effect',{})
    shared_scope=output_scope(node)
    shared[shared_scope]=shared.get(shared_scope,0)+1 if output==node['parent_output'] else 0
    while len(shared)>HISTORY_LIMIT:del shared[next(iter(shared))]
    while len(history) > HISTORY_LIMIT:
        del history[next(iter(history))]


def rotate(state):
    """After two unchanged builds, reserve one in eight slots for deferred exploration.

    Feedback also follows identical parent machine code across different source spellings.
    Nothing is discarded: source changes can unlock a family, so deferred probes remain fair.
    The persisted frontier's outer scope includes compiler, flags, target, headers and repair rules.
    """
    history = state.get('family_no_effect', {})
    pending = state['pending']
    shared=state.get('output_no_effect',{})
    def ineffective(node):
        local=history.get(key(node),0)
        # Preserve the third direct sibling: two identical outputs can precede the useful
        # cast/assignment placement. Equivalent *other* parents still share deferral immediately.
        if local==2:return False
        return local>=3 or shared.get(output_scope(node),0)>=2
    ordinary = [n for n in pending if not ineffective(n)]
    deferred = [n for n in pending if ineffective(n)]
    slot = state.get('counts', {}).get('matched', 0)+state.get('counts', {}).get('scored', 0)+state.get('counts', {}).get('compile_failed', 0)
    stalls=state.get('output_stalls',{})
    def redirect(node):
        output=node.get('parent_output')
        return (output is not None and (node.get('path') or [''])[-1] in REDIRECT
                and stalls.get(hashlib.sha256(repr(output).encode()).hexdigest(),0)>=3)
    # Preserve the third direct sibling and one ordinary slot in four.
    if ordinary and history.get(key(ordinary[0]),0)!=2 and slot%4!=3:
        ordinary=[n for n in ordinary if redirect(n)]+[n for n in ordinary if not redirect(n)]
    if not deferred:
        state['pending']=ordinary
        return
    if not ordinary:return
    # Advance fairness only for measured trials, not repeated proposal generation.
    first = deferred if slot % DEFER_INTERVAL == DEFER_INTERVAL-1 else ordinary
    other = ordinary if first is deferred else deferred
    state['pending'] = first[:1]+other+first[1:] if first is deferred else ordinary+deferred
