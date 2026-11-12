"""Compact edits and reversible trials must use real build/diff control flow, even without compilers."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from decompnet.agents.api import api_tool_schemas
from decompnet.app import compile_tools, tools
from decompnet.app.engine import RomNetwork
from decompnet.forks.objdiff import ObjdiffError
from decompnet.forks.project import ForkProject
from decompnet.network import NetworkError

SOURCE = 'int work(int x) { return x + 1; }\n'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path / 'workspace')
    engine = net.forks
    root = tmp_path / 'project'
    root.mkdir()
    path = root / 'unit.c'
    path.write_text(SOURCE + 'int neighbor(void) { return 4; }\n')
    base, target = root / 'build/unit.o', root / 'expected/unit.o'
    for obj in (base, target):
        obj.parent.mkdir()
        obj.write_bytes(b'original object')
    (root / 'objdiff.json').write_text(json.dumps({'units': [{'name': 'unit', 'base_path': 'build/unit.o',
        'target_path': 'expected/unit.o', 'metadata': {'source_path': 'unit.c'}}]}))
    fp = ForkProject(root)
    unit = fp.unit('unit')
    proj = {'id': 1, 'arch': 'x86_64'}
    fn = {'id': 1, 'name': 'work', 'unit': 'unit', 'status': 'open', 'best_score': 100, 'n_insns': 5}
    engine.get = lambda slug: (proj, fp, {'platform': 'pc'})
    engine._fn = lambda slug, name: fn
    engine._meta = lambda fn: {}
    engine._names = lambda meta, fn: ['work']
    agent = SimpleNamespace(id=7)
    state = SimpleNamespace(builds=0, diffs=0, broken=False, literals=False, fail=False,
                            restore_fail=False, raises=False, placements=[], linked=False, verified=True)
    monkeypatch.setattr(fp, 'linkable', lambda: state.linked)

    def build(*args):
        state.builds += 1
        text = path.read_text()
        if state.restore_fail and '+ 1' in text and state.builds > 1:
            return False, 'restore failed'
        if state.fail and '+ 2' in text:
            return False, 'candidate failed'
        base.write_bytes(text.encode())
        return True, ''

    def diff(*args):
        state.diffs += 1
        text = base.read_text()
        if state.raises and '+ 2' in text:
            raise ObjdiffError('synthetic diff failure')
        percent = 100 if '+ 2' in text else 80
        return SimpleNamespace(match_percent=percent, matched=percent == 100 and not state.literals,
            unit_scores={'work': percent, 'neighbor': 40 if state.broken and '+ 2' in text else 100},
            literals=['different data'] if state.literals and '+ 2' in text else [], render=lambda: 'synthetic diff')

    engine._build_unit = build
    engine._build_all = build
    engine._verify = lambda *args: (state.verified, 'linked bytes differ')
    monkeypatch.setattr(fp, 'diff', diff)
    def attempt(agent, slug, name, source):
        state.placements.append(source)
        _, after, out, _ = engine._place(proj, fp, unit, fn, source, ['work'])
        out.write_text(after)
        return {'text': 'ordinary keeping judge', 'score': 0, 'matched': True}
    engine.attempt = attempt
    return SimpleNamespace(net=net, engine=engine, root=root, path=path, base=base, target=target, unit=unit,
                           fp=fp, proj=proj, fn=fn, agent=agent, state=state)


def revision(rig):
    return compile_tools.snapshot(rig.engine, 'test', 'work')[2]


def recipes():
    return [{'name': 'two', 'edits': [{'old': '+ 1', 'new': '+ 2'}]}]


def test_preview_restores_source_outputs_and_database_even_for_an_exact_match(rig):
    before = rig.path.read_bytes()
    database = list(rig.net.store.conn.iterdump())
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes())
    assert 'Preview only' in result and '100' in result
    assert rig.path.read_bytes() == before and rig.base.read_bytes() == before.replace(b"\r\n", b"\n")
    assert not rig.state.placements
    assert list(rig.net.store.conn.iterdump()) == database
    assert rig.net.store.one('SELECT COUNT(*) n FROM attempts')['n'] == 0
    assert rig.net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_applying_rejudges_the_best_recipe_through_the_ordinary_attempt(rig):
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert 'Applying two' in result and rig.state.placements == [SOURCE.replace('+ 1', '+ 2')]


def test_compact_patch_reaches_the_ordinary_judge_and_returns_a_new_revision(rig):
    before = revision(rig)
    result = compile_tools.compile_patch(rig.net, rig.agent, 'test', 'work', before, recipes()[0]['edits'])
    assert 'ordinary keeping judge' in result and revision(rig) != before
    assert 'revision: ' + revision(rig) in result


@pytest.mark.parametrize('input', ['unit.c', 'layout.h', 'tools/compile.py', 'config/settings.json',
                                  'harness/unit.drv.cpp', 'objdiff.json', 'build.ninja', 'expected/unit.o'])
def test_a_changed_compile_input_rejects_the_old_revision(rig, input):
    before = revision(rig)
    path = rig.root / input
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(path.read_bytes() + b' ' if path.exists() else b'new input')
    with pytest.raises(NetworkError, match='Stale revision'):
        compile_tools.compile_patch(rig.net, rig.agent, 'test', 'work', before, recipes()[0]['edits'])
    assert not rig.state.placements and rig.state.builds == 0


@pytest.mark.parametrize('edits', [[], [{'old': '', 'new': ''}], [{'old': '+', 'new': 'x', 'other': 1}],
                                  [{'old': 'missing', 'new': 'x'}], [{'old': '+ 1', 'new': 2}]])
def test_invalid_edits_are_rejected_before_building(rig, edits):
    with pytest.raises(NetworkError):
        compile_tools.compile_patch(rig.net, rig.agent, 'test', 'work', revision(rig), edits)
    assert rig.state.builds == 0


@pytest.mark.parametrize('source', ['int x;\n' + SOURCE, SOURCE + 'int helper(void) { return 0; }\n',
                                  '#if 0\n' + SOURCE + '#endif\n', SOURCE.replace('return', '#define X 1\nreturn')])
def test_a_patch_cannot_change_shared_declarations_or_hide_the_definition(rig, source):
    with pytest.raises(NetworkError, match='complete function'):
        compile_tools.definition(rig.engine, rig.fn, source)


@pytest.mark.parametrize('state', ['fail', 'raises', 'broken'])
def test_rejected_or_failed_trials_are_restored_and_never_published(rig, state):
    before = rig.path.read_bytes()
    setattr(rig.state, state, True)
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert rig.path.read_bytes() == before and rig.base.read_bytes() == before.replace(b"\r\n", b"\n")
    assert not rig.state.placements
    assert 'Applying' not in result


def test_restoration_failure_is_reported_and_never_published(rig):
    before = rig.path.read_bytes()
    rig.state.restore_fail = True
    with pytest.raises(NetworkError, match='rebuilding it failed'):
        compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert rig.path.read_bytes() == before and not rig.state.placements


def test_every_call_rebuilds_an_unchanged_baseline(rig):
    for _ in range(2):
        result = compile_tools.preview(rig.engine, rig.agent, 'test', 'work', SOURCE)
        assert result['score'] is not None
    assert rig.state.builds == 6 and rig.state.diffs == 4


@pytest.mark.parametrize('alias', ['target', 'outside', 'symlink', 'hardlink'])
def test_forced_build_never_removes_original_or_aliased_files(rig, alias, tmp_path):
    if alias == 'target':
        rig.unit.base = rig.target
    elif alias == 'outside':
        rig.unit.base = tmp_path / 'outside.o'
        rig.unit.base.write_bytes(b'protected')
    else:
        rig.base.unlink()
        if alias == 'symlink':
            rig.base.symlink_to(rig.target)
        else:
            import os
            os.link(rig.target, rig.base)
    before = rig.target.read_bytes()
    with pytest.raises(NetworkError, match='protected'):
        compile_tools.preview(rig.engine, rig.agent, 'test', 'work', SOURCE)
    assert rig.target.read_bytes() == before and rig.state.builds == 0


def test_project_changes_during_preview_prevent_applying_a_winner(rig, monkeypatch):
    real = compile_tools.preview
    def changed(*args):
        result = real(*args)
        (rig.root / 'layout.h').write_text('changed')
        return result
    monkeypatch.setattr(compile_tools, 'preview', changed)
    with pytest.raises(NetworkError, match='inputs changed'):
        compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert not rig.state.placements


def test_tools_have_matching_mcp_and_api_definitions_and_are_not_offered_to_roms(rig):
    names = {'function_brief', 'compile_patch', 'try_recipes'}
    assert names <= set(tools.tool_names(True))
    assert not names & set(tools.tool_names(False))
    api_names = {t['function']['name'] for t in api_tool_schemas(fork=True)}
    assert names <= api_names
    mcp = tools.build_mcp(rig.net, lambda _: (rig.agent, 'test'))
    assert names <= {t.name for t in asyncio.run(mcp.list_tools())}


def test_a_literal_difference_at_one_hundred_percent_is_not_an_exact_match(rig):
    rig.state.literals = True
    result = compile_tools.preview(rig.engine, rig.agent, 'test', 'work', SOURCE.replace('+ 1', '+ 2'))
    assert not result['matched'] and result['score'] > 0
    assert '+ 1' in rig.path.read_text() and not rig.state.placements


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_recipe_apply_requires_an_explicit_boolean(rig, value):
    with pytest.raises(NetworkError, match='true or false'):
        compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=value)
    assert rig.state.builds == 0


@pytest.mark.parametrize('batch', [[], recipes() * 2, [{'name': 'preset'}],
                                 [{'name': '', 'edits': recipes()[0]['edits']}],
                                 [{'name': 'same', 'edits': [{'old': '+ 1', 'new': '+ 1'}]}]])
def test_invalid_or_duplicate_recipes_do_not_build(rig, batch):
    with pytest.raises(NetworkError):
        compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), batch)
    assert rig.state.builds == 0


def test_another_helpers_claim_is_never_expired_or_changed_by_a_preview(rig, monkeypatch):
    read = rig.net.store.one
    monkeypatch.setattr(rig.net.store, 'one', lambda sql, *args:
                        {'agent_id': 99} if sql.startswith('SELECT agent_id FROM claims') else read(sql, *args))
    before = rig.path.read_bytes()
    with pytest.raises(NetworkError, match='another helper'):
        compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert rig.state.builds == 0 and rig.path.read_bytes() == before


def test_generated_python_cache_files_do_not_invalidate_a_revision(rig):
    before = revision(rig)
    cache = rig.root / 'tools/__pycache__'
    cache.mkdir(parents=True)
    (cache / 'module.pyc').write_bytes(b'generated by Python')
    assert revision(rig) == before


def test_linked_game_verification_can_reject_a_matching_trial(rig):
    rig.unit.complete = True
    rig.state.linked = True
    rig.state.verified = False
    before = rig.path.read_bytes()
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert 'linked game differs' in result and not rig.state.placements
    assert rig.path.read_bytes() == before


def test_an_unreadable_baseline_diff_prevents_trials_without_changing_source(rig, monkeypatch):
    def fail(*args):
        raise ObjdiffError('original cannot be compared')
    monkeypatch.setattr(rig.fp, 'diff', fail)
    before = rig.path.read_bytes()
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', revision(rig), recipes(), apply=True)
    assert 'Baseline could not be judged' in result and not rig.state.placements
    assert rig.path.read_bytes() == before


def test_a_current_exact_function_is_protected_even_when_the_database_says_open(rig):
    rig.path.write_text(SOURCE.replace('+ 1', '+ 2') + 'int neighbor(void) { return 4; }\n')
    before = rig.path.read_bytes()
    result = compile_tools.preview(rig.engine, rig.agent, 'test', 'work', SOURCE)
    assert result['score'] is None and not result['matched']
    assert rig.path.read_bytes() == before


def test_unexpected_diff_exceptions_still_restore_the_original_source_and_object(rig, monkeypatch):
    real = rig.fp.diff
    def fail(*args):
        if '+ 2' in rig.base.read_text():
            raise RuntimeError('unexpected error')
        return real(*args)
    monkeypatch.setattr(rig.fp, 'diff', fail)
    before = rig.path.read_bytes()
    with pytest.raises(RuntimeError, match='unexpected error'):
        compile_tools.preview(rig.engine, rig.agent, 'test', 'work', SOURCE.replace('+ 1', '+ 2'))
    assert rig.path.read_bytes() == before and rig.base.read_bytes() == before.replace(b"\r\n", b"\n")


def test_crlf_source_is_restored_byte_for_byte_and_revisions_stay_stable(rig):
    rig.path.write_bytes((SOURCE + 'int neighbor(void) { return 4; }\n').replace('\n', '\r\n').encode())
    raw, before = rig.path.read_bytes(), revision(rig)
    fn, definition, _ = compile_tools.snapshot(rig.engine, 'test', 'work')
    assert definition == SOURCE
    result = compile_tools.try_recipes(rig.net, rig.agent, 'test', 'work', before, recipes())
    assert 'Preview only' in result
    assert rig.path.read_bytes() == raw and revision(rig) == before
