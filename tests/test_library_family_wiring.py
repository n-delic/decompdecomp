"""Every decomprepair repair module reaches a lane, so the Deep Permuter can propose it.

The Deep Permuter's beam runs `draft_repairs.variants` with no limit, so a family wired there reaches drafts and the
beam alike. Library modules (register levers, indexed records) had
`variants` but no caller anywhere in the app: no agent, draft or permuter ever saw them. This test lists the library's
modules with a `variants` function and fails for any the app never names (directly or through another library
module that calls it)."""
import pathlib
import re

import decomprepair
from decompnet.app import draft_repairs

SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'decompnet'


def test_every_library_variants_module_is_reached():
    library = pathlib.Path(decomprepair.__file__).parent
    modules = {path.stem: path.read_text() for path in library.glob('*.py')}
    app = '\n'.join(path.read_text() for path in SRC.rglob('*.py'))
    unreached = []
    for name, text in modules.items():
        if not re.search(r'(?m)^def variants\(', text):
            continue
        word = re.compile(r'\b' + name + r'\b')
        if word.search(app) or any(word.search(other) for key, other in modules.items() if key != name):
            continue
        unreached.append(name)
    assert unreached == []


def test_the_newly_wired_families_are_proposed_and_known_to_agents():
    source = """\
void f(int *p, int a, int b)
{
  int x;
  int y;
  x = a + b;
  y = b * 2;
  if (a) { p[0] = x; } else { p[0] = x; }
  p[1] = y;
}
"""
    labels = {}
    draft_repairs.variants(source, 'f', 'mips', labels=labels, limit=None)
    assert {'allocation_lever', 'identical_arm_collapse'} <= set(labels.values())
    assert {'allocation_lever', 'indexed_record_base', 'identical_arm_collapse'} <= set(draft_repairs.AGENT_FAMILIES)
