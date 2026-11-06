import pytest

from decompnet.app import integer_lifetime_repairs, local_flow, masked_reuse_repairs


def flow(body):
    source = 'int f(int flag) { int x; int y; int width; int centre; '+body+' }\n'
    result = local_flow.analyze(source, 'f')
    assert result is not None
    return source, result


@pytest.mark.parametrize('suffix,dead', [
    ('y=2; return y;', True),
    ('if(flag) y=2; return y;', False),
    ('if(flag) y=2; else y=3; return y;', True),
    ('goto use; y=2; use: return y;', False),
    ('goto reset; use: return y; reset: y=2; goto use;', True),
    ('while(flag) { if(flag==2) break; y=2; } return y;', False),
    ('while(flag) { y=2; } return y;', False),
    ('do { y=2; } while(flag); return y;', True),
    ('switch(flag) { case 1:y=2;break; default:y=3; } return y;', True),
    ('switch(flag) { case 1:y=2;break; } return y;', False),
    ('switch(flag) { case 1:y=2; case 2:return y; default:return 0; }', False),
    ('for(x=0;x<flag;x++) { if(x) continue; y=2; } return y;', False),
])
def test_dead_values_follow_every_successor(suffix, dead):
    source, result = flow('width=flag; centre=width & 65535; '+suffix)
    assert result.dead('y', source.index('centre=')) == dead


def test_loop_backedge_keeps_a_value_live_before_a_conditional_reset():
    source, result = flow('y=1; do { centre=width & 65535; if(flag) y=2; use(y); } while(flag); return 0;')
    assert not result.dead('y', source.index('centre='))
    source, result = flow('y=1; do { centre=width & 65535; y=2; use(y); } while(flag); return 0;')
    assert result.dead('y', source.index('centre='))


def test_mask_reuse_preserves_original_value_and_later_reset():
    source = 'void f(void) { int width; int width_2; int centre; width=get(); centre=(320-(width & 65535))/2; width_2=get(); use(width_2); }'
    choices = masked_reuse_repairs.variants(source, 'f')
    assert any('width_2 = width & 65535;' in candidate and '(320-(width_2))' in candidate
               for family, candidate in choices if family == 'masked_value_reuse')
    # A read reached by a jump prevents reuse even though a reset is textually next.
    unsafe = source.replace('width_2=get(); use(width_2);', 'goto use_old; width_2=get(); use_old: use(width_2);')
    assert not any('width_2 = width & 65535;' in text for _, text in masked_reuse_repairs.variants(unsafe, 'f'))


@pytest.mark.parametrize('body', [
    'use(&(y)); width=flag; centre=(unsigned short)width; y=1; return y;',
    'volatile int z; width=flag; centre=(unsigned short)width; y=1; return y;',
    'width=flag; if(flag) centre=(unsigned short)width; y=1; return y;',
    'width=flag; goto *ptr; centre=(unsigned short)width; y=1; return y;',
])
def test_escaped_locals_and_unknown_or_unbraced_control_flow_are_not_rewritten(body):
    source='int f(int flag) { int y; int width; int centre; '+body+' }'
    assert not any('y = (unsigned short)width;' in text for _, text in masked_reuse_repairs.variants(source, 'f'))


def test_cfg_merges_and_draft_hints_keep_m2c_names_and_distinguish_overlapping_values():
    source='int f(int n) { int temp_s0; int temp_s0_2; temp_s0=read(); use(temp_s0); while(n--) { temp_s0_2=read(); use(temp_s0_2); } return 0; }'
    assert any(family == 'cfg_integer_lifetimes' for family, _ in integer_lifetime_repairs.variants(source,'f'))
    assert local_flow.hints(source,'f') == ['temp_s0 / temp_s0_2: non-overlapping scalar lifetimes; try a shared local and verify.']
    overlapping=source.replace('use(temp_s0_2);', 'use(temp_s0_2+temp_s0);')
    assert not local_flow.hints(overlapping, 'f')


@pytest.mark.parametrize('expression', [
    '2+width & 65535', 'width & 65535+3', '((unsigned short)width++)',
    '((unsigned short)width[0])',
])
def test_mask_pattern_never_splits_a_larger_expression_or_moves_postfix_effects(expression):
    source='int f(void) { int width; int scratch; int centre; width=get(); centre='+expression+'; scratch=get(); return scratch; }'
    assert not masked_reuse_repairs.variants(source, 'f')


def test_nested_switch_and_loop_edges_keep_a_value_live_on_continue():
    source, result = flow('y=1; while(flag) { centre=width & 65535; switch(flag) { case\n1: continue; default:y=2; } use(y); } return y;')
    assert not result.dead('y', source.index('centre='))


def test_machine_draft_hints_are_shown_in_briefs_without_editing_saved_source(tmp_path, monkeypatch):
    from tests.test_claims import _game
    net, _ = _game(tmp_path, [('f',10,'open')])
    source='int f(int n){int temp_s0;int temp_s0_2;temp_s0=read();use(temp_s0);while(n--){temp_s0_2=read();use(temp_s0_2);}return 0;}'
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?,best_score=20 WHERE name=?',(source,'f'))
    monkeypatch.setattr('decompnet.app.compiler_preflight.warnings',lambda *args:'')
    monkeypatch.setattr('decompnet.network.Network._brief',lambda *args:'normal brief')
    agent, _=net.local_agent('reader')
    text=net._brief(net._function('f','game'),agent,'FUNCTION')
    assert 'Draft lifetime hints' in text and 'temp_s0 / temp_s0_2' in text
    assert net._function('f','game')['best_source'] == source


@pytest.mark.parametrize('arm', ['if(flag) reset:y=2;', 'while(flag) reset:y=2;',
                               'if(flag) y=3; else reset:y=2;'])
def test_unbraced_labeled_arms_give_no_liveness_advice(arm):
    source='int f(int flag) { int y; int width; int centre; centre=width & 65535; '+arm+' return y; }'
    assert local_flow.analyze(source,'f') is None
    assert not masked_reuse_repairs.variants(source,'f')


def test_inline_record_members_are_not_function_scalar_locals():
    from decompnet.app.local_flow import analyze
    source='int f(int a){struct {int member; int other;} rec; int value; value=a;rec.member=value;return rec.member;}'
    flow=analyze(source,'f')
    assert flow is not None
    assert flow.names=={'value'}
