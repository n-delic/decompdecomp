"""search_learnings: helpers search the recorded compiler-matching evidence by symptom, as hand sessions do with
tools/learnings.py (they repeated experiments the evidence already recorded)."""
import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import learnings_search, tool_policy, tools
from decompnet.network import NetworkError


def test_registered_for_matching_helpers_in_every_mode():
    assert 'search_learnings' in tools.HANDLERS and 'search_learnings' in tool_policy.MATCHING
    assert 'search_learnings' in tools.READ_ONLY and 'search_learnings' in tools.QUICK_TOOLS
    assert 'search_learnings' in tools.tool_names(False) and 'search_learnings' in tools.tool_names(True)
    assert 'search_learnings' in {t['function']['name'] for t in api_tool_schemas()}


def test_finds_an_entry_its_fix_and_families_in_this_checkout():
    root = learnings_search.repository()
    assert root is not None
    text = learnings_search.search('sched1 birthing single set', top=3)
    assert 'sched1' in text.lower() and 'fix:' in text and 'prove_chain' in text


def test_entries_written_to_a_folder_are_ranked(tmp_path):
    import shutil
    root = learnings_search.repository()
    (tmp_path / 'tools').mkdir()
    shutil.copy(root / 'tools' / 'learnings.py', tmp_path / 'tools' / 'learnings.py')
    folder = tmp_path / 'docs' / 'learnings' / 'finding'
    folder.mkdir(parents=True)
    (folder / 'frame-slot.md').write_text('---\ntitle: frame slot from a dead local\nkind: finding\ncompiler: gcc\n'
                                          'evidence: observation\ndate: 2026-11-22\n---\n\nThe fix: declare `zero` '
                                          'last; family `dead_slot_local` makes it.\n')
    text = learnings_search.search('frame slot', root=tmp_path)
    assert 'frame slot from a dead local' in text and 'dead_slot_local' in text


def test_says_what_to_do_without_a_symptom_or_the_evidence(tmp_path):
    with pytest.raises(NetworkError, match='symptom'):
        learnings_search.search('  ')
    assert learnings_search.repository(tmp_path / 'x.py') is None
