"""Repairs stay bounded and source-scoped; profile matches are still made by the normal judge."""


import pytest

from decompnet.app import operand_order_repairs


@pytest.mark.parametrize('expression', ['get() != b', 'a++ != b', 'a != b++', 'a != b()', 'a + c != b'])
def test_no_partial_side_effecting_comparison_rewrites(expression):
    assert not operand_order_repairs.variants('int f(void){return '+expression+';}', 'f')


def test_volatile_macros_and_floating_products_are_not_reordered():
    assert not operand_order_repairs.variants('int f(void){return a != b;}', 'f', 'extern volatile int a;')
    assert not operand_order_repairs.variants('#define a read()\nint f(void){return a != b;}', 'f')
    assert not operand_order_repairs.variants('float f(float a,float b,float c){return a*b*c;}', 'f')
    got = operand_order_repairs.variants('u32 f(u32 a,u32 b,u32 c){return a*b*c;}', 'f')
    assert len(got) == 2 and all(family == 'integer_product_order' for family,_ in got)
