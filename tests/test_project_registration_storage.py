"""Registration is atomic; reimports use their selected project writer."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from decompnet.network import Network, NetworkError
from tests import test_project_storage as base

scoped = base.scoped


def fields(slug):
    return dict(slug=slug,name=slug,description='test',arch='mips',big_endian=False,root='.',config={})


def test_new_registration_rolls_back_identity_and_initial_function(tmp_path):
    net = Network(tmp_path/'catalog.db')
    try:
        with pytest.raises(RuntimeError):
            with net.project_registration(**fields('new')) as (db,pid):
                db.execute("INSERT INTO functions(project_id,name) VALUES(?,'first')",(pid,))
                raise RuntimeError('cancelled import')
        assert not net.store.one('SELECT 1 FROM projects')
        assert not net.store.one('SELECT 1 FROM functions')
        with net.project_registration(**fields('new')) as (db,pid):
            db.execute("INSERT INTO functions(project_id,name) VALUES(?,'first')",(pid,))
        assert net._project('new')['id'] == pid
        assert net._function('first','new')['project_id'] == pid
    finally:
        net._pool.shutdown()
        net.store.conn.close()


def test_reimport_registration_writes_only_selected_project(scoped):
    net,stores = scoped
    def reimport():
        with net.project_registration(**fields('first'),existing=1) as (db,pid):
            db.execute('UPDATE functions SET size=12 WHERE project_id=?',(pid,))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with net.store.tx():pool.submit(reimport).result(timeout=5)
    assert stores[1].one('SELECT size FROM functions')['size'] == 12
    assert stores[2].one('SELECT size FROM functions')['size'] is None
    assert not net.store.one('SELECT 1 FROM functions')
    with pytest.raises(NetworkError,match='does not match'):
        with net.project_registration(**fields('second'),existing=1):
            pytest.fail('wrong owner must not enter the registration')
