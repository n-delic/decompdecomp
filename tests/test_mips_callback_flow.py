"""Homemade control-flow cases must not turn incompatible paths into evidence."""
from decompnet.arch.mips_callback_flow import trace


def run(words):
    image=b''.join(w.to_bytes(4,'big') for w in words)
    return trace(image,[dict(name='example',rom=0,vram=0x80000000,size=len(image))],'big',1000,100)


def test_spill_restore_and_branch_chain():
    # Load a callback, spill, branch past a NOP, restore and call.
    rows,limited=run([0x3c088001,0x8d090020,0xafa90010,0x10000002,0,0,
                      0x8faa0010,0x0140f809,0,0x03e00008,0])
    assert not limited and len(rows)==1
    assert rows[0]['load_address']==(0x80010020,{})
    assert [e['operation'] for e in rows[0]['evidence_chain']]==[
        'load field +32','stack spill','stack restore','indirect call']


def test_disagreeing_diamond_discards_callback():
    # Either a pointer from +32 or a different pointer from +36 reaches JALR.
    rows,_=run([0x3c088001,0x10800004,0,0x8d090020,0x10000003,0,
                0x8d090024,0,0x0120f809,0,0x03e00008,0])
    assert rows==[]


def test_call_invalidates_spilled_memory():
    rows,_=run([0x3c088001,0x8d090020,0xafa90010,0x0c000080,0,
                0x8fa90010,0x0120f809,0,0x03e00008,0])
    assert rows==[]


def test_record_pointer_keeps_field_evidence_without_inventing_address():
    rows,_=run([0x3c088001,0x8d080020,0x8d090014,0x0120f809,0,0x03e00008,0])
    assert len(rows)==1
    assert rows[0]['load_address'][1]  # Opaque pointer: cannot name a ROM cell.
    assert [e['operation'] for e in rows[0]['evidence_chain']][:2]==['load field +32','load field +20']


def test_loop_is_bounded():
    rows,limited=trace(b'\x10\x00\xff\xff'+b'\0'*4,
        [dict(name='loop',rom=0,vram=0x80000000,size=8)],'big',10,10)
    assert rows==[] and not limited


def test_unlinked_field_calls_are_reported_separately():
    from decompnet.arch.mips_indirect import consumers
    words=[0x3c088001,0x8d080020,0x8d090014,0x0120f809,0,0x03e00008,0]
    image=b''.join(w.to_bytes(4,'big') for w in words);unresolved=[]
    rows=[{'pointer_references':[{'rom_offset':0}]}]
    consumers(image,[dict(name='example',rom=0,vram=0x80000000,size=len(image))],
              rows,'big',[],[],unresolved)
    assert rows[0]['callback_consumers']==[]
    assert len(unresolved)==1 and 'not linked' in unresolved[0]['reason']
