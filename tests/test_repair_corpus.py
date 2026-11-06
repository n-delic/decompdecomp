"""Sampling preserves distinct build/change evidence and records repeated successes cheaply."""
import copy
import sqlite3

from decompnet.repair_corpus import SCHEMA, bucket, retain


def example(kind='deep local repair'):
    return {'before':{'score':20},'after':{'settings':{'mode':'rom','platform':'n64',
            'compiler':{'toolchain':'gcc','flags':['-O2']},'context_sha256':'header','scoring_revision':'judge'}},
            'comparable_settings':True,'provenance':{'kind':kind,'family':'unsigned_shift'}}


def test_repeated_automatic_repairs_keep_one_sample_and_counts():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SCHEMA+'; CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
    evidence=example()
    assert retain(db,evidence,'int a;','unsigned a;',1)
    assert not retain(db,evidence,'int b;','unsigned b;',2)
    row=dict(db.execute('SELECT * FROM repair_capture_groups').fetchone())
    assert (row['successes'],row['retained'],row['first_at'],row['last_at'])==(2,1,1,2)


def test_manual_examples_have_small_diverse_sample():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript(SCHEMA+'; CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
    evidence=example('agent')
    assert [retain(db,evidence,'int a;','unsigned a;',n) for n in range(5)]==[True,True,True,False,False]


def test_context_literals_family_and_sequence_remain_distinct():
    evidence=example();base=bucket(evidence,'return 1;','return 2;')[0]
    assert base!=bucket(evidence,'return 1;','return 3;')[0]
    for key,value in [('context_sha256','other'),('platform','ps2'),('scoring_revision','new'),('compiler',{'toolchain':'gcc','flags':['-O0']})]:
        changed=copy.deepcopy(evidence);changed['after']['settings'][key]=value
        assert bucket(changed,'return 1;','return 2;')[0]!=base
    for key,value in [('family','other'),('path',['a','b'])]:
        changed=copy.deepcopy(evidence);changed['provenance'][key]=value
        assert bucket(changed,'return 1;','return 2;')[0]!=base


def test_incomplete_and_imported_evidence_is_not_discarded():
    evidence=example()
    assert bucket(evidence,None,'int a;')[0] is None
    evidence['comparable_settings']=False
    assert bucket(evidence,'int a;','unsigned a;')[0] is None
    evidence=example();evidence['after']['settings']['mode']='project'
    assert bucket(evidence,'int a;','unsigned a;')[0] is None
    assert bucket(example(),'x'*65537,'unsigned a;')[0] is None


def test_configurable_limits_zero_disabled_and_reenable():
    import json

    from decompnet.repair_corpus import KEY
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript(SCHEMA+'; CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
    def configure(enabled,automatic,manual):
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(
            dict(enabled=enabled,automatic_limit=automatic,manual_limit=manual))))
    configure(True,0,2)
    assert not retain(db,example(),'int a;','unsigned a;',1)
    assert retain(db,example('agent'),'int a;','unsigned a;',2)
    assert retain(db,example('agent'),'int a;','unsigned a;',3)
    assert not retain(db,example('agent'),'int a;','unsigned a;',4)
    configure(False,0,0)
    assert retain(db,example(),'int a;','unsigned a;',5)
    configure(True,2,2)
    assert retain(db,example(),'int a;','unsigned a;',6)
    assert not retain(db,example(),'int a;','unsigned a;',7)


def test_settings_persist_and_reject_invalid_values(tmp_path):
    import pytest

    from decompnet.repair_corpus import DEFAULTS, options, read_options, save
    from decompnet.store import Store
    path=tmp_path/'settings.db'
    store=Store(path)
    assert read_options(store)==DEFAULTS
    chosen=dict(enabled=False,automatic_limit=5,manual_limit=8)
    assert save(store,chosen)==chosen
    store.conn.close()
    store=Store(path)
    assert read_options(store)==chosen
    for bad in ({'enabled':'yes'},{'automatic_limit':True},{'manual_limit':-1},{'manual_limit':1001},None):
        with pytest.raises(ValueError):options(bad)
    assert read_options(store)==chosen
    store.conn.close()
