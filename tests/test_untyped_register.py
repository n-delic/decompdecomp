"""A best that holds `register NAME = ...;` (a permuter bug's new local where a global was stored) goes back to the
best attempt without one before a deep pass searches from it."""
import sqlite3
from contextlib import contextmanager

from decompnet.app import untyped_register

DAMAGED = 'void f(void) {\n    if (g()) {\n        register D_80102ED4 = 0;\n        return;\n    }\n    D_80102ED4 = 1;\n}\n'
CLEAN = DAMAGED.replace('register D_80102ED4', 'D_80102ED4')


def test_only_an_untyped_register_declaration_counts():
    assert untyped_register.damaged(DAMAGED)
    assert not untyped_register.damaged(CLEAN)
    assert not untyped_register.damaged('void f(void) {\n    register s32 v = 0;\n    v == 1;\n}\n')
    assert not untyped_register.damaged(None)


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('CREATE TABLE functions(id INTEGER PRIMARY KEY, name TEXT, status TEXT, best_score INT, '
                              'best_source TEXT);'
                              'CREATE TABLE attempt_details(id INTEGER PRIMARY KEY, function_id INT, compiled INT, '
                              'score INT, source TEXT);')

    def all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    @contextmanager
    def tx(self):
        yield self.db
        self.db.commit()


class Net:
    def __init__(self):
        self.store = Store()

    def project_store(self, project):
        return self.store

    def cfg(self, proj):
        return {'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}

    def _judge_with(self, proj, fn, toolchain, flags, source):
        from types import SimpleNamespace
        self.judged = (toolchain, flags, source)
        return False, SimpleNamespace(score=830), ''

    def _function(self, name, slug):
        return self.store.db.execute('SELECT * FROM functions WHERE name=?', (name,)).fetchone()


def test_the_best_goes_back_to_the_best_clean_attempt():
    net = Net()
    db = net.store.db
    db.execute("INSERT INTO functions VALUES(1,'f','open',575,?)", (DAMAGED,))
    db.executemany('INSERT INTO attempt_details(function_id,compiled,score,source) VALUES(1,?,?,?)',
                   [(1, 900, CLEAN), (1, 745, CLEAN.replace('= 1;', '= 2;')), (1, 575, DAMAGED), (0, None, CLEAN)])
    fn = untyped_register.restore(net, {'id': 1, 'slug': 'game'}, net._function('f', 'game'))
    assert fn['best_score'] == 745 and fn['best_source'] == CLEAN.replace('= 1;', '= 2;')


def test_left_alone_when_clean_matched_or_nothing_clean():
    net = Net()
    db = net.store.db
    db.execute("INSERT INTO functions VALUES(1,'f','open',600,?)", (CLEAN,))
    db.execute("INSERT INTO functions VALUES(2,'g','matched',0,?)", (DAMAGED,))
    for name, score in (('f', 600), ('g', 0)):
        fn = untyped_register.restore(net, {'id': 1, 'slug': 'game'}, net._function(name, 'game'))
        assert fn['best_score'] == score


def test_without_a_clean_attempt_the_c_is_cleaned_and_judged():
    net = Net()
    db = net.store.db
    db.executescript('ALTER TABLE functions ADD COLUMN vram INT; ALTER TABLE functions ADD COLUMN meta TEXT;')
    db.execute("INSERT INTO functions(id,name,status,best_score,best_source,vram) VALUES(3,'h','open',575,?,0)",
               (DAMAGED,))
    db.execute("INSERT INTO attempt_details(function_id,compiled,score,source) VALUES(3,1,575,?)", (DAMAGED,))
    fn = untyped_register.restore(net, {'id': 1, 'slug': 'game'}, net._function('h', 'game'))
    assert fn['best_source'] == CLEAN and fn['best_score'] == 830
    assert net.judged == ('gcc-2.7.2-kmc', ['-O2'], CLEAN)


def test_the_other_permuter_bugs_count_too():
    assert untyped_register.damaged('void f(u32 *p) {\n    p[0] = (unsigned short)0xDA380003;\n}\n')
    assert untyped_register.damaged('void f(void) {\n    g((char *)((unsigned short)D) + 0xFC);\n}\n')
    assert not untyped_register.damaged('void f(u16 *p) {\n    p[0] = (u16)0x8000;\n}\n')
