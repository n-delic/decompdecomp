"""compiler_source: helpers read the pass function behind their last rows, as hand sessions do in the installed GCC
source (sched.c birthing_insn_p, local-alloc.c qty_compare); they were told where it is but could not read it."""
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import compiler_source, tool_policy, tools
from decompnet.network import NetworkError

SCHED = '''\x0c
/* Return nonzero if PAT is the pattern of an insn which makes a
   register live.  */

static int
birthing_insn_p (pat)
     rtx pat;
{
  int j;
  return 0;
}

#define MAX_THING(x) \\
  ((x) + 1)
'''


def test_registered_for_every_console_and_mode():
    assert 'compiler_source' in tools.HANDLERS and 'compiler_source' in tool_policy.MATCHING
    assert 'compiler_source' in tools.READ_ONLY
    assert 'compiler_source' in tools.tool_names(False) and 'compiler_source' in tools.tool_names(True)
    assert 'compiler_source' in {t['function']['name'] for t in api_tool_schemas()}


def test_reads_a_definition_with_its_comment_and_a_macro(tmp_path):
    (tmp_path / 'sched.c').write_text(SCHED)
    text = compiler_source.read('gcc-x', 'birthing_insn_p', root=tmp_path)
    assert 'sched.c:2' in text  # a form feed is no line break (GCC sources hold them) and 'makes a' in text and 'static int' in text and 'return 0;' in text
    assert 'MAX_THING' not in text
    macro = compiler_source.read('gcc-x', 'MAX_THING', root=tmp_path)
    assert '((x) + 1)' in macro


def test_bounded_and_says_what_to_do(tmp_path, monkeypatch):
    (tmp_path / 'big.c').write_text('int\nhuge (x)\n{\n' + '  x++;\n' * 5000 + '}\n')
    text = compiler_source.read('gcc-x', 'huge', root=tmp_path)
    assert 'cut' in text and len(text) < compiler_source.MAX_CHARS + 600
    with pytest.raises(NetworkError, match="isn't defined"):
        compiler_source.read('gcc-x', 'nothing_here', root=tmp_path)
    with pytest.raises(NetworkError, match='Name an installed compiler'):
        compiler_source.read('gcc-x', '../etc/passwd', root=tmp_path)
    from decompnet import toolchains
    monkeypatch.setattr(toolchains, 'source_for', lambda c: None)
    with pytest.raises(NetworkError, match='No source'):
        compiler_source.read('ido-7.1', 'anything')


def test_the_installed_kmc_source_when_present():
    from decompnet import toolchains
    if toolchains.source_for('gcc-2.7.2-kmc') is None:
        pytest.skip('gcc-2.7.2-kmc source not installed')
    text = compiler_source.read('gcc-2.7.2-kmc', 'qty_compare')
    assert 'local-alloc.c' in text and 'qty_compare' in text
