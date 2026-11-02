"""Resumable proposal pages and bounded, evidence-led repair combinations."""
import hashlib


class Page(list):
    def __init__(self, items=(), *, more=False):
        super().__init__(items)
        self.more = more


FOLLOWUP_WINDOW = 16

COMPLEMENTS = {
    'loop_weight_scope': ('cfg_equal_value_increment', 'equal_value_increment'),
    'cfg_equal_value_increment': ('loop_weight_scope',),
    'equal_value_increment': ('loop_weight_scope',),
    'paired_operand_reuse': ('affine_counter_guard', 'affine_counter'),
    'affine_counter': ('paired_operand_reuse', 'affine_counter_guard'),
    'affine_counter_guard': ('paired_operand_reuse',),
}


def order(families, path):
    preferred = COMPLEMENTS.get(path[-1], ()) if path else ()
    return sorted(families, key=lambda f: preferred.index(f) if f in preferred else len(preferred))


def page(proposals, source, cursors, limit):
    """Cursor belongs to the exact source and ordered proposal pool; outer frontier owns build scope."""
    if limit < 1:
        raise ValueError('A repair page needs a positive limit.')
    proposals = list(dict.fromkeys(proposals))
    digest = hashlib.sha256(source.encode())
    for family, candidate in proposals:
        digest.update(repr((family, hashlib.sha256(candidate.encode()).hexdigest())).encode())
    key = digest.hexdigest()
    start = cursors.get(key, 0)
    stop = min(len(proposals), start+limit)
    cursors[key] = stop
    return Page(proposals[start:stop], more=stop < len(proposals))
