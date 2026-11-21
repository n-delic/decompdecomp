from types import SimpleNamespace

from decompnet import structs
from decompnet.app import cross_function_repairs as repairs
from decompnet.app import deep_handoff, experiment_evidence
from tests.test_deep_permuter import setup
from tests.test_deep_switch_loop import rows


def test_call_contract_uses_delay_slot_and_callee_entry(monkeypatch):
    source = 'int callee(int x);\nint f(int x) { return callee(x); }\n'
    asm = 'move $a0, $s0\njal callee\naddiu $a1, $zero, 7\n'
    callee_asm = ('/* 0 80000000 00851021 */ addu $v0, $a0, $a1\n'
                  '/* 4 80000004 03E00008 */ jr $ra\n'
                  '/* 8 80000008 00000000 */ nop\n')
    assert repairs.argument_reads(callee_asm) == {'a0', 'a1'}
    row = {'name': 'callee', 'matched_source': 'int callee(short x, short y) { return x+y; }'}
    monkeypatch.setattr(repairs, 'callees', lambda *args, **kwargs: [row])
    store = SimpleNamespace(all=lambda *args: [{'id': 2}])
    net = SimpleNamespace(project_store=lambda _: store, _asm_text=lambda *args: callee_asm)
    fn = {'id': 1, 'name': 'f'}
    candidates = repairs.call_variants(net, {'id': 1}, fn, source, 'typedef int s32;', asm)
    assert any(family == repairs.CALL and 'callee(x, 7)' in text and 'short x, short' in text
               for family, text in candidates)
    assert repairs.call_variants(net, {'id': 1}, fn, source, 'typedef int s32;', asm.replace('addiu $a1, $zero, 7', 'nop')) == []


def test_typed_draft_requires_independent_consistent_field_reads():
    source = ('int f(void *p) { return M2C_FIELD(p, short *, 0) + '
              'M2C_FIELD(p, int *, 4); }')
    first = structs.FieldGuess(0, 2, 's', {1, 2}, {})
    second = structs.FieldGuess(4, 4, 's', {1, 2}, {})
    layout = structs.Layout(0, [first, second], {1, 2}, [], [])
    found = SimpleNamespace(type_of=lambda key: 0 if key == (structs.ARGUMENT, 1, 0) else None,
                            layout=lambda _: layout)
    net = SimpleNamespace(structs=lambda _: found)
    fn, proj = {'name': 'f', 'vram': 1}, {'arch': 'mips'}
    [(family, text)] = repairs.record_variants(net, proj, fn, source)
    assert family == repairs.RECORD and 'short field_0' in text and 'int field_4' in text
    second.functions = {1}
    assert not repairs.record_variants(net, proj, fn, source)
    second.functions = {1, 2}
    second.kind = 'u'
    assert not repairs.record_variants(net, proj, fn, source)


def test_handoff_survives_reload_and_expires_when_the_draft_or_build_changes(monkeypatch):
    net = setup(monkeypatch)
    fn, proj = dict(net.function()), net.project_row('game')
    source = fn['best_source']
    monkeypatch.setattr(experiment_evidence, 'build_scope', lambda *_: 'build1')
    assert not deep_handoff.save(net, proj, fn, source, rows(2, 4))
    for n in range(3):
        experiment_evidence.record(net, fn, experiment_evidence.scope(net, proj, fn),
                                   'frame', source, source+str(n), 40, 40, 'same', 'same', 1)
    assert deep_handoff.save(net, proj, fn, source, rows(2, 4))
    hint = deep_handoff.brief(net, proj, fn, source)
    assert '3 tried, 3 same output' in hint and 'ordinary judge' in hint
    assert len(deep_handoff.smallest_block(rows(2, 4), 'mips')) < len(rows(2, 4))
    assert not deep_handoff.brief(net, proj, {**fn, 'best_source': source+' '}, source+' ')
    monkeypatch.setattr(experiment_evidence, 'build_scope', lambda *_: 'build2')
    assert not deep_handoff.brief(net, proj, fn, source)
