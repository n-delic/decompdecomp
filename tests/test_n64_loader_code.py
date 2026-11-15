"""Whole loader-bank recovery needs a closed body and independent CPU-code evidence."""
import pytest

from decompnet.consoles.n64.loader_code import complete

BASE=0x80100000
LEAF=[0x24020001,0x24420001,0x03e00008,0]
FRAME=[0x27bdffe8,0xafbf0014,0x8fbf0014,0,0x03e00008,0x27bd0018]


def jal(address):return 0x0c000000|((address>>2)&0x3ffffff)


def setup(words,calls=()):
    image=bytearray(0x8000)
    for i,w in enumerate(words):image[0x4000+i*4:0x4004+i*4]=w.to_bytes(4,'big')
    caller=[w for target in calls for w in (jal(target),0)]+[0x03e00008,0]
    for i,w in enumerate(caller):image[0x1000+i*4:0x1004+i*4]=w.to_bytes(4,'big')
    return bytes(image),[(0x1000,0x1000+len(caller)*4,0x80001000)],[(0x4000,0x4000+len(words)*4,BASE)]


def test_direct_leaf_and_adjacent_quiet_frame_are_found_in_entire_bank():
    image,taken,banks=setup(LEAF+FRAME,[BASE])
    assert complete(image,[],banks,taken)==[(0x4000,0x4010,BASE),(0x4010,0x4028,BASE+16)]


def test_new_calls_are_followed_to_completion_in_other_banks():
    words=[0x27bdffe8,0xafbf0014,jal(BASE+64),0,0x8fbf0014,0x03e00008,0x27bd0018]+[0]*9+LEAF
    image,taken,banks=setup(words,[BASE])
    result=complete(image,[],banks,taken)
    assert {r[0] for r in result}=={0x4000,0x4040}


def test_materialized_callback_can_anchor_a_closed_body():
    image,taken,banks=setup(LEAF)
    image=bytearray(image)
    words=[0x3c048010,0x24840000,0x03e00008,0]
    for i,w in enumerate(words):image[0x1000+i*4:0x1004+i*4]=w.to_bytes(4,'big')
    taken=[(0x1000,0x1010,0x80001000)]
    assert complete(bytes(image),[],banks,taken)==[(0x4000,0x4010,BASE)]


def test_quiet_frame_calls_an_independently_known_callee_and_preserves_ra():
    words=[0x27bdffe8,0xafbf0014,jal(0x80001000),0,0x8fbf0014,0x03e00008,0x27bd0018]
    image,taken,banks=setup(words,[0x80001000])
    assert complete(image,[],banks,taken)==[(0x4000,0x401c,BASE)]


@pytest.mark.parametrize('words',[FRAME,LEAF,[0x27bdffe8,0x03e00008,0x27bd0020,0]])
def test_assets_or_unbalanced_patterns_without_independent_evidence_stay_raw(words):
    image,taken,banks=setup(words)
    assert complete(image,[],banks,taken)==[]


def test_external_branch_to_an_interior_instruction_prevents_swallowing_entry():
    image,taken,banks=setup(FRAME,[BASE,BASE+8])
    assert complete(image,[],banks,taken)==[]


def test_known_code_and_unverified_banks_are_never_reinterpreted():
    image,taken,banks=setup(LEAF,[BASE])
    existing=[(0x4000,0x4010,BASE)]
    assert complete(image,existing,banks,taken)==existing
    assert complete(image,[],[],taken)==[]


def test_all_direct_callees_are_processed_without_a_candidate_batch_limit():
    count=600
    words=LEAF*count
    image,taken,banks=setup(words,[BASE+i*16 for i in range(count)])
    assert len(complete(image,[],banks,taken))==count


def test_a_quiet_frame_and_its_closed_leaf_callee_can_corroborate_each_other():
    words=[0x27bdffe8,0xafbf0014,jal(BASE+64),0,0x8fbf0014,0x03e00008,0x27bd0018]+[0]*9+LEAF
    image,taken,banks=setup(words)
    assert {r[0] for r in complete(image,[],banks,taken)}=={0x4000,0x4040}


def test_a_call_to_unmapped_or_invalid_bytes_does_not_corroborate_a_quiet_frame():
    words=[0x27bdffe8,0xafbf0014,jal(BASE+64),0,0x8fbf0014,0x03e00008,0x27bd0018]+[0]*13
    image,taken,banks=setup(words)
    assert complete(image,[],banks,taken)==[]


def test_scanning_and_call_discovery_honour_import_cancellation():
    image,taken,banks=setup(LEAF,[BASE])
    class Stop:
        def check_cancel(self):raise RuntimeError('stopped')
    with pytest.raises(RuntimeError,match='stopped'):complete(image,[],banks,taken,Stop())


def test_full_pipeline_uses_verified_banks_even_when_the_histogram_finds_no_code(monkeypatch):
    from decompnet.consoles.n64 import code

    from .test_n64_loader_mapping import fixture
    image,taken,_=fixture();image=bytearray(image)
    for i,w in enumerate(FRAME):image[0x3000+i*4:0x3004+i*4]=w.to_bytes(4,'big')
    end=taken[0][1]
    for i,w in enumerate((jal(BASE),0,0x03e00008,0)):image[end+i*4:end+i*4+4]=w.to_bytes(4,'big')
    taken=[(taken[0][0],end+16,taken[0][2])]
    monkeypatch.setattr(code,'place',lambda *args:None)
    assert code.find_segments(bytes(image),taken,log=lambda _:None)==[(0x3000,0x3018,BASE)]


def test_projects_without_verified_banks_keep_sorted_segment_output():
    assert complete(bytes(64),[(32,64,BASE+32),(0,32,BASE)],[])==[(0,32,BASE),(32,64,BASE+32)]
