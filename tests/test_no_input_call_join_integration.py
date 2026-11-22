import sqlite3

from decompnet.app.residual_trials import proposals

FAMILY = 'verified_no_input_call_scan_join'

SOURCE = """\
void *list_head(s8);
void visit(void)
{
    void *current;
    void *node;
    s32 mode;
    s32 port;
    current = initial();
    port = get_port();
    mode = 0;
    if (current == NULL) {
        mode = 1;
        node = list_head(port);
        goto check_mode;
    }
    node = next(current);
    if (choose(port)) {
        mode = 1;
    }
    check_mode:
    if (mode != 0) {
        current = NULL;
        inspect(node);
    }
    current = NULL;
    report(current);
}
"""
GETTER = 'extern s32 root; s32 list_head(void) { return root; }'
ROWS = [
    ('i', 'bnez $v0, .Lnext', 'bnez $v0, .Lelse'),
    (' ', 'move $a0, $s4', 'move $a0, $s4'),
    ('o', '', 'addiu $s3, $zero, 1'),
    (' ', 'jal list_head', 'jal list_head'),
    ('o', 'addiu $s3, $zero, 1', 'move $a0, $s4'),
    (' ', 'nop', 'nop'),
    ('<', 'move $v0, $s3', ''),
    ('r', 'beqz $v0, .Lskip', 'beqz $s3, .Lskip'),
    (' ', 'nop', 'nop'),
]


def test_route_requires_a_matched_project_definition_and_word_width(tmp_path):
    with sqlite3.connect(tmp_path / 'scope.db') as database:
        database.row_factory = sqlite3.Row
        database.execute('CREATE TABLE functions (project_id, name, status, matched_source)')
        database.executemany('INSERT INTO functions VALUES (?, ?, ?, ?)', [
            ('demo', 'list_head', 'matched', GETTER),
            ('other', 'list_head', 'matched', GETTER.replace('(void)', '(int ignored)')),
        ])

        class Store:
            def all(self, query, *params):
                return database.execute(query, params).fetchall()

        class Network:
            def project_store(self, project_id):
                assert project_id == 'demo'
                return Store()

        options = {'net': Network(), 'proj': {'id': 'demo'}, 'fn': {'name': 'visit'}}
        profile = {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-mgp32', '-O2']}
        [(label, candidate)] = proposals(SOURCE, 'visit', '#define NULL ((void *) 0)', ROWS,
                                         profile=profile, **options)
        assert label == FAMILY
        assert 's32 list_head(void);' in candidate
        assert 'current = (void *) mode;' in candidate
        assert not proposals(SOURCE, 'visit', '', ROWS, profile={**profile, 'flags': []}, **options)
        database.execute("UPDATE functions SET status = 'open' WHERE project_id = 'demo'")
        assert not proposals(SOURCE, 'visit', '', ROWS, profile=profile, **options)
