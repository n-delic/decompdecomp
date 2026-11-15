"""Exact bytes across loaded projects need no verified C donor."""
from decompnet.app.engine import RomNetwork


def test_cross_project_bytes(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        for pid, arch in [(1, 'mips'), (2, 'mips'), (3, 'arm')]:
            db.execute('INSERT INTO projects(id,slug,arch,root,config) VALUES(?,?,?,?,?)',
                       (pid, str(pid), arch, str(tmp_path), '{}'))
        for pid, name, code in [(1, 'shared', b'abcd'), (1, 'same_game', b'efgh'),
                                (1, 'duplicate', b'efgh'), (1, 'different_arch', b'ijkl'),
                                (1, 'empty', b''), (2, 'other', b'abcd'), (3, 'arm', b'ijkl')]:
            db.execute('INSERT INTO functions(project_id,name,target,size,n_insns) VALUES(?,?,?,?,?)',
                       (pid, name, code, len(code), 1))
    rows = {r['name']: r for r in net.function_map('1')}
    assert [name for name, row in rows.items() if row['cross_project']] == ['shared']
    assert rows['shared']['best'] is None
    signature = net.map_signature('1')
    with net.store.tx() as db:
        db.execute('DELETE FROM functions WHERE project_id=2')
        db.execute('DELETE FROM projects WHERE id=2')
    assert net.map_signature('1') != signature
    assert not any(r['cross_project'] for r in net.function_map('1'))
