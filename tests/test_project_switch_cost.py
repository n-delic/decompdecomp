"""Project views must not rescan the SDK inventory or repeat an imported library report."""
from decompnet.app.engine import RomNetwork
from decompnet.app.libultra import report
from decompnet.app.twins import Print


def project(tmp_path):
    import json
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    root = tmp_path/'game';root.mkdir()
    (root/'base.z64').write_bytes(bytes.fromhex('80371240')+bytes(12))
    cfg = {'platform':'n64','rom':{'file':'base.z64'},'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':[]}}
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(1,'demo','Demo','mips',1,?,?)", (str(root),json.dumps(cfg)))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,1,'','')")
    return net,net.project_row('demo')


def test_library_report_reads_only_fingerprints_present_in_game(tmp_path,monkeypatch):
    net,proj = project(tmp_path)
    monkeypatch.setattr(net,'is_rom',lambda _: True)
    monkeypatch.setattr(net.twins,'prints',lambda _: {1:Print('wanted',[],64)})
    with net.store.tx() as db:
        db.executemany('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                       [('libultra 2.0I','mips',1,'osDemo','wanted',64)] +
                       [('libultra 2.0I','mips',1,f'other{i}',f'other{i}',64) for i in range(1000)])
    all_rows = net.store.all
    read = []
    def measured(sql,*args):
        rows = all_rows(sql,*args)
        if 'FROM sdk_signatures' in sql:
            read.append(len(rows))
        return rows
    monkeypatch.setattr(net.store,'all',measured)
    result=report(net,proj)
    assert result['signature_candidates'][0]['fingerprints']==1
    assert sum(read)==1
    monkeypatch.setattr(net.twins,'prints',lambda _: {})
    read.clear();assert report(net,proj)['signature_candidates']==[]
    assert read==[]


def test_overview_reuses_imported_library_report_including_none(tmp_path,monkeypatch):
    net,_=project(tmp_path)
    monkeypatch.setattr('decompnet.app.libultra.report',lambda *args: (_ for _ in ()).throw(AssertionError('duplicate library report')))
    for value in [None,{'header_version':'2.0I'}]:
        monkeypatch.setattr(net,'_overview',lambda *args, value=value: {'libultra':value})
        assert net.overview('demo')['libultra']==value
