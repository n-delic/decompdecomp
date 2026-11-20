"""The normal ROM and project judges record completed hard claims.

The standard ROM fixture runs the console's build.py in the shared demo build directory.
"""
import pytest

from decompnet import consoles
from decompnet.app import tools
from tests.test_consoles import _function
from tests.test_consoles import demo as rom_fixture
from tests.test_forks import _ref, needs_tools
from tests.test_forks import fork as fork_fixture

demo = rom_fixture
fork = fork_fixture


@pytest.mark.parametrize("demo", [consoles.get("n64")], indirect=True)
def test_rom_routing_outcomes(demo):
    _, net, slug, _, symbols = demo
    name, exact = _function(net, slug, symbols, "checksum_save")
    replay(net, slug, name, exact)


@needs_tools
def test_imported_routing_outcomes(fork):
    net, slug, _ = fork
    replay(net, slug, "checksum_save", _ref("checksum_save"))


def replay(net, slug, name, exact):
    fn = net._function(name, slug)
    store = net.project_store(slug)
    with store.tx() as db:
        db.execute('UPDATE functions SET abandons=3 WHERE id=?', (fn['id'],))
    first, _ = net.local_agent('first-model-1')
    second, _ = net.local_agent('second-model-1')
    assert 'CLAIMED' in tools._claim(net, first, slug, name, '')
    near = exact.replace('0x1234', '0x1235')
    assert near != exact
    assert 'MATCH!' not in tools._compile(net, first, slug, name, near)
    net.release(first, name, '', slug)
    assert 'CLAIMED' in tools._claim(net, second, slug, name, '')
    assert 'MATCH!' in tools._compile(net, second, slug, name, exact)
    rows = store.all('SELECT profile,band,tested,won FROM claim_routing_samples WHERE function_id=? ORDER BY claimed_at', fn['id'])
    assert len(rows) == 2 and rows[0]['profile'] == rows[1]['profile']
    assert rows[0]['band'] in {'small:unscored', 'small:draft'}
    assert rows[1]['band'] == 'small:near'
    assert [(r['tested'], r['won']) for r in rows] == [(1, 0), (1, 1)]
