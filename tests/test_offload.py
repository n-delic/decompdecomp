"""The repair families' entry points run in the proposal subprocess from any call site, returning what they returned."""
import pytest

from decompnet.app import offload
from decompnet.network import NetworkError

SOURCE = 'int f(int k) { switch (k) { case 2: return 11; case 7: return 22; default: return 0; } }'
TABLES = [{'case': 2, 'table': 100, 'target': 202, 'candidate': 201},
          {'case': 7, 'table': 100, 'target': 201, 'candidate': 202}]


@pytest.fixture
def installed():
    offload.install()
    yield offload
    offload.uninstall()


def _worker_calls(monkeypatch):
    """Count what is sent to the subprocess, without losing the real answer."""
    from decompnet.app import draft_repairs
    sent = []
    real = draft_repairs._worker_batch

    def counting(request, check, handlers=None):
        sent.append(request[:1])
        return real(request, check, handlers)
    monkeypatch.setattr(draft_repairs, '_worker_batch', counting)
    return sent


def test_an_entry_point_gives_what_it_gave_in_the_server_and_ran_in_the_child(installed, monkeypatch):
    from decomprepair import integer_address_store_drafts as drafts
    asm = '/* 0 80000000 A4400000 */  sh         $zero, 0x0($v0)\n'
    source = 'void f(void) {\n    *((*count * 0xA10) + base) = 0;\n}\n'
    original = offload.ORIGINALS['decomprepair.integer_address_store_drafts.variants']
    expected = original(source, 'f', asm)
    sent = _worker_calls(monkeypatch)
    assert drafts.variants(source, 'f', asm) == expected and expected
    assert type(drafts.variants(source, 'f', asm)[0]) is tuple  # pickled: tuples stay tuples
    assert sent == [b'P', b'P']


def test_app_repair_modules_are_offloaded_too_and_helpers_are_not(installed):
    from decompnet.app import hazard_line_repairs, stack_padding_repairs
    from decomprepair import field_struct_view_repairs, integer_repair_support, pattern_repairs
    assert hazard_line_repairs.variants.__offloaded__  # an app family's entry point
    assert not getattr(stack_padding_repairs.layout_candidates, '__offloaded__', False)  # not an entry-point name
    assert not getattr(integer_repair_support.edit, '__offloaded__', False)
    assert not getattr(pattern_repairs.close, '__offloaded__', False)
    assert field_struct_view_repairs.variants.__offloaded__ and field_struct_view_repairs.site_variants.__offloaded__


def test_arguments_that_cannot_be_pickled_run_in_the_server(installed, monkeypatch):
    from decomprepair import field_struct_view_repairs as view
    sent = _worker_calls(monkeypatch)

    class Unsendable(str):
        def __reduce_ex__(self, protocol):
            raise TypeError('no')
    assert view.variants(Unsendable(SOURCE), 'f') == []  # the function ran here, and found nothing to do
    assert sent == []


def test_an_exception_the_function_raised_is_raised_again_as_the_same_type(installed):
    from decomprepair import field_struct_view_repairs as view
    with pytest.raises(TypeError):
        view.variants(SOURCE)  # a missing argument is the caller's TypeError, not a worker failure


def test_lists_and_dicts_a_function_fills_in_place_are_filled_here_too(installed):
    from decomprepair import normalization_repairs
    choices, labels = [], {}
    original = offload.ORIGINALS['decomprepair.normalization_repairs.extend_frame']
    expected_choices, expected_labels = [], {}
    original(expected_choices, expected_labels, SOURCE, 'f', 'mips', [])
    normalization_repairs.extend_frame(choices, labels, SOURCE, 'f', 'mips', [])
    assert (choices, labels) == (expected_choices, expected_labels)


def test_the_child_and_a_disabled_server_call_the_function_directly(installed, monkeypatch):
    from decomprepair import field_struct_view_repairs as view
    sent = _worker_calls(monkeypatch)
    monkeypatch.setattr(offload, 'ENABLED', False)
    view.variants(SOURCE, 'f')
    monkeypatch.setattr(offload, 'ENABLED', True)
    monkeypatch.setattr(offload, 'IN_WORKER', True)
    view.variants(SOURCE, 'f')
    assert sent == []


def test_a_call_past_the_deadline_is_stopped_with_a_message(installed, monkeypatch):
    from decompnet.app import draft_repairs
    from decomprepair import field_struct_view_repairs as view

    def slow(request, check, handlers=None):
        raise draft_repairs._ProposalDeadline
    monkeypatch.setattr(draft_repairs, '_worker_batch', slow)
    with pytest.raises(NetworkError, match='took longer'):
        view.variants(SOURCE, 'f')


def test_a_library_reloaded_in_place_is_offloaded_again(installed):
    import importlib

    from decomprepair import field_struct_view_repairs as view
    importlib.reload(view)
    assert view.variants.__offloaded__ and 'decomprepair.field_struct_view_repairs.variants' in offload.ORIGINALS
