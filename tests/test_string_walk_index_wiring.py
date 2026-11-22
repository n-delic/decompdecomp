"""decomprepair string_walk_index (a byte string walked by a pointer written as the index loop it came from: an
82-instruction KMC font routine 360 -> 0): reached from the drafts and the Deep Permuter's local lane."""
import importlib

from decompnet.app import deep_permuter, draft_repairs, generation_calls
from tests.test_deep_parallel import Log, setup

SOURCE = """\
s32 callee();

s32 f(char *text, u16 size) {
    s32 width = 0;
    u16 length = size;
    char *cursor;
    u8 symbol;

    if (length != 0) {
        cursor = text;
        do {
            symbol = *cursor;
            if (symbol == 6) {
                cursor++;
            } else {
                width += callee(cursor);
            }
            cursor++;
        } while (cursor < (char *)((u32)length + (u32)text));
    }
    return width;
}
"""


def test_the_drafts_offer_the_family():
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    assert 'string_walk_index' in set(labels.values())
    assert 'string_walk_index' in draft_repairs.AGENT_FAMILIES
    assert any('symbol = text[i];' in text and '} while (i < length);' in text
               for text, family in labels.items() if family == 'string_walk_index')


def test_it_runs_in_the_bounded_local_lane(monkeypatch):
    from tests.test_permute import AGENT

    net = setup(monkeypatch)
    monkeypatch.setattr(generation_calls, 'IN_SERVER', True)
    proj = net.project_row('game')
    fn = {**net.function(), 'asm': ''}
    child = fn['best_source'] + '\n/* string_walk_index */'
    monkeypatch.setattr(importlib.import_module('decomprepair.string_walk_index'), 'variants',
                        lambda *args: [('string_walk_index', child)])
    search = deep_permuter.Search(net, 1, 6, Log())
    tried = []
    monkeypatch.setattr(search, '_score', lambda agent, proj, fn, child, best, fork: tried.append(child) or 20)

    assert search.operand_order_pass(AGENT, proj, fn, 180, None) == 20
    assert child in tried
