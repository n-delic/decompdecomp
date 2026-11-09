from decompnet.app import param_mask_repairs as masks

SOURCE = '''void f(u16 arg0)
{
  s32 i = arg0 * 0x64;
  g(i, arg0);
}
'''


def test_narrow_parameter_becomes_a_word_masked_at_each_use():
    (family, out), = masks.variants(SOURCE, 'f')
    assert family == 'parameter_mask'
    assert out == SOURCE.replace('u16 arg0', 's32 arg0').replace('arg0 * 0x64', '(arg0 & 0xFFFF) * 0x64').replace(', arg0)', ', (arg0 & 0xFFFF))')


def test_assigned_or_wide_parameters_are_left_alone():
    assert not masks.variants(SOURCE.replace('g(i', 'arg0 = 1; g(i'), 'f')
    assert not masks.variants(SOURCE.replace('u16 arg0', 's32 arg0'), 'f')
    assert not masks.variants(SOURCE.replace('g(i, arg0)', 'g(&arg0)'), 'f')
