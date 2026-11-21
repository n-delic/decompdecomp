"""The member-form M2C_FIELD candidate reaches the draft repair path and compiles with the KMC compiler."""
import pytest

from decompnet import seeds, toolchains
from decompnet.app import draft_repairs
from decompnet.compiler import compile_source
from decompnet.project import compiler_config

SOURCE = seeds.M2C_MACROS + '''
extern s8 gFlag;
void f(void *a) {
    gFlag = 0;
    M2C_FIELD(a, s32 *, 0x58) = M2C_FIELD(a, s16 *, 0x7C);
}
'''


def test_the_candidate_is_proposed_labelled_and_a_known_family():
    labels = {}
    result = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    member = [text for text in result if labels.get(text) == 'field_struct_view']
    assert member and '__typeof__' in member[0]
    assert 'field_struct_view' in draft_repairs.AGENT_FAMILIES


def test_a_draft_without_fields_gets_no_candidate():
    labels = {}
    result = draft_repairs.variants('void f(void) { g(); }', 'f', 'mips', labels=labels, limit=None)
    assert 'field_struct_view' not in labels.values()
    assert not [t for t in result if '__typeof__' in t]


def test_the_member_form_compiles_under_kmc(tmp_path):
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('GCC 2.7.2 KMC is not installed')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                                                  'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2']}})
    header = 'typedef signed char s8; typedef short s16; typedef int s32; typedef unsigned char u8;'
    labels = {}
    result = draft_repairs.variants(SOURCE, 'f', 'mips', labels=labels, limit=None)
    member = next(text for text in result if labels.get(text) == 'field_struct_view')
    for text in (SOURCE, member):
        assert compile_source(cfg, header, text, trusted=True).ok
