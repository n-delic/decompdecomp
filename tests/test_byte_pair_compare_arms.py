"""decomprepair 0.5.374's byte-pair, packed-constant and compare-into-arms families reach the draft repair path, and
what they write compiles under the KMC compiler."""
import pytest

from decompnet import toolchains
from decompnet.app import draft_repairs
from decompnet.compiler import compile_source
from decompnet.project import compiler_config

HEADER = ('typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16; '
          'typedef int s32; typedef unsigned u32;\nextern u8 flat[]; extern u8 grid[];')
SOURCE = '''inline static s32 low_byte(u32 arg0)
{
  return (s32) (arg0 & 0xFF);
}

inline static u8 find(u8 *obj, s32 key)
{
  u8 a;
  u8 b;
  u32 x;
  u32 y;
  if (key == (-1))
    return 0;
  a = obj[0x10];
  b = obj[0x11];
  y = key;
  if (b == 0xFF)
    x = flat[(u8)y];
  else
  {
    u8 *row = grid + (b << 4);
    x = row[a];
  }
  if (x == (u8)y)
    return 1;
  return 0;
}

s32 f(u8 *obj, u8 *p, s32 pitch, s32 r)
{
  u32 hi;
  u32 lo;
  u32 w = *((u32 *) p);
  lo = w >> 8;
  hi = w >> 24;
  if (hi < (lo & 0xFF))
  {
    u32 tmp = lo;
    lo = hi; hi = tmp;
  }
  r = r % ((((s32) (hi & 0xFF)) - low_byte(lo)) + 1);
  *((s32 *) p) = (((pitch & 0xFF) << 16) | (((lo & 0xFF) + r) << 8)) | 0x19;
  return find(obj, *((s32 *) (obj + 0x60)));
}
'''
FAMILIES = ('compare_into_arms', 'masked_byte_pair', 'or_constant_stage')


def _proposals():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    return result, labels


def test_the_families_are_proposed_labelled_and_known():
    _, labels = _proposals()
    assert set(FAMILIES) <= set(labels.values())
    for family in FAMILIES:
        assert family in draft_repairs.AGENT_FAMILIES


def test_each_proposal_compiles_under_kmc(tmp_path):
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('GCC 2.7.2 KMC is not installed')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                                                  'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    result, labels = _proposals()
    for family in FAMILIES:
        text = next(t for t in result if labels.get(t) == family)
        compiled = compile_source(cfg, HEADER, text, trusted=True)
        assert compiled.ok, compiled.log
