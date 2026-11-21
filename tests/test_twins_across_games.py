"""Matched code from one of the user's games tried on another game's identical function (Twins.across_games):
libraries come in the same code from game to game. The second game here is the demo again under another name,
with its own names, so the names have to be swapped as its own symbols say."""

import json
import shutil
from pathlib import Path

import pytest

from decompnet.app.engine import RomNetwork

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def copy_project(net, slug, new_slug):
    """The same game as another project: copy its ROM, functions, symbols and headers under a new id."""
    src = net._project(slug)
    root = Path(src["root"]) / new_slug
    cfg = json.loads(src["config"])
    rom = Path(cfg["rom"]["file"])
    destination = root / rom
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(src["root"]) / rom, destination)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         (new_slug, "Another Game", "", src["arch"], src["big_endian"], str(root),
                          src["config"])).lastrowid
        for table, cols in (("functions", "name,size,n_insns,target,relocs,asm,seed,unit,vram,rom_offset"),
                            ("symbols", "name,address,size,kind"),
                            ("context_versions", "version,header,agent_id,message,created_at,unit")):
            db.execute(f"INSERT INTO {table}(project_id,{cols}) SELECT ?,{cols} FROM {table} WHERE project_id=?",
                       (pid, src["id"]))
    return pid


@pytest.mark.parametrize("subprocess_search", [False, True])
def test_a_match_in_one_game_matches_the_same_function_in_another(built, tmp_path, subprocess_search):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "another-game")  # the split is in both: it's the same code
    source_pid = net._project(slug)["id"]
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")  # this game's name only
    hints = net.twins.suggestions("another-game", "func_800007D0")
    assert any(h["name"] == "clamp_s32" and h["kind"] == "verified C in another game" for h in hints)
    if subprocess_search:
        from decompnet.app import maintenance_tasks, workers
        from decompnet.app.permuter_worker import PermuterWorker
        from decompnet.app.twins import Job

        class Log:
            def __call__(self, line):
                pass

            def check_cancel(self):
                pass

        previous = workers.WORKERS
        with_worker = net.permuter = PermuterWorker(net)
        try:
            result = maintenance_tasks.cross_game(net, "another-game", Job(), Log())
            assert with_worker._process is not None
            assert result['matched'] >= 1
        finally:
            with_worker.close()
            workers.configure(previous)
    else:
        assert net.twins.across_games("another-game") >= 1
    results = net.twins.cross_game_results("another-game")
    assert results["counts"]["match"] >= 1
    assert any(r["source"] == "clamp_s32" and r["target"] == "func_800007D0" for r in results["recent"])
    fn = net._function("func_800007D0", "another-game")
    assert fn["status"] == "matched" and "identical to clamp_s32 in " in fn["matched_source"]
    assert "clamp_s32" not in fn["matched_source"].split("*/", 1)[1]  # its own name, not the other game's
    assert net.twins.across_games("another-game") == 0  # nothing left to try
    net.remove_project(slug)
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_index WHERE project_id=?", source_pid)["n"] == 0


def test_local_object_signatures_are_name_hints_not_matches(built, tmp_path):  # noqa: F811
    from pathlib import Path

    from decompnet.app.twins import _ar_members
    from decompnet.network import NetworkError

    obj = Path(__file__).parents[1] / "projects/demo-rom/build/libc.o"
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    count = net.twins.import_sdk_objects(obj, "demo libc 1")
    assert count > 0
    assert net.store.one("SELECT COUNT(*) AS n FROM sdk_signatures WHERE corpus=?", "demo libc 1")["n"] == count
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    signatures = {r["fingerprint"]: r["name"] for r in net.store.all(
        "SELECT fingerprint,name FROM sdk_signatures WHERE corpus=?", "demo libc 1")}
    prints = net.twins.prints(net._project(slug))
    fn = next(r for r in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open'",
                                        net._project(slug)["id"])
              if r["id"] in prints and prints[r["id"]].key in signatures)
    assert any(h["name"] == signatures[prints[fn["id"]].key] and h["kind"] == "SDK object signature; name only"
               for h in net.twins.suggestions(slug, fn["name"]))
    assert fn["status"] == "open"  # importing a signature never marks C as matched
    assert _ar_members(b"!<arch>\n" + b"x" * 48 + b"2         " + b"`\n" + b"ab") == [b"ab"]
    try:
        _ar_members(b"not an archive")
    except NetworkError:
        pass
    else:
        assert False, "invalid archives must be rejected"


def test_imported_c_is_rejudged_in_rom_and_misses_are_remembered(built, tmp_path, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from decompnet.app.twins import Print

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("autopilot")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    donor_id = copy_project(net, slug, "imported-library")
    proj, donor = net._project(slug), net._project("imported-library")
    b = net._function("func_800007D0", slug)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE project_id=? AND name=?", (donor_id, b["name"]))
    mine = net.twins.prints(proj)
    fingerprint = mine[b["id"]]
    a = net._function(b["name"], "imported-library")
    monkeypatch.setattr(net.forks, "get", lambda _: (donor, object(), {"platform": "n64"}))
    monkeypatch.setattr(net.forks, "is_asm", lambda *_: False)
    monkeypatch.setattr(net.twins, "_fork_prints", lambda _, **kwargs: {
        a["id"]: Print(fingerprint.key, fingerprint.refs, fingerprint.size, name="library_clamp")})
    # Even a donor marked matched cannot bypass the destination's actual compiler and judge.
    monkeypatch.setattr(net.forks, "_current_source", lambda *_: "int library_clamp(int x) { return 123; }")
    wanted = {fingerprint.key: [b["id"]]}
    stopped = SimpleNamespace(stop=SimpleNamespace(is_set=lambda: False), reuse_limit=0,
                              reuse_attempts=0, reuse_deadline=float("inf"))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, stopped) == 0
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts")["n"] == 0
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 0
    assert net._function(b["name"], slug)["status"] == "open"
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 0
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts")["n"] == 1
    monkeypatch.setattr(net.forks, "_current_source", lambda *_: CLAMP.replace(b["name"], "library_clamp"))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, None) == 1
    result = net._function(b["name"], slug)
    assert result["status"] == "matched" and "in Another Game" in result["matched_source"]
    assert net.store.one("SELECT COUNT(*) AS n FROM cross_game_attempts WHERE outcome='match'")["n"] == 1
    job = SimpleNamespace(stop=SimpleNamespace(is_set=lambda: True))
    assert net.twins._fork_to_rom(agent, proj, donor, wanted, mine, job) == 0


def test_exact_donor_names_are_tracked_and_do_not_overwrite_names(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("tester")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "target")
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    before = next(r for r in net.function_map("target") if r["name"] == "func_800007D0")
    assert not before["named"] and not before["renamed"]
    assert net.twins.reuse_names("target") == 1
    fn = net._function("clamp_s32", "target")
    naming = json.loads(fn["meta"])["naming"]
    assert naming["method"] == "exact_cross_game" and naming["source_project"] == slug
    assert naming["original_name"] == "func_800007D0"
    after = next(r for r in net.function_map("target") if r["name"] == "clamp_s32")
    assert after["named"] and after["renamed"]
    assert net.twins.reuse_names("target") == 0
    assert net.rename_symbol(agent, "target", "clamp_s32", "my_clamp").startswith("✅")
    assert net.twins.reuse_names("target") == 0
    assert json.loads(net._function("my_clamp", "target")["meta"])["naming"]["original_name"] == "func_800007D0"
    # Even an explicit rename to another generated-looking name is owned by the user.
    assert net.rename_symbol(agent, "target", "my_clamp", "sub_800007D0").startswith("✅")
    assert net.twins.reuse_names("target") == 0



def test_conflicting_exact_donor_names_are_not_guessed(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("tester")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    copy_project(net, slug, "target")
    copy_project(net, slug, "second-donor")
    for donor, name in [(slug, "clamp_s32"), ("second-donor", "limit_s32")]:
        assert net.compile_and_diff(agent, "func_800007D0", CLAMP, donor).startswith("✅ MATCH")
        assert net.rename_symbol(agent, donor, "func_800007D0", name).startswith("✅")
    assert net.twins.reuse_names("target") == 0
    assert net._function("func_800007D0", "target")["name"] == "func_800007D0"
    assert net.twins.reuse_names("target", donor_slug="not-a-project") == 0
    assert net.twins.reuse_names("target", donor_slug=slug) == 1


def test_donors_built_with_the_wanting_functions_own_compiler_come_first():
    """Donors were ordered by the destination game's default compiler, which a function in another address range
    (or with its own verified profile) isn't built with."""
    from decompnet.app.twins import prefer_own_compilers

    sources = [{"fingerprint": "fp", "compiler": "game-default", "name": "a"},
               {"fingerprint": "fp", "compiler": "range-compiler", "name": "b"},
               {"fingerprint": "other", "compiler": "game-default", "name": "c"}]
    own = {1: "range-compiler", 2: "game-default"}
    prefer_own_compilers(sources, {"fp": [1], "other": [2]}, own.get)
    assert [s["name"] for s in sources] == ["b", "a", "c"] or [s["name"] for s in sources] == ["b", "c", "a"]
    assert sources[0]["name"] == "b"


def test_manual_search_finds_imported_code_inside_a_merged_rom_function(built, tmp_path, monkeypatch):  # noqa: F811
    from decompnet.app.twins import Job, object_prints

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("autopilot")
    copy_project(net, slug, "imported-library")
    proj, donor = net._project(slug), net._project("imported-library")
    net.split_function(agent, donor['slug'], "func_800007C4", "0x800007D0")
    a = net._function("func_800007D0", donor['slug'])
    with net.project_store(donor['id']).tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (a['id'],))
    ctx = net._context(donor['id'], a['unit'])
    compiled = net._compile(donor, ctx['header'], CLAMP, a)
    assert compiled.ok
    fingerprint = object_prints(compiled.obj)[a['name']]
    assert fingerprint.key == net.twins.prints(donor)[a['id']].key
    monkeypatch.setattr(net.forks, "get", lambda _: (donor, object(), {"platform": "n64"}))
    monkeypatch.setattr(net.forks, "is_asm", lambda *_: False)
    monkeypatch.setattr(net.twins, "_fork_prints", lambda _, **kwargs: {a['id']: fingerprint})
    job = Job()
    job.cross_exhaustive = True
    original = net._function("func_800007C4", slug)
    monkeypatch.setattr(net.forks, "_current_source", lambda *_: "int func_800007D0(void) { return 123; }")
    assert net.twins._fork_to_rom(agent, proj, donor, {}, net.twins.prints(proj), job) == 0
    assert net._function(original['name'], slug)['target'] == original['target']

    monkeypatch.setattr(net.forks, "_current_source", lambda *_: CLAMP)
    assert net.twins._fork_to_rom(agent, proj, donor, {}, net.twins.prints(proj), job) == 1
    recovered = net._function(a['name'], slug)
    assert recovered['status'] == 'matched'
    assert recovered['target'] == a['target']
    assert recovered['vram'] == a['vram']
    head = net._function(original['name'], slug)
    assert original['target'].startswith(head['target'])
    assert original['target'].endswith(recovered['target'])
    assert not any(original['target'][head['size']:-recovered['size']])  # alignment padding


@pytest.mark.parametrize('ambiguous', [False, True])
def test_manual_search_tries_donor_when_relocation_layouts_differ(built, tmp_path, monkeypatch, ambiguous):  # noqa: F811
    from decompnet.app.twins import Job, Print, _key
    from decompnet.elfobj import Reloc, mask_relocs

    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('autopilot')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    copy_project(net, slug, 'imported-library')
    proj, donor = net._project(slug), net._project('imported-library')
    b = net._function('func_800007D0', slug)
    a = net._function(b['name'], donor['slug'])
    with net.project_store(donor['id']).tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (a['id'],))
    # Model an expected object with an additional linker-marked operand. The destination retains its own bytes
    # and relocations, and the actual judge must still accept its independently compiled source.
    layout = [(0, 5)]
    code = mask_relocs(b['target'], [Reloc(0, 5, '', 0)], 'mips', True)
    fingerprint = Print(_key(code, layout), ['donor_symbol'], len(code), layout=layout)
    mine = net.twins.prints(proj)
    assert mine[b['id']].key != fingerprint.key
    source = 'int func_800007D0(void) { return 123; }'
    if ambiguous:
        from types import SimpleNamespace

        from decompnet.app import donor_build, donor_relocations, donor_sources
        from decompnet.project import compiler_for

        unit = SimpleNamespace(name=a['unit'], base=None)
        fork = SimpleNamespace(root=tmp_path, units=lambda: [unit])
        settings = compiler_for(net.cfg(proj), b['vram'], b)
        monkeypatch.setattr(donor_build.Settings, 'for_unit', lambda *_: settings)
        monkeypatch.setattr(donor_relocations, 'donor_refs', lambda *_:
                            Print(fingerprint.key, ['.local_section'], len(code)))
        monkeypatch.setattr(donor_sources, 'imported', lambda *_: [(source, '', '')])
    else:
        fork = object()
    monkeypatch.setattr(net.forks, 'get', lambda _: (donor, fork, {'platform': 'n64'}))
    monkeypatch.setattr(net.forks, 'is_asm', lambda *_: False)
    monkeypatch.setattr(net.twins, '_fork_prints', lambda _, **kwargs: {a['id']: fingerprint})
    job = Job()
    job.cross_exhaustive = True
    monkeypatch.setattr(net.forks, '_current_source', lambda *_: source)
    assert net.twins._fork_to_rom(agent, proj, donor, {}, mine, job) == 0
    assert net._function(b['name'], slug)['status'] == 'open'
    source = CLAMP
    assert net.twins._fork_to_rom(agent, proj, donor, {}, mine, job) == 1
    matched = net._function(b['name'], slug)
    assert matched['status'] == 'matched'
    assert matched['target'] == b['target'] and matched['relocs'] == b['relocs']


def test_donor_build_retains_profile_and_header_only_after_exact_verdict(built, tmp_path, monkeypatch):  # noqa: F811
    from decompnet.app import donor_build, donor_sources
    from decompnet.app.twins import Job
    from decompnet.project import compiler_for

    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('autopilot')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    proj = net._project(slug)
    fn = net._function('func_800007D0', slug)
    own = compiler_for(net.cfg(proj), fn['vram'], fn)
    settings = {'toolchain': own['toolchain'], 'flags': [*own['flags'], '-g', '-DKEEP_DONOR_VALUE=1']}
    header = '#if !KEEP_DONOR_VALUE\n#error missing file define\n#endif\ntypedef int s32;\n#define KEEP_VALUE(x) (x)\n'
    donor = {'name': fn['name']}
    job = Job()
    wrong = 's32 func_800007D0(s32 x) { return KEEP_VALUE(x + 2); }'
    monkeypatch.setattr(donor_sources, 'imported', lambda *_: [(wrong, header, '')])
    assert donor_build.verify(net, proj, fn, proj, donor, {}, settings, agent, job, {})[0] == 'miss'
    assert net._function(fn['name'], slug)['unit'] == fn['unit']
    assert compiler_for(net.cfg(net._project(slug)), fn['vram'], fn) == own

    source = CLAMP.replace('return v;', 'return KEEP_VALUE(v);')
    monkeypatch.setattr(donor_sources, 'imported', lambda *_: [(source, header, '')])
    assert donor_build.verify(net, proj, fn, proj, donor, {}, settings, agent, job, {})[0] == 'match'
    matched = net._function(fn['name'], slug)
    assert net._context(matched['project_id'], matched['unit'])['header'] == header
    assert compiler_for(net.cfg(net._project(slug)), matched['vram'], matched)['flags'] == settings['flags']
    assert matched['status'] == 'matched'


def test_saved_whole_donor_unit_reverifies_and_exports_only_its_entry(built, tmp_path):  # noqa: F811
    from decompnet.app import donor_build, saves
    from decompnet.app.twins import Job
    from decompnet.project import compiler_for

    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('autopilot')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    proj = net._project(slug)
    fn = net._function('func_800007D0', slug)
    own = compiler_for(net.cfg(proj), fn['vram'], fn)

    root = tmp_path / 'donor'
    (root / 'build').mkdir(parents=True)
    (root / 'words.h').write_text('typedef int s32;\n')
    whole = ('#include "words.h"\n'
             "// The donor's preprocessing must preserve the next definition. \\\n"
             "this is still part of the line comment\n"
             'int other_body(int x) { return x + 31; }\n' + CLAMP)
    (root / 'unit.c').write_text(whole)
    (root / 'build/unit.c').write_text(whole)
    (root / 'objdiff.json').write_text(json.dumps({'units': [
        {'name': 'unit', 'base_path': 'build/unit.c.o', 'metadata': {'source_path': 'unit.c'}}
    ]}))
    donor = {'name': fn['name'], 'unit': 'unit', 'meta': None}
    settings = {'toolchain': own['toolchain'], 'flags': own['flags']}
    status, reason = donor_build.verify(net, proj, fn, {'root': str(root)}, donor, {}, settings, agent, Job(), {})
    assert status == 'match', reason
    assert reason == 'Verified from a saved donor translation unit.'
    saved = net._function(fn['name'], slug)
    assert 'other_body' in net._context(proj['id'], saved['unit'])['header']
    assert 'other_body' not in saved['matched_source']

    folder = tmp_path / 'saved'
    saves.save(net, slug, folder)
    shutil.rmtree(root)  # finished matches carry the C and include text independently of the donor checkout
    net.recheck(slug, log=lambda *_: None)
    assert net._function(fn['name'], slug)['status'] == 'matched'
    with net.progress.judging(slug, synchronize=False):
        assert net._verify_saved_match(
            proj['id'], fn['name'], saved['matched_source'], agent, False, lambda: None,
            previous_source=saved['matched_source'], removed_status=None,
        )
    out = net.export_rom(slug, log=lambda *_: None)
    assert out['identical'], out['log']
    assert not out['fallback']
    manifest = json.loads((Path(out['path']) / 'build.json').read_text())
    entry = next(row for row in manifest['functions'] if row['vram'] == fn['vram'])
    assert entry['entry_only']
