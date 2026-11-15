"""Discovery continues past cached prefixes without mixing the wider similarity cursor."""
from decompnet.app.reuse_traversal import rotating
from tests.test_permute import FakeNet


def test_cursor_survives_early_return_and_wraps_in_size_order():
    net = FakeNet()
    rows = [{'id':9}, {'id':2}, {'id':5}]
    with rotating(net, 1, rows) as functions:
        assert next(functions)['id'] == 9
    with rotating(net, 1, rows) as functions:
        assert [r['id'] for r in functions] == [2,5,9]
    # A removed/matched cursor restarts safely instead of skipping new work.
    with rotating(net, 1, rows[1:]) as functions:
        assert next(functions)['id'] == 2
    try:
        with rotating(net, 1, rows) as functions:
            assert next(functions)['id'] == 5
            raise RuntimeError('cancelled')
    except RuntimeError:
        pass
    with rotating(net, 1, rows) as functions:
        assert next(functions)['id'] == 9
