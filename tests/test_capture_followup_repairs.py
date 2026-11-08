






from decompnet.app import stack_repairs


def test_scalar_declaration_swaps_reach_later_pairs():
    source = 'int f(void){'+' '.join('int v'+str(i)+';' for i in range(10))+'return 0;}'
    swaps = [s for f,s in stack_repairs.variants(source,'f') if f=='local_declaration_order']
    assert any('int v8; int v7;' in s for s in swaps)
    assert len(swaps)<=12
