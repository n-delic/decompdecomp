"""Changed external inputs get another bounded search; own hill climbing cannot renew it forever."""
from decompnet.app import permuter_inputs
from tests.test_permute import AGENT, HEADER, FakeNet


def set_turns(net, turns=4):
    with net.store.tx() as db:
        db.execute('UPDATE functions SET permuted=? WHERE id=1', (turns,))


def refresh(net):
    return permuter_inputs.refresh(net, net.project_row('game'), [net.function()])


def test_existing_counters_adopted_then_header_change_renews_once():
    net = FakeNet()
    set_turns(net)
    assert refresh(net) == []
    assert net.function()['permuted'] == 4
    net._context = lambda *args: {'version': 2, 'header': HEADER + '\nextern int value;'}
    assert refresh(net) == [1]
    assert net.function()['permuted'] == 0
    set_turns(net)
    assert refresh(net) == []
    assert net.function()['permuted'] == 4


def test_claimed_input_change_waits_until_release():
    net = FakeNet()
    refresh(net)
    set_turns(net)
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(1,?,?,?)', (AGENT.id, 1, 9999999999))
        db.execute("UPDATE functions SET target=? WHERE id=1", (b'changed',))
    assert refresh(net) == []
    assert net.function()['permuted'] == 4
    with net.store.tx() as db:
        db.execute('DELETE FROM claims')
    assert refresh(net) == [1]


def test_only_external_new_source_renews_budget():
    net = FakeNet()
    for name, source, expected in [('permuter', 'better', 4), ('helper', net.function()['best_source'], 4),
                                    ('autopilot', 'new external seed', 0)]:
        set_turns(net)
        with net.store.tx() as db:
            permuter_inputs.external_seed(db, net.function(), source, name)
        assert net.function()['permuted'] == expected


def test_imported_build_content_change_not_timestamp_renews(tmp_path):
    net = FakeNet()
    net.fork_slug = lambda slug: slug
    class Forks:
        def permuter_header(self, *args):
            return HEADER
    net.forks = Forks()
    with net.store.tx() as db:
        db.execute('UPDATE projects SET root=?', (str(tmp_path),))
    manifest = tmp_path / 'build.ninja'
    manifest.write_text('flags=-O2')
    refresh(net)
    set_turns(net)
    manifest.touch()
    assert refresh(net) == []
    manifest.write_text('flags=-O3')
    assert refresh(net) == [1]
