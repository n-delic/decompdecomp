"""Bulk names reject stale audits and use one checked engine transaction."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from decompnet.app import symbol_names
from decompnet.network import NetworkError


def network():
    names = {'func_80000000': 0x80000000, 'func_80000010': 0x80000010, 'taken': 0x80000020}
    return NS(project_row=lambda _: {'id': 3}, is_rom=lambda _: True,
              fork_slug=lambda _: None, links={}, symbols=lambda _: NS(resolve=names.get),
              local_agent=lambda _: ('agent', ''), rename_symbols=Mock(return_value='✅ renamed'),
              progress=NS(sync=Mock()))


def entries():
    return [dict(old='func_80000000', new='Player_Update', address=0x80000000),
            dict(old='func_80000010', new='Player_Draw', address=0x80000010)]


def test_batch_uses_one_regression_operation_and_flushes_saved_progress():
    net = network(); log = NS(progress=Mock())
    assert symbol_names.apply(net, 'game', entries(), 'source evidence', log)['renamed'] == 2
    net.rename_symbols.assert_called_once_with('agent', 'game',
        [('func_80000000', 'Player_Update'), ('func_80000010', 'Player_Draw')],
        'source evidence', progress=log.progress)
    net.progress.sync.assert_called_once_with('game', force=True)


@pytest.mark.parametrize('change', [lambda r: r[0].update(address=3),
    lambda r: r[0].update(new='taken'), lambda r: r[1].update(new='Player_Update'),
    lambda r: r[1].update(old='func_80000000'), lambda r: r[0].update(address=True),
    lambda r: r[0].update(extra='no')])
def test_preflight_rejects_conflicts_or_stale_mapping_without_writes(change):
    net = network(); rows = entries(); change(rows)
    with pytest.raises(NetworkError):symbol_names.prepare(net, 'game', rows)
    net.rename_symbols.assert_not_called()


def test_rejected_regression_does_not_publish_progress():
    net = network(); net.rename_symbols.return_value = '❌ REJECTED: match changed'
    with pytest.raises(NetworkError, match='REJECTED'):
        symbol_names.apply(net, 'game', entries(), '', NS())
    net.progress.sync.assert_not_called()


def test_room_is_not_silently_modified_locally():
    net = network(); net.links = {'game': object()}
    with pytest.raises(NetworkError, match='local ROM'):symbol_names.prepare(net, 'game', entries())
