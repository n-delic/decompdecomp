"""add_repair_rule: agents save the library's declarative repair rules to the home's folder, and every lane runs them."""
import json
import os
from types import SimpleNamespace

import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import agent_tokens, repair_rules, tool_policy, tools
from decompnet.app.engine.core import Core
from decompnet.network import NetworkError

rules = pytest.importorskip('decomprepair.rules')
from decomprepair import registry  # noqa: E402

SOURCE = 'void f(void)\n{\n    i = i + 1;\n}\n'
RULE = {'schema': 1, 'id': 'plus_one', 'families': ['plus_one'],
        'sites': [{'match': r'^(?P<lead>[ \t]*)(?P<v>@IDENT@)\s*=\s*(?P=v)\s*\+\s*1\s*;', 'replace': '{lead}{v}++;'}],
        'children': {'all': 'plus_one'}}


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setenv(rules.ENVIRONMENT, '')
    monkeypatch.setattr(repair_rules, '_configured', '')
    home = tmp_path / 'home'
    fake = SimpleNamespace(workspace=home, _function=lambda function, slug: {'name': function, 'asm': '', 'unit': 'u'},
                           _project=lambda slug: {'id': 1, 'arch': 'mips'}, fork_slug=lambda slug: None,
                           _context=lambda project, unit: {'header': ''})
    monkeypatch.setattr(agent_tokens, 'candidate', lambda net, agent, slug, function: (SOURCE, 'r1'))
    return fake


def add(net, rule, function='f'):
    return repair_rules.run(net, SimpleNamespace(id=1), 'game', function, rule if isinstance(rule, str) else json.dumps(rule))


def test_registered_for_matching_helpers_in_every_mode():
    assert 'add_repair_rule' in tools.HANDLERS and 'add_repair_rule' in tool_policy.MATCHING
    assert 'add_repair_rule' in tools.QUICK_TOOLS and 'add_repair_rule' not in tools.READ_ONLY
    assert 'add_repair_rule' in tools.tool_names(False) and 'add_repair_rule' in tools.tool_names(True)
    assert 'add_repair_rule' in {t['function']['name'] for t in api_tool_schemas()}
    assert 'repair-rules' in tools.guide_text() and '"schema": 1' in tools.guide_text('repair-rules')


def test_the_guides_example_is_a_valid_rule():
    text = tools.guide_text('repair-rules')
    example = json.loads(text.split('```json')[1].split('```')[0])
    assert rules.problems(example) == []
    shipped = json.loads((rules.PACKAGED / 'abs_idiom.json').read_text())
    shipped.pop('description')
    assert example == shipped   # the guide teaches the library's own worked rule, not a copy that can drift


def test_a_home_points_the_library_at_its_rules_folder_and_keeps_the_users_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(repair_rules, '_configured', '')
    monkeypatch.setenv(rules.ENVIRONMENT, str(tmp_path / 'mine'))
    first = repair_rules.configure(tmp_path / 'a')
    assert first == str(tmp_path / 'a' / '.decomp' / 'repair-rules')
    assert os.environ[rules.ENVIRONMENT].split(os.pathsep) == [first, str(tmp_path / 'mine')]
    second = repair_rules.configure(tmp_path / 'b')    # another home replaces the first, never piles up
    assert os.environ[rules.ENVIRONMENT].split(os.pathsep) == [second, str(tmp_path / 'mine')]
    repair_rules.configure(tmp_path / 'b')
    assert os.environ[rules.ENVIRONMENT].count(second) == 1


def test_opening_a_home_configures_it(tmp_path, monkeypatch):
    monkeypatch.setattr(repair_rules, '_configured', '')
    monkeypatch.delenv(rules.ENVIRONMENT, raising=False)
    Core(tmp_path / 'db.sqlite', tmp_path / 'home')
    assert os.environ[rules.ENVIRONMENT].split(os.pathsep)[0] == str(tmp_path / 'home' / '.decomp' / 'repair-rules')


def test_a_rule_that_makes_a_child_is_saved_and_the_registry_runs_it(net):
    text = add(net, RULE)
    path = net.workspace / '.decomp' / 'repair-rules' / 'plus_one.json'
    assert json.loads(path.read_text()) == RULE
    assert 'Saved repair rule "plus_one"' in text and '+    i++;' in text and '-    i = i + 1;' in text
    assert registry.run('rule:plus_one', source=SOURCE, name='f') == [('plus_one', 'void f(void)\n{\n    i++;\n}\n')]
    assert registry.problems(registry.get('rule:plus_one')) == []


def test_the_same_id_replaces_the_rule_so_it_can_be_corrected(net):
    add(net, RULE)
    changed = json.loads(json.dumps(RULE))
    changed['sites'][0]['replace'] = '{lead}{v} += 1;'
    add(net, changed)
    folder = net.workspace / '.decomp' / 'repair-rules'
    assert [p.name for p in folder.glob('*')] == ['plus_one.json']
    assert registry.run('rule:plus_one', source=SOURCE, name='f')[0][1].count('i += 1;') == 1


def test_a_rule_that_makes_nothing_here_is_not_saved(net):
    nothing = {**RULE, 'sites': [{**RULE['sites'][0], 'match': r'^(?P<lead>[ \t]*)(?P<v>@IDENT@)\s*=\s*(?P=v)\s*-\s*1\s*;'}]}
    with pytest.raises(NetworkError, match='makes nothing'):
        add(net, nothing)
    assert not (net.workspace / '.decomp' / 'repair-rules').exists()


def test_a_bad_rule_is_refused_with_the_librarys_sentences(net):
    with pytest.raises(NetworkError, match='nothing was saved.*families'):
        add(net, {**RULE, 'families': []})
    with pytest.raises(NetworkError, match='not valid JSON'):
        add(net, '{not json')
    with pytest.raises(NetworkError, match='JSON object'):
        add(net, '   ')
    with pytest.raises(NetworkError, match='repeats'):
        add(net, {**RULE, 'sites': [{**RULE['sites'][0], 'match': '(?P<v>a+)+b'}]})


def test_a_shipped_id_and_the_wrong_processor_are_refused(net):
    with pytest.raises(NetworkError, match='shipped with the library'):
        add(net, {**RULE, 'id': 'abs_idiom'})
    with pytest.raises(NetworkError, match='for ppc; f is mips'):
        add(net, {**RULE, 'archs': ['ppc']})


def test_a_function_with_no_c_has_nothing_to_try_the_rule_on(net, monkeypatch):
    monkeypatch.setattr(agent_tokens, 'candidate', lambda net, agent, slug, function: ('', 'r0'))
    with pytest.raises(NetworkError, match='no C yet'):
        add(net, RULE)


def test_a_full_folder_takes_no_new_rule_but_still_replaces_one(net, monkeypatch):
    monkeypatch.setattr(repair_rules, 'MAX_FILES', 1)
    add(net, RULE)
    with pytest.raises(NetworkError, match='already holds 1 rules'):
        add(net, {**RULE, 'id': 'other', 'families': ['other'], 'children': {'all': 'other'}})
    add(net, RULE)


def test_a_saved_rule_is_in_the_draft_pass_and_the_deep_permuters_local_calls(net):
    """The local lane runs `decomprepair.registry.run('<module>')` in a subprocess: the rule must be listed there by name."""
    from decompnet.app import library_registry
    assert 'rule:plus_one' not in [r.module for r in library_registry.runnable('local', 'mips')]
    add(net, RULE)
    assert 'rule:plus_one' in [r.module for r in library_registry.runnable('draft', 'mips')]
    context = library_registry.draft_context(SOURCE, 'f', '', 'mips', '', ())
    calls = [call for call in library_registry.local_calls(context) if call[1] == ('rule:plus_one',)]
    assert calls == [('decomprepair.registry.run', ('rule:plus_one',), {'source': SOURCE, 'name': 'f'})]
    assert 'plus_one' in library_registry.families()
