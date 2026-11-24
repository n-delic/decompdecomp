"""The target's jump tables reach the library's switch arm families, and the judge worker names the cases whose table
entry differs when every instruction is exact (a score of 1 whose diff has no rows)."""
import json
import stat
import sys
import textwrap
from types import SimpleNamespace

import pytest

from decompnet import hosted_judge
from decompnet.app import draft_repairs, library_registry
from tests.test_hosted import project
from tests.test_match_skill_fast_judge import load

SOURCE = '''s32 f(void *a) {
    s32 t;
    s32 v;
    t = g();
    switch (t) {
    case 0x12:
        v = 0x13;
block_4:
        if (a == v) {
        case 0x6:
        case 0x8:
            return 1;
        }
        return 0;
    case 0x20:
        v = 0x21;
        goto block_4;
    default:
        return 0;
    }
}
'''
TABLE = (6, [0xF4, 0xFC, 0xF4] + [0xFC] * 9 + [0xE0] + [0xFC] * 13 + [0xE8])


def host_children(**options):
    labels = {}
    draft_repairs.variants(SOURCE, 'f', 'mips', '', (), labels, limit=None, header='typedef int s32;', **options)
    return [family for family in labels.values() if family == 'switch_join_host']


def test_the_draft_context_carries_the_jump_tables_as_plain_lists():
    context = library_registry.draft_context(SOURCE, 'f', '', 'mips', '', (), tables=[TABLE])
    assert context['tables'] == [[6, TABLE[1]]]
    assert library_registry.draft_context(SOURCE, 'f', '', 'mips', '', ())['tables'] == []
    local = dict((call[0], call[2]) for call in library_registry.local_calls(context))
    json.dumps(local)  # the local lane's subprocess takes them as JSON


def test_a_draft_with_the_jump_table_gets_the_arm_the_table_names_and_fewer_orders():
    pytest.importorskip('decomprepair.switch_join_arms')
    without, with_table = host_children(), host_children(tables=[TABLE])
    assert len(without) == 6 and len(with_table) == 3


def test_the_worker_names_the_cases_whose_jump_table_entry_differs(tmp_path, monkeypatch):
    net = project(tmp_path)
    rows = [{'table': 0x80000510, 'case': 18, 'target': 0x8000F2E0, 'candidate': 0x8000F2E8},
            {'table': 0x80000510, 'case': 32, 'target': 0x8000F2E8, 'candidate': 0x8000F2E0}]
    diff = SimpleNamespace(score=1, render=lambda **kw: 'no rendered differences', rows=[])

    def judge_with(proj, fn, toolchain, flags, source):
        net._last_judge_checks = {'jump_tables': rows}
        return False, diff, 'your data for jtbl_80000510 differs from the ROM'
    monkeypatch.setattr(net, '_judge_with', judge_with)
    target = bytes([1, 3, 0xE0, 8]).hex()
    request = {'slug': 'game', 'function': {'name': 'f1', 'vram': 0x80001100, 'target': target}, 'diff': True,
               'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}, 'source': 'int f1(void){return 0;}'}
    reply = hosted_judge.judge(net, request)
    assert reply['score'] == 1 and reply['jump_tables'] == rows
    assert 'Linked jump-table differences' in reply['diff'] and '18 | 0x8000F2E0 | 0x8000F2E8' in reply['diff']
    monkeypatch.setattr(net, '_judge_with', lambda proj, fn, toolchain, flags, source: (False, diff, ''))
    assert 'jump_tables' not in hosted_judge.judge(net, request)  # a judge without checks adds nothing


STUB = textwrap.dedent("""\
    #!{python}
    import json, sys
    for line in sys.stdin:
        r = json.loads(line)
        if r["op"] == "function":
            out = {{"name": r["name"], "vram": 4096, "target": "03e00008", "header": "",
                   "compiler": {{"toolchain": "t", "flags": []}}}}
        else:
            out = {{"score": 1, "detail": "", "rows": [], "jump_tables": [{{"table": 1, "case": 18, "target": 2, "candidate": 3}}]}}
        print(json.dumps(out), flush=True)
""")


def test_fast_judge_keeps_the_jump_table_rows_prove_chain_hands_to_the_relabel_family(tmp_path, monkeypatch):
    path = tmp_path / 'decomp'
    path.write_text(STUB.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv('DECOMP_BIN', str(path))
    judge = load('fast_judge').Judge('game', 'f1')
    try:
        assert judge.jump_tables == []
        assert judge.rows('int f1(void){return 0;}') == (1, [])
        assert judge.jump_tables == [{'table': 1, 'case': 18, 'target': 2, 'candidate': 3}]
    finally:
        judge.close()
