"""Homemade scalar phases and optional private, ROM-backed allocation replay."""













from decompnet.app import integer_abi

HEADER = 'typedef long s32; typedef unsigned long u32; typedef short s16;'


SOURCE = '''int f(int a,int b) { int value; int later; int unused;
value=3; value += a+b; observe(value); later=9; observe(later); return value; }'''


SOURCE = SOURCE.replace('int unused;','int answer;').replace('observe(value);','answer=value;').replace('return value;','return answer;')


ROWS = [('r','addu $s0, $t0, $v0','addu $s0, $v0, $t0')]


def test_abi_resolution_never_uses_host_long_width():
    for arch in integer_abi.ARCHES:
        assert integer_abi.scalar('s32',HEADER,arch)==(32,True)
        assert integer_abi.scalar('u32',HEADER,arch)==(32,False)
        assert integer_abi.scalar('alias',HEADER+' typedef s32 alias;',arch)==(32,True)
    for context in [HEADER+'typedef short s32;', '#define s32 int', 'typedef volatile int s32;', 'typedef other s32;']:
        assert integer_abi.scalar('s32',context,'mips') is None
    assert integer_abi.scalar('long',HEADER,'x86_64') is None
