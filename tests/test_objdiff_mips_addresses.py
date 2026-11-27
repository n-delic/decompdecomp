"""Some MIPS assemblers assign addresses even to relocatable sections."""

import json
import subprocess

import pytest
from elftools.elf.elffile import ELFFile

from decompnet import toolchains
from decompnet.forks import objdiff
from decompnet.forks.objdiff_mips import view


@pytest.fixture
def addressed_object(tmp_path):
    if not toolchains.installed('binutils-mips') or not toolchains.installed('objdiff-cli'):
        pytest.skip('needs the pinned MIPS assembler and objdiff')
    tools = toolchains.binary('binutils-mips').parent
    source = tmp_path / 'demo.s'
    source.write_text('''.set noreorder
.text
.globl demo
.ent demo
demo:
  lui $2, %hi(values)
  lw $2, %lo(values)($2)
  jr $31
  nop
.end demo
.section .rodata
values:
.word 1
''')
    plain, addressed = tmp_path / 'plain.o', tmp_path / 'addressed.o'
    subprocess.run([str(tools / 'mips-ps2-decompals-as'), '-32', '-EB', '-o', str(plain), str(source)], check=True)
    subprocess.run([str(tools / 'mips-ps2-decompals-objcopy'), '--change-section-vma', '.rodata=0x100',
                    str(plain), str(addressed)], check=True)
    return addressed


def test_view_preserves_original_and_section_contents(addressed_object, tmp_path):
    original = addressed_object.read_bytes()
    cache = {}
    normalized = view(addressed_object, tmp_path, cache)
    assert normalized != addressed_object
    assert view(addressed_object, tmp_path, cache) == normalized
    assert addressed_object.read_bytes() == original
    with addressed_object.open('rb') as f, normalized.open('rb') as g:
        before, after = ELFFile(f), ELFFile(g)
        assert before.get_section_by_name('.rodata')['sh_addr'] == 0x100
        assert after.get_section_by_name('.rodata')['sh_addr'] == 0
        for a, b in zip(before.iter_sections(), after.iter_sections()):
            if a['sh_type'] != 'SHT_NOBITS':
                assert a.data() == b.data()


def test_report_and_per_function_diff_retry_with_temporary_views(addressed_object, tmp_path):
    cfg = {'units': [{'name': 'demo', 'target_path': str(addressed_object), 'base_path': str(addressed_object)}]}
    (tmp_path / 'objdiff.json').write_text(json.dumps(cfg))
    with pytest.raises(objdiff.ObjdiffError, match='Symbol address out of section bounds'):
        objdiff._run(['diff', '-1', str(addressed_object), '-o', '/dev/null'], cwd=tmp_path)
    report = objdiff.report(tmp_path, tmp_path / 'report.json')
    assert report['measures']['matched_functions'] == 1
    result = objdiff.diff(addressed_object, addressed_object, 'demo', cwd=tmp_path)
    assert result.matched
    mapped = objdiff.diff(addressed_object, addressed_object, 'demo', cwd=tmp_path, project=tmp_path, unit='demo')
    assert mapped.matched
    assert json.loads((tmp_path / 'objdiff.json').read_text()) == cfg


def test_other_objdiff_errors_are_not_retried(monkeypatch, tmp_path):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise objdiff.ObjdiffError('another error')

    monkeypatch.setattr(objdiff, '_run', fail)
    with pytest.raises(objdiff.ObjdiffError, match='another error'):
        objdiff._run_compatible(['diff'], cwd=tmp_path)
    assert len(calls) == 1


def test_section_error_without_a_supported_object_is_preserved(monkeypatch, tmp_path):
    path = tmp_path / 'not-an-elf.o'
    path.write_bytes(b'not an ELF')
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise objdiff.ObjdiffError('Symbol address out of section bounds')

    monkeypatch.setattr(objdiff, '_run', fail)
    with pytest.raises(objdiff.ObjdiffError, match='Symbol address out of section bounds'):
        objdiff._run_compatible(['diff', '-1', str(path)], cwd=tmp_path)
    assert len(calls) == 1
