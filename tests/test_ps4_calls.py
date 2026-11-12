"""Non-leaf matching with the public compiler, real linked targets and strict objdiff identity checks."""

import io
import json
import subprocess

import pytest
from elftools.elf.elffile import ELFFile

from decompnet.app.engine import RomNetwork
from decompnet.consoles.ps4 import scratch
from decompnet.consoles.ps4.project_template.tools.compiler_flags import CL_FLAGS
from decompnet.forks import csource
from decompnet.limits import capped

from .test_ps4_workflows import ROOT, ps4_demo  # noqa: F401  module-scoped demo build and tool checks


@pytest.mark.parametrize('caller,callee,increment,same_file', [
    ('demo_local_caller', 'demo_local_callee', 7, True),
    ('demo_cross_caller', 'demo_external_callee', 11, False),
    ('demo_import_caller', 'demo_import', 13, False),
])
def test_the_app_judges_same_file_cross_file_and_import_calls(ps4_demo, tmp_path, caller, callee, increment, same_file):  # noqa: F811 fixture
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    folder = ROOT / 'projects/demo-ps4'
    built = json.loads((folder / 'build/demo.json').read_text())
    slug = net.import_rom(folder / 'build' / built['game'], confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('call-check-agent')
    _, fp, _ = net.forks.get(slug)
    caller_row, callee_row = net._function(caller, slug), net._function(callee, slug)
    assert (caller_row['unit'] == callee_row['unit']) is same_file
    unit = next(u for u in fp.units() if u.name == caller_row['unit'])
    local = csource.get((folder / 'extras.c').read_text(), callee) if same_file else ''
    (fp.root / unit.source).write_text('#include "common.h"\n' + local + '\n')
    net.claim(agent, slug, caller)
    prototypes = f'extern int {callee}(int);\nextern int wrong_callee(int);\n'
    body = f'int {caller}(int value) {{ return {callee}(value) + {increment}; }}'
    for source in [body.replace(f'{callee}(value)', 'wrong_callee(value)'),
                   body.replace(f'+ {increment}', f'+ {increment + 1}')]:
        result = net.attempt(agent, caller, prototypes + source, slug)
        assert not result['matched'], result
    result = net.attempt(agent, caller, prototypes + body, slug)
    assert result['matched'] and result['score'] == 0, result
    # A definition in this file already uses PC32; unresolved callees may use PLT32.
    raw = tmp_path / 'raw.o'
    subprocess.run(capped([str(scratch.clang_path()), *CL_FLAGS, '-I', str(fp.root / 'include'), '-c',
                           str(fp.root / unit.source), '-o', str(raw)]), check=True, timeout=60)
    def call_relocs(blob):
        elf = ELFFile(io.BytesIO(blob))
        return [(elf.get_section(section['sh_link']).get_symbol(r['r_info_sym']).name, r['r_info_type'], r['r_addend'])
                for section in elf.iter_sections() if section['sh_type'] == 'SHT_RELA'
                for r in section.iter_relocations()]
    assert (callee, 2 if same_file else 4, -4) in call_relocs(raw.read_bytes())
    assert (callee, 2, -4) in call_relocs(unit.base.read_bytes())
    assert not any(kind == 4 for _, kind, _ in call_relocs(unit.base.read_bytes()))
    assert net._function(caller, slug)['vram'] == built['symbols'][caller]
