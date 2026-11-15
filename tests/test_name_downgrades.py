"""Cross-project placeholders must not overwrite descriptive function names."""
from types import SimpleNamespace as NS

import pytest

from decompnet.app.engine.editing import EditMixin
from decompnet.app.forkengine import ForkEngine
from decompnet.app.function_names import check_rename, generated, state
from decompnet.network import NetworkError


@pytest.mark.parametrize('new', ['func_8000C0D0', 'func_placeholder', 'func_8000C0D0_overlay_01'])
def test_descriptive_name_cannot_be_downgraded(new):
    with pytest.raises(NetworkError, match='Keep __osDevMgrMain'):
        check_rename('__osDevMgrMain', new)


def test_real_names_can_replace_generated_names_and_other_descriptive_names():
    check_rename('func_8000C0D0', '__osDevMgrMain')
    check_rename('old_name', '__osDevMgrMain')
    check_rename('__osDevMgrMain', '__osDevMgrMain')


def test_rom_rename_refuses_before_project_or_room_mutation():
    with pytest.raises(NetworkError, match='generated function name'):
        EditMixin.rename_symbol(NS(), None, 'game', '__osDevMgrMain', 'func_8000C0D0')


def test_project_rename_refuses_before_build_or_source_mutation():
    with pytest.raises(NetworkError, match='generated function name'):
        ForkEngine.rename_symbol(NS(), None, 'game', '__osDevMgrMain', 'func_8000C0D0')


@pytest.mark.parametrize('name', [
    'func_801701DC_overlay_051', 'func_801701DC_ovl_51', 'func_801701DC',
    'func_placeholder', '_func_801701DC_overlay_051', 'fn_1_16F0', 'FUN_80001000', 'sub_401000',
])
def test_generated_names_include_overlay_suffixes(name):
    assert generated(name)
    for status in ('open', 'matched'):
        assert not state({'name':name,'meta':'{}','status':status})['named']


@pytest.mark.parametrize('name',['__osDevMgrMain','render_scene','functional_update'])
def test_descriptive_names_remain_named(name):
    assert not generated(name)
    assert state({'name':name,'meta':'{}'})['named']


def test_real_symbol_name_and_rename_history_remain_independent():
    assert state({'name':'func_801701DC_overlay_051','meta':'{"real":"render_scene"}'})['named']
    value=state({'name':'func_801701DC_overlay_051','meta':'{"naming":{"original_name":"func_801701DC"}}'})
    assert not value['named'] and value['renamed']
