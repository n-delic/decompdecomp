"""A full audit must retain every candidate while bounding resident storage."""
from pathlib import Path

from decompnet.arch.audit_spool import CandidateSpool, editable


def test_spool_eviction_mutation_sort_and_cleanup():
    with CandidateSpool(cache_size=2) as rows:
        path=Path(rows.temp.name)
        for i in range(100):
            row=rows.setdefault((i,i+1000),{'rom_offset':i,'vram':i+1000,'incoming':[]})
            row['incoming'].append(i)
        assert len(rows)==100 and len(rows.cache)<=2
        assert (1,1001) in rows and (101,1101) not in rows
        rows.setdefault((1,1001),{})['incoming'].append('again')
        for row in editable(rows):row['checked']=True
        rows.sort(key=lambda row:(row['rom_offset']%2,row['rom_offset'],row['vram']))
        observed=list(rows)
        assert [r['rom_offset'] for r in observed]==list(range(0,100,2))+list(range(1,100,2))
        assert all(row['checked'] for row in observed)
        assert next(r for r in observed if r['rom_offset']==1)['incoming']==[1,'again']
        assert path.exists()
    assert not path.exists()


def test_spool_and_memory_audits_produce_the_same_evidence():
    from decompnet.arch.mips_gaps import audit
    from tests.test_rom_gap_audit import fixture

    image,segments,functions=fixture()
    expected=audit(image,segments,functions,full_scan=True)
    with CandidateSpool(cache_size=1) as spool:
        actual=audit(image,segments,functions,full_scan=True,candidate_store=spool)
        actual['candidates']=list(actual['candidates'])
    assert actual==expected


def test_spooled_report_keeps_every_review_id_when_saved(tmp_path):
    from decompnet.app import code_audit_reviews
    from tests.test_code_audit_reviews import fixture

    net,pid,_=fixture(tmp_path)
    with CandidateSpool(cache_size=3) as rows:
        for i in range(1101):
            rows[i*4,i*4+4096]=dict(rom_offset=i*4,vram=i*4+4096,gap_start=0,gap_end=5000,extent_bytes=4)
        saved=code_audit_reviews.record(net,pid,{'candidates':rows,'gaps':1},bytes(5000))
        assert saved['candidate_count']==1101
        first=saved['candidates'][0]['review_id']
        assert len(first)==64
        assert code_audit_reviews.review(net,'demo',first,'dismissed','Checked data.',compact=True)['candidate']['review_id']==first
        assert code_audit_reviews.page(net,pid,22,True)['candidates'][0]['rom_offset']==4400
