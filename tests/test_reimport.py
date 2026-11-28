"""Reimporting a ROM project replaces its analysis while preserving valid work."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.repository_git import git
from decompnet.consoles import Console
from decompnet.network import NetworkError
from decompnet.rom import Picked, split


def test_reimport_preserves_moved_symbol_bindings_in_saved_c():
    from decompnet.app.engine.importing import _preserve_moved_references
    from decompnet.rom.symbols import Symbol, SymbolTable

    symbols = SymbolTable()
    symbols.add(Symbol('old_func', 0x80002000, 8, 'func'))
    symbols.add(Symbol('reimport_ref_80001000', 0x80003000, 4, 'data'))
    source = 'extern int old_func; /* old_func */\nint f(void) { return old_func; }\n'
    result = _preserve_moved_references(source, {'old_func': 0x80001000}, symbols)
    assert result == ('extern int reimport_ref_80001000_1; /* old_func */\n'
                      'int f(void) { return reimport_ref_80001000_1; }\n')
    assert symbols.by_name['reimport_ref_80001000_1'].address == 0x80001000
    assert symbols.by_name['old_func'].address == 0x80002000
    before = set(symbols.by_name)
    assert _preserve_moved_references('/* old_func */ "old_func"', {'old_func': 0x80001000}, symbols) == '/* old_func */ "old_func"'
    assert set(symbols.by_name) == before


def test_simultaneous_reimports_recheck_only_their_own_saved_sources(monkeypatch):
    from decompnet.app.engine.importing import ImportMixin

    barrier = threading.Barrier(2)
    seen = {}
    class Fake(ImportMixin):
        def _project(self, slug):
            return {'id': slug, 'config': '{"kind":"rom"}'}
        def import_split(self, console, picked, *, reimport_slug, rejudge_out=None, **kwargs):
            item = (reimport_slug, f'{reimport_slug} source', None)
            if rejudge_out is None:  # The old shared queue made the race reproducible.
                self._reimport_rejudge = [item]
            else:
                rejudge_out.append(item)
            barrier.wait(timeout=5)
        def _judge_kept_matches(self, slug, kept, log):
            seen[slug] = kept[:]
        def project_store(self, pid):
            return SimpleNamespace(one=lambda *args: {'functions': 1, 'matched': 0})
    net = Fake()
    monkeypatch.setattr('decompnet.app.engine.importing.consoles.identify',
                        lambda _: SimpleNamespace(read_game=lambda data, name: data))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(net.reimport_rom, slug, b'game', confirmed=True, log=lambda _: None)
                   for slug in ('one', 'two')]
        for future in futures:
            future.result(timeout=10)
    assert seen == {'one': [('one', 'one source', None)], 'two': [('two', 'two source', None)]}


def test_reimport_rejudge_releases_only_its_own_claims(tmp_path, monkeypatch):
    from decompnet.app.engine import importing
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        for index, name in enumerate(('first', 'second', 'user_claim')):
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                       'VALUES(?,?,?,?,?,?,?,?)', (pid, name, 4, 1, b'\0'*4, '[]', 'open', 0x80000000+4*index))
    agent = net.attribution_agent('saved-author')
    store = net.project_store(pid)
    seen = []
    def attempt(who, name, source, slug):
        fn = net._function(name, slug)
        assert store.one('SELECT agent_id FROM claims WHERE function_id=?', fn['id'])['agent_id'] == who.id
        seen.append(name)
        return {'matched': False}
    net.attempt = attempt
    net._judge_kept_matches('game', [('first', 'int x;', agent.id), ('second', 'int y;', agent.id)], lambda _: None)
    assert seen == ['first', 'second']
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0
    net.local_agent = lambda name: pytest.fail('rechecking saved C must not issue new runner credentials')
    net._judge_kept_matches('game', [('first', 'int x;', None)], lambda _: None)

    existing = net._function('user_claim', 'game')
    with store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (existing['id'], agent.id, 1.0, 9999999999.0))
    net._judge_kept_matches('game', [('first', 'int x;', agent.id)], lambda _: None)
    assert seen[-1] == 'first'
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 1
    waited = []
    def release(_):
        assert store.one('SELECT claimed_at FROM claims WHERE function_id=?', existing['id'])['claimed_at'] == 1.0
        waited.append(True)
        with store.tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=?', (existing['id'],))
    monkeypatch.setattr(importing, 'time', SimpleNamespace(time=importing.time.time, sleep=release))
    messages = []
    net._judge_kept_matches('game', [('user_claim', 'int z;', agent.id)], messages.append)
    assert waited == [True] and seen[-1] == 'user_claim'
    assert any('waiting for its current claim' in message for message in messages)
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0
    def interrupted(*args):
        raise RuntimeError('interrupted')
    net.attempt = interrupted
    with pytest.raises(RuntimeError, match='interrupted'):
        net._judge_kept_matches('game', [('first', 'int x;', agent.id)], lambda _: None)
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0

    net._judge_kept_matches('game', [('removed', 'int preserved;', None)], lambda _: None)
    note = store.one("SELECT text FROM notes WHERE project_id=? AND function_id IS NULL", pid)
    assert 'removed' in note['text'] and 'int preserved;' in note['text']


def test_reimport_rejudge_records_proven_retry_and_keeps_original(tmp_path, monkeypatch):
    from types import SimpleNamespace

    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                         'VALUES(?,?,?,?,?,?,?,?)', (pid, 'work', 4, 1, b'\0' * 4, '[]', 'open', 0x80000000)).lastrowid
    store = net.project_store(pid)
    monkeypatch.setattr(net, 'console', lambda project: SimpleNamespace(
        retry_reimport_source=lambda network, project, fn, source: 'rebound'))
    seen = []

    def attempt(agent, name, source, slug):
        seen.append(source)
        if source == 'rebound':
            with store.tx() as db:
                db.execute("UPDATE functions SET status='matched' WHERE id=?", (fid,))
        return {'matched': source == 'rebound'}

    monkeypatch.setattr(net, 'attempt', attempt)
    net._judge_kept_matches('game', [('work', 'original', None)], lambda _: None)
    assert seen == ['original', 'rebound']
    assert 'original' in store.one('SELECT text FROM notes WHERE function_id=?', fid)['text']
    assert store.one('SELECT status FROM functions WHERE id=?', fid)['status'] == 'matched'
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0


def test_reimport_wait_can_be_stopped_without_releasing_another_claim(tmp_path, monkeypatch):
    from decompnet.app.engine import importing

    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                         'VALUES(?,?,?,?,?,?,?,?)', (pid, 'work', 4, 1, b'\0'*4, '[]', 'open', 0x80000000)).lastrowid
    store = net.project_store(pid)
    agent = net.attribution_agent('author')
    with store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fid, agent.id, 1.0, 9999999999.0))
    waited = []
    monkeypatch.setattr(importing, 'time', SimpleNamespace(time=importing.time.time, sleep=lambda _: waited.append(True)))
    class Log:
        def __call__(self, message): pass
        def check_cancel(self):
            if waited: raise RuntimeError('stopped')
    with pytest.raises(RuntimeError, match='stopped'):
        net._judge_kept_matches('game', [('work', 'saved source', agent.id)], Log())
    assert store.one('SELECT claimed_at FROM claims WHERE function_id=?', fid)['claimed_at'] == 1.0


def test_reimport_wait_keeps_another_workers_match_and_saves_old_source(tmp_path, monkeypatch):
    from decompnet.app.engine import importing

    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                         'VALUES(?,?,?,?,?,?,?,?)', (pid, 'work', 4, 1, b'\0'*4, '[]', 'open', 0x80000000)).lastrowid
    store = net.project_store(pid)
    agent = net.attribution_agent('author')
    with store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (fid, agent.id, 1.0, 9999999999.0))
    def finish(_):
        with store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", ('new match', fid))
            db.execute('DELETE FROM claims WHERE function_id=?', (fid,))
    monkeypatch.setattr(importing, 'time', SimpleNamespace(time=importing.time.time, sleep=finish))
    monkeypatch.setattr(net, 'attempt', lambda *a, **k: pytest.fail('must not replace another match'))
    net._judge_kept_matches('game', [('work', 'old source', agent.id)], lambda _: None)
    assert store.one('SELECT matched_source FROM functions WHERE id=?', fid)['matched_source'] == 'new match'
    assert 'old source' in store.one('SELECT text FROM notes WHERE function_id=?', fid)['text']


def test_reimport_does_not_wait_on_its_own_autopilot_claim(tmp_path, monkeypatch):
    from decompnet.app.engine import importing, judging

    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        for index, name in enumerate(('own', 'other')):
            db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                       'VALUES(?,?,?,?,?,?,?,?)', (pid, name, 4, 1, b'\0'*4, '[]', 'open', 0x80000000+4*index))
    agent = net.attribution_agent('autopilot')
    store = net.project_store(pid)
    other = net._function('other', 'game')
    with store.tx() as db:
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (other['id'], agent.id, 1.0, 9999999999.0))
    waited = []
    def release(_):
        waited.append(True)
        with store.tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=?', (other['id'],))
    monkeypatch.setattr(judging, 'time', SimpleNamespace(sleep=release))
    with importing._reimport_claim(net, 'game', 'own', agent):
        assert net._yield_autopilot('own', 'game') is False
        assert waited == []
        assert net._yield_autopilot('other', 'game') is True
        assert waited == [True]
    assert getattr(net._compile_ahead_local, 'reimport_claim', None) is None
    assert store.one('SELECT COUNT(*) AS n FROM claims')['n'] == 0


def test_reimport_still_waits_if_its_claim_is_replaced(tmp_path, monkeypatch):
    from decompnet.app.engine import importing, judging

    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(tmp_path), 'mips', '{}')).lastrowid
        db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,vram) '
                   'VALUES(?,?,?,?,?,?,?,?)', (pid, 'f', 4, 1, b'\0'*4, '[]', 'open', 0x80000000))
    agent = net.attribution_agent('autopilot')
    store = net.project_store(pid)
    fn = net._function('f', 'game')
    waited = []
    def release(_):
        waited.append(True)
        with store.tx() as db:
            db.execute('DELETE FROM claims WHERE function_id=?', (fn['id'],))
    monkeypatch.setattr(judging, 'time', SimpleNamespace(sleep=release))
    with importing._reimport_claim(net, 'game', 'f', agent):
        with store.tx() as db:
            db.execute('UPDATE claims SET claimed_at=? WHERE function_id=?', (1.0, fn['id']))
        assert net._yield_autopilot('f', 'game') is True
        assert waited == [True]
    assert getattr(net._compile_ahead_local, 'reimport_claim', None) is None


@pytest.mark.parametrize("legacy_reset",[False,True])
def test_reimport_replaces_analysis_and_keeps_only_unchanged_matches(tmp_path, monkeypatch, legacy_reset):
    root = tmp_path / "game"
    root.mkdir()
    (root / "old.z64").write_bytes(b"old")
    (root / "old.yaml").write_text("old split", encoding="utf-8")
    (root / "progress").mkdir()
    (root / "progress" / "save.json").write_text("saved work", encoding="utf-8")
    repository = root / ("exports/game" if legacy_reset else "source")
    repository.mkdir(parents=True)
    git(repository, "init", "--initial-branch=main")
    origin = "https://example.com/game.git"
    git(repository, "remote", "add", "origin", origin)
    build = repository / "build.py"
    build.write_text("# committed build script\n", encoding="utf-8")
    git(repository, "add", "build.py")
    git(repository, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Initial build")
    head = git(repository, "rev-parse", "HEAD")
    build.write_text("# staged change\n", encoding="utf-8")
    git(repository, "add", "build.py")
    build.write_text("# unstaged change\n", encoding="utf-8")
    (repository / "local.txt").write_text("local work", encoding="utf-8")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {
        "kind": "rom", "platform": "n64", "big_endian": True, "gp": 0,
        "rom": {"file": "old.z64", "program": "old.z64", "sha1": "old"},
        "segments": [], "compiler": {"toolchain": "chosen-toolchain", "flags": ["-G0"], "timeout": 30},
        "auto_compiler": True, "export": {"path": "old/source"}, "autopilot": {"matched": 100},
        "collab": {"open": True},
    }
    if legacy_reset:
        cfg["repository_sync_folder"] = str(repository)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                         ("game", "old game", str(root), "mips", json.dumps(cfg))).lastrowid
        unchanged_id = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, "clamp_s32", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void clamp_s32(void) {}", 0x80000000),  # renamed since import (polishing): paired by its address
        ).lastrowid
        changed_id = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, "func_80000004", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void func_80000004(void) {}", 0x80000004),
        ).lastrowid
        db.execute("UPDATE functions SET last_checked=123,last_improved=120 WHERE id=?", (changed_id,))
        removed_id = db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source) "
                                "VALUES(?,?,?,?,?,?,?)", (pid, "func_80000008", 4, 1, 0x80000008, "matched",
                                                          "void func_80000008(void) { gone(); }")).lastrowid
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (pid, "clamp_s32", 0x80000000, 4, "func"))
        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,?,?,?)",
                   (pid, changed_id, "keep this note", 1))
        db.execute("INSERT INTO notes(project_id,function_id,text,created_at) VALUES(?,?,?,?)",
                   (pid, removed_id, "note on removed function", 2))
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) "
                   "VALUES(?,?,?,?,?,?)", (pid, 1, "user header", "custom", 1, ""))

    with net.store.tx() as db:
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) VALUES(?,2,'private types','source',2,'local_source_test')",(pid,))
        db.execute("UPDATE functions SET unit='local_source_test' WHERE id=?",(unchanged_id,))
        if legacy_reset:
            db.execute("UPDATE functions SET unit='original_segment' WHERE id=?",(unchanged_id,))
            db.execute("INSERT INTO attempts(function_id,score,compiled,context_version,source,created_at) VALUES(?,0,1,2,?,2)",
                       (unchanged_id,"void clamp_s32(void) {}"))
        db.execute('UPDATE functions SET meta=? WHERE id=?',
                   (json.dumps({'naming':{'method':'operator'},'reopened':True}),unchanged_id))

    game = tmp_path / "selected.z64"
    game.write_bytes(b"new rom")

    class TestConsole(Console):  # the connector's own split/judge/asm parts, with only the facts this test needs
        id = "n64"
        name = "Nintendo 64"
        arch = "mips"

        def read_game(self, file, name):
            return Picked("n64", b"new rom", "new.z64", {
                "title": "new game", "size": 7, "game_code": "ABCD", "region": "US",
                "compiler_guess": "KMC", "sha1": "new",
            }, True)

        def debug_info(self, program, known):
            return None

        def default_compiler(self, info, guess):
            return "detected-toolchain"

        def default_flags(self, toolchain, gp):
            return []

        def after_import(self, net, slug, chosen, log):
            assert chosen is True

    console = TestConsole()
    monkeypatch.setattr("decompnet.app.engine.importing.consoles.identify", lambda _: console)
    net.console = lambda _: console
    line_same = split.AsmLine(0, 0x80000000, 0x24020001, "addiu", "v0, zero, 1")
    line_changed = split.AsmLine(4, 0x80000004, 0x24020002, "addiu", "v0, zero, 2")
    line_new = split.AsmLine(8, 0x8000000C, 0x24020003, "addiu", "v0, zero, 3")
    functions = [
        split.RomFunction("func_80000000", 0x80000000, 4, "code", [line_same], root / "same.s"),
        split.RomFunction("func_80000004", 0x80000004, 4, "code", [line_changed], root / "changed.s"),
        split.RomFunction("func_8000000C", 0x8000000C, 8, "code", [line_new], root / "new.s"),
    ]

    def fake_split(workdir, *args, **kwargs):
        (workdir / "new.yaml").write_text("new split", encoding="utf-8")
        return split.SplitResult(functions, [], [split.Segment("code", 0, 16, 0x80000000)], {}, None, "", "")

    monkeypatch.setattr("decompnet.app.engine.importing.split.split_rom", fake_split)

    def assert_repository_preserved():
        assert git(repository, "rev-parse", "HEAD") == head
        assert git(repository, "remote", "get-url", "origin") == origin
        assert git(repository, "show", ":build.py") == "# staged change"
        assert build.read_text(encoding="utf-8") == "# unstaged change\n"
        assert (repository / "local.txt").read_text(encoding="utf-8") == "local work"

    def fail_registration(**kwargs):
        assert_repository_preserved()
        raise RuntimeError("registration failed")

    with monkeypatch.context() as patch:
        patch.setattr(net, "project_registration", fail_registration)
        with pytest.raises(RuntimeError, match="registration failed"):
            net.reimport_rom("game", game, confirmed=True, log=lambda _: None)
    assert_repository_preserved()
    assert (root / "old.z64").read_bytes() == b"old"
    assert not (root / "new.z64").exists()

    messages = []
    result = net.reimport_rom("game", game, confirmed=True, log=messages.append)
    assert result == {"slug": "game", "functions": 3, "matched": 1}
    assert messages[0] == "Starting reimport: reading the selected game and checking its format."
    assert any("New split ready: 3 functions" in message for message in messages)
    assert any("Replacing project files" in message for message in messages)
    assert any("reimport kept 1 matched functions" in message for message in messages)
    assert messages[-1] == "Reimport complete: 3 functions; 1 still match."
    assert (root / "progress" / "save.json").read_text(encoding="utf-8") == "saved work"
    assert_repository_preserved()
    assert (root / "new.z64").read_bytes() == b"new rom"
    assert (root / "new.yaml").read_text(encoding="utf-8") == "new split"
    assert not (root / "old.yaml").exists()
    assert net.store.one('SELECT unit FROM functions WHERE id=?',unchanged_id)['unit']=='local_source_test'
    unchanged = net.store.one("SELECT status, matched_source FROM functions WHERE id=?", unchanged_id)
    assert json.loads(net.store.one("SELECT meta FROM functions WHERE id=?",unchanged_id)["meta"])["naming"]=={"method":"operator"}
    assert unchanged["status"] == "matched"
    assert unchanged["matched_source"] == "void clamp_s32(void) {}"
    assert net.store.one("SELECT name FROM functions WHERE id=?", unchanged_id)["name"] == "clamp_s32"
    assert net.store.one("SELECT address FROM symbols WHERE project_id=? AND name='clamp_s32'", pid)["address"] == 0x80000000
    assert not net.store.one("SELECT 1 FROM symbols WHERE project_id=? AND name='func_80000000'", pid)
    kept = net.store.one("SELECT text FROM notes WHERE text LIKE '%it matched with this C%'")["text"]
    assert "gone();" in kept and any("their C is kept in the project's notes" in m for m in messages)
    changed = net.store.one("SELECT status, matched_source, target, last_checked, last_improved "
                            "FROM functions WHERE id=?", changed_id)
    assert changed["status"] == "open" and changed["matched_source"] is None
    assert changed["target"] == bytes.fromhex("24020002")
    assert 'void func_80000004(void) {}' in net.store.one(
        "SELECT text FROM notes WHERE function_id=? AND text LIKE '%before reimport: saved C%'", changed_id)["text"]
    assert changed["last_checked"] is None and changed["last_improved"] is None
    assert net.store.one("SELECT text FROM notes WHERE function_id=?", changed_id)["text"] == "keep this note"
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?",
                         pid, "func_80000008") is None
    orphaned_note = net.store.one("SELECT function_id, text FROM notes WHERE text LIKE '%removed function%'")
    assert orphaned_note["function_id"] is None
    assert orphaned_note["text"] == "[func_80000008 before reimport] note on removed function"
    assert net.store.one("SELECT 1 FROM functions WHERE project_id=? AND name=?", pid, "func_8000000C")
    saved = json.loads(net.project_row("game")["config"])
    assert saved["compiler"] == cfg["compiler"] and saved["auto_compiler"] is True
    assert net.project_row("game")["name"] == "old game"
    assert "export" not in saved and "autopilot" not in saved
    assert net.store.one("SELECT header FROM context_versions WHERE project_id=? AND unit='' ORDER BY version DESC LIMIT 1",
                         pid)["header"] == "user header"
    assert net.store.one("SELECT kind FROM events WHERE project_id=? ORDER BY id DESC LIMIT 1",
                         pid)["kind"] == "reimport"
    assert not list(tmp_path.glob(".game-reimport-backup-*"))


def test_reimport_still_blocks_projects_with_an_active_room(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    root = tmp_path / "game"
    root.mkdir()
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                   ("game", "Game", str(root), "mips", json.dumps({
                       "kind": "rom", "platform": "n64", "collab": {"open": True},
                   })))

    class TestConsole:
        id = "n64"

    console = TestConsole()
    net.console = lambda _: console
    net.rooms["game"] = object()
    with pytest.raises(NetworkError, match="leave the shared room"):
        net.import_split(console, Picked("n64", b"rom", "game.z64", {}, True), reimport_slug="game")


@pytest.mark.parametrize('derived', [False, True])
@pytest.mark.parametrize('unit_changed', [False, True])
def test_reimport_pairs_saved_matches_by_overlay_and_address(tmp_path, monkeypatch, derived, unit_changed):
    import hashlib

    from decompnet import consoles

    root = tmp_path / 'banks'
    root.mkdir()
    net = RomNetwork(tmp_path / 'banks.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'rom': {'file': 'rom.z64', 'sha1': 'same',
                   'analysis_sha1': hashlib.sha1(b'derived image' if derived else bytes(16)).hexdigest()
                   if unit_changed else 'stale'}, 'segments': []}
    originals = {}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('banks', 'Banks', str(root), 'mips', json.dumps(cfg))).lastrowid
        for index in [1, 2]:
            name = f'renamed_bank_{index}'
            body = bytes.fromhex(f'2402000{index}')
            fid = db.execute('INSERT INTO functions(project_id,name,unit,vram,size,n_insns,target,relocs,status,matched_source,rom_offset) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (pid, name, f'overlay_{index:03d}', 0x80300000,
                              4, 1, body, '[]', 'matched', f'int {name}(void) {{ return {index}; }}',
                              index * 4 if unit_changed else None)).lastrowid
            originals[fid] = (name, body)

    def replacement(work, *a, **kw):
        functions = []
        # Reverse order to catch accidental address-only pairing and stolen match identities.
        for index in [2, 1]:
            unit = f'{"renamed" if unit_changed else "overlay"}_{index:03d}'
            line = split.AsmLine(index * 4, 0x80300000, 0x24020000 + index, 'addiu', f'v0, zero, {index}')
            functions.append(split.RomFunction(f'func_80300000_{unit}', 0x80300000, index * 4, unit,
                                                [line], work / (unit + '.s')))
        if unit_changed:
            (work/'splat.yaml').write_text('segments: []\n')
        if derived:(work/'analysis.bin').write_bytes(b'derived image')
        return split.SplitResult(functions, [], [], {}, work / 'splat.yaml', 'KMC', '', image='analysis.bin' if derived else 'rom.z64')
    monkeypatch.setattr(split, 'split_rom', replacement)
    picked = Picked('n64', bytes(16), 'rom.z64', {'title': 'Banks', 'size': 16, 'sha1': 'same',
                    'game_code': 'DEMO', 'region': 'Test', 'compiler_guess': 'KMC'}, True)
    net.import_split(consoles.get('n64'), picked, reimport_slug='banks', log=lambda *_: None)
    recorded = net.cfg(net.project_row('banks'))['rom']
    assert recorded['sha1'] == 'same'
    assert recorded['analysis_sha1'] == hashlib.sha1(b'derived image' if derived else bytes(16)).hexdigest()
    for fid, (name, body) in originals.items():
        row = net.store.one('SELECT * FROM functions WHERE id=?', fid)
        assert row['name'] == name and row['target'] == body and row['status'] == 'matched'
    assert len(net.store.all('SELECT * FROM functions WHERE project_id=?', pid)) == 2


@pytest.mark.parametrize('saved_status', ['matched', 'open'])
def test_reimport_rejudges_saved_c_when_a_referenced_symbol_moves(tmp_path, monkeypatch, saved_status):
    import hashlib

    from decompnet import consoles

    root = tmp_path/'game'
    root.mkdir()
    image = bytes.fromhex('2402000124020002') + bytes(8)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
           'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'rom': {'file': 'rom.z64', 'sha1': 'same', 'analysis_sha1': hashlib.sha1(image).hexdigest()},
           'segments': []}
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    source = 'extern int moved; int keep(void) { return moved; }'
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(root), 'mips', json.dumps(cfg))).lastrowid
        for name, addr, offset, word, text in [('keep', 0x80000000, 0, image[:4], source),
                                                ('moved', 0x80001000, 4, image[4:8], 'int moved(void) { return 2; }')]:
            status = saved_status if name == 'keep' else 'matched'
            db.execute('INSERT INTO functions(project_id,name,unit,vram,rom_offset,size,n_insns,target,relocs,status,matched_source,best_source) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid, name, 'unit', addr, offset, 4, 1, word, '[]', status,
                        text if status == 'matched' else None, text if status == 'open' else None))
            db.execute('INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)',
                       (pid, name, addr, 4, 'func'))
    def replacement(work, *args, **kwargs):
        (work/'splat.yaml').write_text('segments: []\n')
        functions = [split.RomFunction('keep', 0x80000000, 0, 'unit',
                     [split.AsmLine(0, 0x80000000, 0x24020001, 'addiu', 'v0, zero, 1')], work/'keep.s'),
                     split.RomFunction('new_moved', 0x80002000, 4, 'unit',
                     [split.AsmLine(4, 0x80002000, 0x24020002, 'addiu', 'v0, zero, 2')], work/'moved.s')]
        return split.SplitResult(functions, [], [], {}, work/'splat.yaml', 'KMC', '', image='rom.z64')
    monkeypatch.setattr(split, 'split_rom', replacement)
    picked = Picked('n64', image, 'rom.z64', {'title': 'Game', 'size': len(image), 'sha1': 'same',
                    'game_code': 'DEMO', 'region': 'Test', 'compiler_guess': 'KMC'}, True)
    queued = []
    net.import_split(consoles.get('n64'), picked, reimport_slug='game', rejudge_out=queued, log=lambda *_: None)
    row = net.store.one('SELECT * FROM functions WHERE project_id=? AND name=?', pid, 'keep')
    assert row['status'] == 'open' and row['matched_source'] is None
    assert row['best_source'] == 'extern int reimport_ref_80001000; int keep(void) { return reimport_ref_80001000; }'
    assert ('keep', row['best_source'], None) in queued
    assert net.store.one('SELECT address FROM symbols WHERE project_id=? AND name=?', pid, 'reimport_ref_80001000')['address'] == 0x80001000
    assert source in net.store.one('SELECT text FROM notes WHERE function_id=?', row['id'])['text']


def test_reimport_keeps_agents_names_and_judges_c_whose_references_were_read_anew(tmp_path, monkeypatch):
    """A racing game lost 234 matches at once: its new split sized bss blocks over 177 addresses helpers had named
    (the names went, so matched C stopped linking), and a newer reader annotated references the old targets left as
    numbers (same bytes, other relocations), so the C was dropped unjudged."""
    root = tmp_path / "game"
    root.mkdir()
    (root / "old.z64").write_bytes(b"old")
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    cfg = {"kind": "rom", "platform": "n64", "big_endian": True, "gp": 0,
           "rom": {"file": "old.z64", "program": "old.z64", "sha1": "old"}, "segments": [],
           "compiler": {"toolchain": "chosen-toolchain", "flags": ["-G0"], "timeout": 30}}
    helper = net.attribution_agent("claude-helper")
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)",
                         ("game", "old game", str(root), "mips", json.dumps(cfg))).lastrowid
        fid = db.execute(
            "INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,matched_agent_id,vram) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, "race_update", 4, 1, bytes.fromhex("24020001"), "[]", "matched",
             "void race_update(void) { gRecords = 0; }", helper.id, 0x80000000)).lastrowid
        db.execute("UPDATE functions SET unit='private_source' WHERE id=?", (fid,))
        db.execute("INSERT INTO context_versions(project_id,version,header,message,created_at,unit) "
                   "VALUES(?,1,'private types','source',1,'private_source')", (pid,))
        for name, address, size, kind in (("gRecords", 0x800C35A0, 0, "data"), ("gSlotState", 0x800C35A0, 0, "data"),
                                          ("gLapCount", 0x800D0000, 0, "bss"), ("gStreamState", 0x800C02AE, 0, "bss"),
                                          ("stream_state", 0x800C02AE, 0, "bss")):
            db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                       (pid, name, address, size, kind))
    game = tmp_path / "selected.z64"
    game.write_bytes(b"new rom")

    class TestConsole(Console):
        id = "n64"
        name = "Nintendo 64"
        arch = "mips"

        def read_game(self, file, name):
            return Picked("n64", b"new rom", "new.z64", {"title": "new game", "size": 7, "game_code": "ABCD",
                                                         "region": "US", "compiler_guess": "KMC", "sha1": "new"}, True)

        def debug_info(self, program, known):
            return None

        def default_compiler(self, info, guess):
            return "detected-toolchain"

        def default_flags(self, toolchain, gp):
            return []

        def after_import(self, net, slug, chosen, log):
            pass

        def target_from_split(self, fn, resolve, big_endian, gp):
            from decompnet.rom.match import RomTarget
            return RomTarget(fn.name, fn.vram, bytes.fromhex("24020001"), [(0, 5, 0x800C35A0)], True, 0)

    console = TestConsole()
    monkeypatch.setattr("decompnet.app.engine.importing.consoles.identify", lambda _: console)
    net.console = lambda _: console
    line = split.AsmLine(0, 0x80000000, 0x24020001, "addiu", "v0, zero, 1")
    functions = [split.RomFunction("func_80000000", 0x80000000, 4, "code", [line], root / "f.s")]
    data = [split.DataSymbol("D_800C02AE", 0x800C02AE, None, 66514, "bss")]  # sized over gRecords
    data.append(split.DataSymbol("D_800CFFF0", 0x800CFFF0, None, 0x100, "bss"))  # gLapCount lies inside this one

    def fake_split(workdir, *args, **kwargs):
        return split.SplitResult(functions, data, [split.Segment("code", 0, 16, 0x80000000)], {}, None, "", "")

    monkeypatch.setattr("decompnet.app.engine.importing.split.split_rom", fake_split)
    judged = []

    def attempt(agent, name, source, project=None):
        judged.append((agent.name, name, source))
        return {"matched": False}

    net.attempt = attempt
    messages = []
    net.reimport_rom("game", game, confirmed=True, log=messages.append)
    names = {r["name"]: r["address"] for r in net.store.all("SELECT name, address FROM symbols WHERE project_id=?", pid)}
    assert names["gRecords"] == names["gSlotState"] == 0x800C35A0  # both names of one address
    sizes = {r["name"]: r["size"] for r in net.store.all("SELECT name, size FROM symbols WHERE project_id=?", pid)}
    assert sizes["gStreamState"] == sizes["stream_state"] == 66514  # both names of the block keep its size
    assert "D_800C02AE" not in sizes
    assert names["gLapCount"] == 0x800D0000 and names["D_800CFFF0"] == 0x800CFFF0  # inside a sized block
    assert judged == [("claude-helper", "race_update", "void race_update(void) { gRecords = 0; }")]
    row = net.store.one("SELECT status, best_source, unit FROM functions WHERE id=?", fid)
    assert row["status"] == "open" and row["best_source"] == "void race_update(void) { gRecords = 0; }"
    assert row["unit"] == "private_source"
    assert any("0 of 1 still match" in m for m in messages)


@pytest.mark.parametrize('status,collision,interrupt,extended', [
    ('matched', False, False, False), ('open', False, False, False),
    ('matched', True, False, False), ('open', True, False, False),
    ('matched', False, True, False), ('open', False, True, False),
    ('matched', False, False, True), ('open', False, False, True),
])
def test_reimport_rejudges_same_rom_function_when_its_link_address_changes(tmp_path, monkeypatch, status, collision, interrupt, extended):
    """A corrected overlay base must not discard C or give it to a different function at the old address."""
    import hashlib

    root = tmp_path / 'game'
    root.mkdir()
    image = bytes(16)
    (root / 'rom.z64').write_bytes(image)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'big_endian': True,
           'rom': {'file': 'rom.z64', 'sha1': 'game', 'analysis_sha1': hashlib.sha1(image).hexdigest()},
           'segments': [], 'compiler': {'toolchain': 'ido-7.1', 'flags': ['-O2']}}
    source = 'int preserved(void) { return 1; }'
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game', 'Game', str(root), 'mips', json.dumps(cfg))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,target,relocs,status,matched_source,best_source,vram,rom_offset) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (pid, 'old_entry', 4, 1, bytes.fromhex('24020001'),
                          '[]', status, source if status == 'matched' else None,
                          source if status == 'open' else None, 0x80401000, 4)).lastrowid

    class TestConsole(Console):
        id = 'n64'
        name = 'Nintendo 64'
        arch = 'mips'

        def read_game(self, file, name):
            return Picked('n64', image, 'rom.z64', {'title': 'Game', 'size': len(image), 'game_code': 'DEMO',
                          'region': 'Test', 'compiler_guess': 'IDO', 'sha1': 'game'}, True)

        def debug_info(self, program, known):
            return None

        def default_compiler(self, info, guess):
            return 'ido-7.1'

        def default_flags(self, toolchain, gp):
            return []

        def after_import(self, net, slug, chosen, log):
            pass

    console = TestConsole()
    monkeypatch.setattr('decompnet.app.engine.importing.consoles.identify', lambda _: console)
    net.console = lambda _: console
    line = split.AsmLine(4, 0x80402000, 0x24020001, 'addiu', 'v0, zero, 1')
    extra = split.AsmLine(8, 0x80402004, 0x24030003, 'addiu', 'v1, zero, 3')
    fn = split.RomFunction('new_entry', 0x80402000, 4, 'overlay', [line, extra] if extended else [line], root / 'f.s')
    other_line = split.AsmLine(12, 0x80401000, 0x24020002, 'addiu', 'v0, zero, 2')
    other = split.RomFunction('old_entry' if collision else 'other_entry', 0x80401000, 12, 'other',
                              [other_line], root / 'other.s')
    monkeypatch.setattr(split, 'split_rom', lambda work, *a, **kw: split.SplitResult(
        [other, fn], [], [split.Segment('overlay', 4, 12, 0x80402000),
                          split.Segment('other', 12, 16, 0x80401000)], {}, None, '', '', image='rom.z64'))
    judged = []
    def attempt(agent, name, code, slug):
        judged.append((name, code))
        if interrupt:
            raise RuntimeError('judge interrupted')
        return {'matched': False}

    net.attempt = attempt
    if interrupt:
        with pytest.raises(RuntimeError, match='judge interrupted'):
            net.reimport_rom('game', root / 'rom.z64', confirmed=True, log=lambda _: None)
    else:
        net.reimport_rom('game', root / 'rom.z64', confirmed=True, log=lambda _: None)
    row = net.store.one('SELECT name, vram, best_source, status FROM functions WHERE id=?', fid)
    if collision:
        assert row is None or (row['vram'] == 0x80401000 and row['best_source'] is None)
        assert judged == []
        assert source in net.store.one('SELECT text FROM notes WHERE project_id=? AND text LIKE ?', pid,
                                       '%before reimport:%')['text']
        assert net.store.one('SELECT vram FROM functions WHERE name=?', 'old_entry')['vram'] == 0x80401000
    else:
        assert (row['name'], row['vram'], row['best_source'], row['status']) == ('old_entry', 0x80402000, source, 'open')
        assert judged == [('old_entry', source)]
        assert net.store.one('SELECT address FROM symbols WHERE project_id=? AND name=?', pid, 'old_entry')['address'] == 0x80402000
        assert net.store.one('SELECT vram FROM functions WHERE name=?', 'other_entry')['vram'] == 0x80401000


def test_reimport_aborts_if_entries_change_after_preservation_stage(tmp_path, monkeypatch):
    import hashlib

    from decompnet import consoles

    root = tmp_path/'game'
    root.mkdir()
    (root/'old.z64').write_bytes(b'original')
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    image = bytes(16)
    cfg = {'kind':'rom', 'platform':'n64', 'big_endian':True,
           'rom':{'file':'old.z64', 'sha1':'same', 'analysis_sha1':hashlib.sha1(image).hexdigest()},
           'segments':[], 'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game','Game',str(root),'mips',json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) '
                   'VALUES(?,?,?,?,?,?,?,?,?)',(pid,'old',0x80001000,0,4,1,bytes(4),'[]','open'))

    def replacement(work, *args, **kwargs):
        line = split.AsmLine(0,0x80001000,0,'nop','')
        fn = split.RomFunction('new',0x80001000,0,'code',[line],work/'new.s')
        return split.SplitResult([fn],[],[split.Segment('code',0,16,0x80001000)],{},work/'splat.yaml','KMC','',image='rom.z64')

    def changed_during_stage(network, project, result, data, rows, log):
        with network.project_store(pid).tx() as db:
            db.execute("UPDATE functions SET name='changed' WHERE project_id=?",(pid,))
        return 1

    monkeypatch.setattr(split, 'split_rom', replacement)
    console = consoles.get('n64')
    monkeypatch.setattr(console, 'preserve_reimport_entries', changed_during_stage)
    picked = Picked('n64',image,'rom.z64',{'title':'Game','size':16,'sha1':'same',
                    'game_code':'DEMO','region':'Test','compiler_guess':'KMC'},True)
    with pytest.raises(NetworkError, match='entries changed during reimport'):
        net.import_split(console,picked,reimport_slug='game',log=lambda *_:None)
    assert (root/'old.z64').read_bytes() == b'original'
    assert not (root/'rom.z64').exists()
    assert net.project_store(pid).one('SELECT name FROM functions WHERE project_id=?',pid)['name'] == 'changed'


def test_reimport_rechecks_saved_c_for_exact_entry_recreated_after_boundary_repair(tmp_path):
    from decompnet.app.engine.importing import _recreated_matches

    net = RomNetwork(tmp_path/'app.db', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game','Game',str(tmp_path),'mips','{}')).lastrowid
        for name, address, data in [('returned',0x80001000,b'\0'*8), ('different',0x80002000,b'\x01'*8)]:
            db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',(pid,name,address,address & 0xffff,8,2,data,'[]','open'))
    old = {'name':'returned','vram':0x80001000,'rom_offset':0x1000,'size':8,'target':b'\0'*8,
           'status':'matched','matched_source':'int returned(void) { return 0; }','matched_agent_id':None}
    changed = {**old,'name':'different','vram':0x80002000,'rom_offset':0x2000}
    gone = {'returned':old,'different':changed}
    assert _recreated_matches(net,pid,gone,False) == []
    recovered = _recreated_matches(net,pid,gone,True)
    assert recovered == [('returned',old['matched_source'],None)]
    net.attempt = lambda *args, **kwargs: (_ for _ in ()).throw(NetworkError('Compiler unavailable.'))
    net._judge_kept_matches('game',recovered,lambda *_:None)
    saved = net.project_store(pid).one("SELECT status,best_source FROM functions WHERE project_id=? AND name='returned'",pid)
    assert saved['status'] == 'open' and saved['best_source'] == old['matched_source']


def test_reimport_keeps_a_recreated_open_entrys_best_attempt_before_recheck(tmp_path):
    from decompnet.app.engine.importing import _recreated_matches

    net=RomNetwork(tmp_path/'app.db',tmp_path)
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                       ('game','Game',str(tmp_path),'mips','{}')).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) VALUES(?,?,?,?,?,?,?,?,?)",
                   (pid,'returned',0x80001000,0x1000,8,2,b'\0'*8,'[]','open'))
    old={'name':'returned','vram':0x80001000,'rom_offset':0x1000,'size':8,'target':b'\0'*8,
         'status':'open','best_source':'int returned(void) { return 0; }','best_agent_id':None}
    assert _recreated_matches(net,pid,{'returned':old},True)==[('returned',old['best_source'],None)]
    row=net.project_store(pid).one('SELECT status,best_source,best_score FROM functions WHERE project_id=?',pid)
    assert row['status']=='open' and row['best_source']==old['best_source'] and row['best_score']==200


@pytest.mark.parametrize('extended', [False, True])
def test_reimport_queues_a_match_recreated_by_boundary_repair(tmp_path, monkeypatch, extended):
    import hashlib

    root = tmp_path/'game'
    root.mkdir()
    old_image = bytes(16)
    (root/'old.z64').write_bytes(old_image)
    net = RomNetwork(tmp_path/'app.db', tmp_path)
    image = old_image + (bytes(16) if extended else b'')
    cfg = {'kind':'rom', 'platform':'n64', 'big_endian':True,
           'rom':{'file':'old.z64','sha1':'same','analysis_sha1':hashlib.sha1(old_image).hexdigest()},
           'segments':[], 'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,arch,config) VALUES(?,?,?,?,?)',
                         ('game','Game',str(root),'mips',json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status,matched_source) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,'returned',0x80001008,8,4,1,bytes(4),'[]','matched','int returned(void) { return 0; }'))

    class TestConsole(Console):
        id = 'n64'
        name = 'Nintendo 64'
        arch = 'mips'
        def debug_info(self, program, known):
            return None
        def default_compiler(self, info, guess):
            return 'gcc-2.7.2-kmc'
        def default_flags(self, toolchain, gp):
            return []
        def after_import(self, net, slug, chosen, log):
            pass

    def replacement(work, *args, **kwargs):
        line = split.AsmLine(0,0x80001000,0,'nop','')
        fn = split.RomFunction('new',0x80001000,0,'code',[line],work/'new.s')
        return split.SplitResult([fn],[],[split.Segment('code',0,16,0x80001000)],{},work/'splat.yaml','KMC','',image='rom.z64')

    def repair(network, slug, log, **kwargs):
        with network.project_store(pid).tx() as db:
            db.execute('INSERT INTO functions(project_id,name,vram,rom_offset,size,n_insns,target,relocs,status) '
                       'VALUES(?,?,?,?,?,?,?,?,?)',(pid,'returned',0x80001008,8,4,1,bytes(4),'[]','open'))

    monkeypatch.setattr(split, 'split_rom', replacement)
    monkeypatch.setattr('decompnet.app.boundary_repairs.apply_entry_fixes', repair)
    monkeypatch.setattr('decompnet.app.boundary_review.refresh_shared_entries', lambda *args:None)
    console = TestConsole()
    net.console = lambda _:console
    picked = Picked('n64',image,'rom.z64',{'title':'Game','size':16,'sha1':'same',
                    'game_code':'DEMO','region':'Test','compiler_guess':'KMC'},True)
    rejudge = []
    net.import_split(console,picked,reimport_slug='game',rejudge_out=rejudge,log=lambda *_:None)
    assert rejudge == [('returned','int returned(void) { return 0; }',None)]
