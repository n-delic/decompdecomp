"""Every repair family together finishes in moments on a long run of simple statements. decomprepair 0.5.27's
signed-division form backtracked exponentially over such a run: a deep pass spent 2.75 hours in one N64 function's
proposal generation (no compile was running), and the sweep it was part of never reached seventeen functions."""
import time

from decompnet.app import draft_repairs

BODY = ''.join(f'    a{i} = b{i} + c{i};   \n' for i in range(40))
SOURCE = 'void f(int x) {\n    int v;\n    v = x;\n' + BODY + '    if (v < 0) {\n        v = y;\n    }\n' + BODY + '}\n'


def test_all_families_finish_on_a_long_statement_run():
    started = time.monotonic()
    draft_repairs.variants(SOURCE, 'f', 'mips', '', ('-O2',), {}, limit=None)
    assert time.monotonic() - started < 30
