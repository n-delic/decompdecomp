"""Cross-project placeholders must not overwrite descriptive function names."""
from types import SimpleNamespace as NS

import pytest

from decompnet.app.engine.editing import EditMixin
from decompnet.app.forkengine import ForkEngine
from decompnet.app.function_names import check_rename
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
