"""What a small local model (a 4B model on an API helper) got wrong because of what the tools told it."""
import pytest

from decompnet import network
from decompnet.app import tools
from tests.test_claims import _game


def test_function_list_with_a_repair_note_says_what_was_wrong_with_function(tmp_path):
    # It put the callees and data in `function`; the old error read as being about the note, and it gave up.
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f')
    with pytest.raises(network.NetworkError) as error:
        tools._compile(net,a,'game','f, g, D_80109688','int f(){return 0;}',repair_note='Tried a cast.')
    said=str(error.value)
    assert '3 things' in said and 'D_80109688' in said and 'only the one function' in said
    assert 'Nothing was compiled' in said


def test_release_without_a_compile_marks_its_notes(tmp_path):
    # Its notes claimed "no better score in 6 compiles" for a function it never compiled.
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    net.claim_row(a,'game','f')
    said=tools.call(net,a,'game','release_function',{'function':'f','move_on':True,
                                                     'notes':'No better score in 6 compiles.'})
    assert 'Released f' in said
    text=net.store.one('SELECT text FROM notes ORDER BY id DESC LIMIT 1')['text']
    assert text.startswith('[Released without compiling it once') and 'No better score in 6 compiles.' in text
