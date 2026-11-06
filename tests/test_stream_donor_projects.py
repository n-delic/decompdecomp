"""Try one donor before opening the next, and never fingerprint unmatched imported units."""
import json
import threading
from types import SimpleNamespace

import pytest

from decompnet.app import partial_reuse, shared_reuse
from decompnet.app.twins import Print
from tests.test_similar_matches import pair as pair_fixture

pair=pytest.fixture(pair_fixture.__wrapped__)


@pytest.mark.parametrize('fork_destination',[False,True])
def test_donor_is_tried_before_next_project_load(pair,monkeypatch,fork_destination):
    net=pair
    with net.store.tx() as db:
        cfg=db.execute('SELECT config FROM projects WHERE id=1').fetchone()['config']
        db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(3,?,?,?,?,?,?)',
                   ('third','Third','mips',1,'/unused',cfg))
        db.execute("INSERT INTO functions(project_id,name,status,matched_source) VALUES(3,'third','matched','int third(){return 0;}')")
    events=[]
    monkeypatch.setattr(net,'fork_slug',lambda s:s if fork_destination and s=='destination' else None)
    monkeypatch.setattr(net,'is_rom',lambda p:True)
    monkeypatch.setattr(net.match_library,'reuse',lambda *a:0)
    monkeypatch.setattr(net.twins,'prints',lambda p:{2:Print('same',[],32)})
    monkeypatch.setattr(net.twins,'index_project',lambda p:events.append(('load',p['id'])))
    monkeypatch.setattr(net.twins,'_rom_to_rom',lambda a,p,o,*rest:events.append(('try',o['id'])) or 0)
    def discover(t,p,**kwargs):
        assert kwargs['matched_only']
        donor=kwargs['donors'][0]
        events.append(('discover',donor['id']))
        return {'donor':donor['id']}
    monkeypatch.setattr(shared_reuse,'discover',discover)
    monkeypatch.setattr(shared_reuse,'transfer',lambda *a,**kw:events.append(('try',kw['snapshot']['donor'])) or 0)
    monkeypatch.setattr(partial_reuse,'run',lambda *a,**kw:events.append(('partial',kw['projects'][0]['id'])) or 0)
    net.twins.across_games('destination')
    if fork_destination:
        assert events==[('discover',1),('try',1),('partial',1),('discover',3),('try',3),('partial',3)]
    else:
        assert events==[('load',1),('try',1),('partial',1),('discover',1),('load',3),('try',3),('partial',3),('discover',3)]


@pytest.mark.parametrize('selected',[False,True])
def test_imported_donor_scan_only_opens_matched_units_and_can_stop(pair,monkeypatch,tmp_path,selected):
    from decompnet.app import twins
    from decompnet.forks import project
    net=pair
    with net.store.tx() as db:
        db.execute('UPDATE projects SET root=? WHERE id=1',(str(tmp_path),))
        db.execute("UPDATE functions SET unit='matched-unit',meta='{}' WHERE id=1")
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(1,'unmatched','unmatched-unit','open')")
        db.execute("INSERT INTO functions(project_id,name,unit,status) VALUES(1,'later','later-unit','matched')")
    target=tmp_path/'test.o';target.write_bytes(b'synthetic object')
    opened=[]
    class Project:
        def __init__(self,root):self.root=root
        def unit(self,name):
            opened.append(name)
            assert name!='unmatched-unit'
            return SimpleNamespace(target=target)
    monkeypatch.setattr(project,'ForkProject',Project)
    stop=threading.Event()
    job=SimpleNamespace(stop=stop)
    def parse(data):
        stop.set()
        return {'old':Print('same',[],32,name='old')}
    monkeypatch.setattr(twins,'object_prints',parse)
    donor=net.project_row('source')
    found=net.twins._fork_prints(donor,job=job,**({'function_ids':[1]} if selected else {'matched_only':True}))
    assert list(found)==[1] and opened==['matched-unit']
    assert job.progress['total']==(1 if selected else 2)
    # A cancelled subset must never become the full-project in-memory fingerprint cache.
    assert donor['id'] not in net.twins._prints
    stored=json.loads((tmp_path/project.STATE/'twins.json').read_text())
    assert 'unmatched-unit' not in stored and 'later-unit' not in stored


def test_incremental_leads_accumulate_without_duplicates_or_hiding_unvisited_projects(pair,monkeypatch):
    net=pair
    with net.store.tx() as db:
        cfg=db.execute('SELECT config FROM projects WHERE id=1').fetchone()['config']
        db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(3,?,?,?,?,?,?)',
                   ('third','Third','mips',1,'/unused',cfg))
        db.execute("INSERT INTO functions(id,project_id,name,status) VALUES(3,3,'third_fn','matched')")
    monkeypatch.setattr(net.twins,'prints',lambda p:{p['id']:Print('same',[],32)})
    proj=net.project_row('destination')
    for slug in ('source','third','source'):
        current=shared_reuse.discover(net.twins,proj,donors=[net.project_row(slug)],matched_only=True)
        assert {h['source_slug'] for h in current['hints'][2]}=={slug}
    snapshot=net.twins._shared_discovery[proj['id']]
    assert {h['source_slug'] for h in snapshot['hints'][2]}=={'source','third'}
    assert len(snapshot['hints'][2])==2 and not snapshot['complete']
    full=shared_reuse.discover(net.twins,proj)
    assert len(full['hints'][2])==2 and net.twins._shared_discovery[proj['id']]['complete']
