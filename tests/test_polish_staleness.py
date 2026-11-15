"""Polishing refuses a rewrite whose matching C changed meanwhile, and only then.

Called without `original`, the check compared the stored C with None, so every such call said the source changed."""
import pytest

from decompnet.network import NetworkError

from . import test_match_commits

automatic = test_match_commits.automatic
progress = test_match_commits.progress
match = test_match_commits.match


def test_polish_without_original_compares_the_c_the_caller_read(automatic):
    net, _, _, agent = automatic
    match(automatic)
    fn = net._function('f', 'game')
    source = '/* Returns at once. */ void f(void) { return; }'
    assert net.readability._readable(net.project_row('game'), fn, agent, source).startswith('✅ f is readable now')
    assert net._function('f', 'game')['matched_source'] == source


def test_polish_without_original_still_refuses_a_changed_source(automatic):
    net, _, _, agent = automatic
    match(automatic)
    fn = net._function('f', 'game')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source='void f(void) { /* changed meanwhile */ return; }' WHERE id=?",
                   (fn['id'],))
    with pytest.raises(NetworkError, match='matching source changed while polishing'):
        net.readability._readable(net.project_row('game'), fn, agent, '/* Polished. */ void f(void) { return; }')
    assert 'changed meanwhile' in net._function('f', 'game')['matched_source']
