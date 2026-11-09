"""Android native import, archive limits and ARMv7 editing, using only homemade bytes (#133)."""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from decompnet import consoles, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.consoles.android import ANDROID, elf, package
from decompnet.consoles.android.split import Function, cut
from decompnet.project import function_settings
from decompnet.rom import RomError

from .test_ps2_debug import _elf, _symbols


@pytest.fixture(autouse=True)
def no_optional_decompiler_in_import_tests(monkeypatch):
    # These small ELF fixtures test imports and the judge. Real Ghidra is exercised in test_android_drafts.py.
    monkeypatch.setattr(ANDROID, 'drafts_ready', lambda: False)


def native(code=b'\x01\x20\x70\x47', *, thumb=True, entries=None):
    table, strings = _symbols(entries or [('answer', 0x1000 | thumb, len(code), 1, 2, 1)])
    data = bytearray(_elf([('.text', 1, 6, 0x1000, code, 0),
                          ('.symtab', 2, 0, 0, table, 3), ('.strtab', 3, 0, 0, strings, 0)]))
    struct.pack_into('<HH', data, 16, 3, 40)
    return bytes(data)


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for name, value in entries.items():
            z.writestr(name, value)
    return stream.getvalue()


def apk(lib=None):
    return archive({'AndroidManifest.xml': b'<manifest/>', 'lib/armeabi-v7a/libdemo.so': lib or native()})


def test_apk_xapk_and_native_elf_have_the_same_target(tmp_path):
    data = apk()
    xapk = archive({'manifest.json': b'{}', 'base.apk': data, 'Android/obb/main.obb': b'asset'})
    hashes = []
    for name, payload in [('game.apk', data), ('game.xapk', xapk), ('game.so', native())]:
        path = tmp_path / name
        path.write_bytes(payload)
        assert consoles.identify(path) is ANDROID
        assert consoles.identify(payload) is ANDROID
        picked = ANDROID.read_game(path)
        hashes.append(picked.info['sha1'])
        assert picked.data == native()
        assert picked.filename == 'game.so'
    assert len(set(hashes)) == 1


def test_only_the_largest_armv7_library_is_selected_and_scope_is_explicit():
    data = archive({'AndroidManifest.xml': b'', 'lib/armeabi-v7a/libsmall.so': native(),
                    'lib/armeabi-v7a/liblarge.so': native(b'\x00' * 20), 'lib/arm64-v8a/lib64.so': b'\0' * 100})
    picked = ANDROID.read_game(data)
    assert picked.info['library'].endswith('liblarge.so')
    assert picked.info['native_libraries'] == 2 and 'DEX' in picked.info['scope']


def test_archives_without_supported_native_code_explain_the_limit():
    data = archive({'AndroidManifest.xml': b'', 'classes.dex': b'dex\n'})
    assert consoles.identify(data) is ANDROID
    with pytest.raises(RomError, match='DEX and ARM64'):
        ANDROID.read_game(data)


def test_apk_member_limits_are_enforced_before_reading(monkeypatch):
    monkeypatch.setattr(package, 'MAX_LIBRARY_BYTES', 4)
    with pytest.raises(RomError, match='import limits'):
        ANDROID.read_game(apk())
    monkeypatch.setattr(package, 'MAX_APKS', 0)
    with pytest.raises(RomError, match='too many APKs'):
        ANDROID.read_game(archive({'manifest.json': b'{}', 'base.apk': apk()}))


def test_package_members_never_extract_archive_paths(tmp_path):
    data = archive({'AndroidManifest.xml': b'', '../escape': b'no',
                    'lib/armeabi-v7a/../../escape.so': b'no', 'lib/armeabi-v7a/libdemo.so': native()})
    ANDROID.read_game(data)
    assert list(tmp_path.iterdir()) == []


def test_sized_symbols_supply_real_addresses_modes_and_offsets(tmp_path):
    result = elf.split(native(), tmp_path, log=lambda *_: None)
    fn, = result.functions
    assert (fn.name, fn.vram, fn.thumb, fn.code) == ('answer', 0x1000, True, b'\x01\x20\x70\x47')
    assert native()[fn.rom:fn.rom + fn.size] == fn.code
    assert 'movs r0, #1' in fn.asm
    assert result.named['answer'] == 0x1000


def test_aliased_and_overlapping_symbols_are_not_counted_twice(tmp_path):
    entries = [('answer', 0x1001, 4, 1, 2, 1), ('alias', 0x1001, 4, 1, 2, 1),
               ('inner', 0x1003, 2, 1, 2, 1)]
    result = elf.split(native(entries=entries), tmp_path, log=lambda *_: None)
    assert len(result.functions) == 1


def test_a_fully_stripped_library_has_an_actionable_error(tmp_path):
    with pytest.raises(RomError, match='no sized function symbols'):
        elf.split(native(entries=[('data', 0x1000, 4, 1, 1, 1)]), tmp_path)


def test_thumb2_instructions_cannot_be_split_in_the_middle():
    fn = Function('wide', 0x1000, 52, bytes.fromhex('40 f2 01 00 70 47'), True)
    with pytest.raises(ValueError, match='instruction boundary'):
        cut(fn, 0x1002, 'tail')
    first, last = cut(fn, 0x1004, 'tail')
    assert first.code + last.code == fn.code and last.rom == 56


def test_each_function_keeps_its_mode_during_compiler_trials():
    cfg = {'platform': 'android', 'compiler': {'toolchain': 'android-clang-3.5', 'flags': ['-O2', '-mthumb']}}
    fn = {'vram': 0x1000, 'meta': '{"thumb": false}'}
    assert function_settings(cfg, fn)['flags'] == ['-O2', '-marm']
    assert function_settings(cfg, fn, {'toolchain': 'android-gcc-4.8', 'flags': ['-Os', '-mthumb']})['flags'] == ['-Os', '-marm']
    assert cfg['compiler']['flags'] == ['-O2', '-mthumb']


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_android_judges_actual_c_and_rejects_a_changed_return(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(), name='demo.apk', confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('native-test')
    net.claim(agent, slug, 'answer')
    wrong = net.attempt(agent, 'answer', 'int answer(void) { return 2; }', slug)
    assert not wrong['matched'], wrong['text']
    right = net.attempt(agent, 'answer', 'int answer(void) { return 1; }', slug)
    assert right['matched'] and right['score'] == 0, right['text']


def test_armv7_branch_decoder_reads_j_bits_and_blx_alignment():
    from decompnet.arch.arm import thumb2_bl_dest

    # Hand-encoded Thumb-2 BL: positive and negative displacements outside Thumb-1's range.
    def branch(displacement, bl=True):
        value = displacement & ((1 << 25) - 1)
        sign, i1, i2 = value >> 24, (value >> 23) & 1, (value >> 22) & 1
        first = 0xF000 | (sign << 10) | ((value >> 12) & 0x3FF)
        second = 0xC000 | (0x1000 if bl else 0) | ((~(i1 ^ sign) & 1) << 13) | ((~(i2 ^ sign) & 1) << 11) | ((value >> 1) & 0x7FF)
        return struct.pack('<HH', first, second)

    for displacement in (0, 4, -4, 0x800000, -0x800000):
        assert thumb2_bl_dest(branch(displacement), 0, 0x1000) == (0x1004 + displacement) & 0xFFFFFFFF
    assert thumb2_bl_dest(branch(4, False), 0, 0x1002) == 0x1008
    assert thumb2_bl_dest(b'\0\0\0\0', 0, 0) is None


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_supported_calls_preserve_opcode_bits_and_reject_other_callees(tmp_path):
    from decompnet.arch.arm import ArmTarget, link
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    from decompnet.rom.symbols import Symbol, SymbolTable

    settings = {'toolchain': 'android-clang-3.5', 'flags': ['-O2', '-marm', '-march=armv7-a']}
    cfg = compiler_config(tmp_path, {'compiler': settings})
    result = compile_source(cfg, 'extern void callee(void);', 'void caller(void) { callee(); }')
    assert result.ok, result.log
    candidate = extract_function(result.obj, 'caller')
    # Clang tail-calls with an ARM B relocation. Put the destination at caller+0x100.
    code = bytearray(candidate.code)
    code[:4] = struct.pack('<I', 0xEA000000 | ((0x100 - 8) >> 2))
    target = ArmTarget('caller', 0x1000, bytes(code), False, thumb2=True)
    syms = SymbolTable([Symbol('callee', 0x1100, 4, 'func')])
    assert link(result.obj, 'caller', target, syms, lambda *_: None).exact
    syms = SymbolTable([Symbol('callee', 0x1200, 4, 'func')])
    assert not link(result.obj, 'caller', target, syms, lambda *_: None).exact
    # A BL to the same address isn't the original B. The linker must not erase that difference.
    target.code = struct.pack('<I', 0xEB000000 | ((0x100 - 8) >> 2))
    syms = SymbolTable([Symbol('callee', 0x1100, 4, 'func')])
    assert not link(result.obj, 'caller', target, syms, lambda *_: None).exact


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_pic_relocations_fail_closed_instead_of_creating_false_matches(tmp_path):
    from decompnet.arch.arm import ArmTarget, link
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    from decompnet.rom.symbols import SymbolTable

    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'android-clang-3.5'}})
    result = compile_source(cfg, 'extern int value;', 'int getter(void) { return value; }')
    assert result.ok, result.log
    candidate = extract_function(result.obj, 'getter')
    target = ArmTarget('getter', 0x1000, candidate.code, True, thumb2=True)
    verdict = link(result.obj, 'getter', target, SymbolTable(), lambda *_: None)
    assert not verdict.exact and any('supported' in p or 'GOT' in p for p in verdict.problems)


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_android_shared_features_survive_polishing_saves_and_reimport(tmp_path):
    from decompnet.app import saves
    from decompnet.network import NetworkError

    game = tmp_path / 'demo.apk'
    game.write_bytes(apk())
    work = tmp_path / 'work'
    work.mkdir()
    net = RomNetwork(work / 'app.db', work)
    slug = net.import_rom(game, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('codex-gpt-6.1-1')
    assert 'answer' in net.claim(agent, slug, 'answer')
    source = 'int answer(void) { return 1; }'
    assert net.attempt(agent, 'answer', source, slug)['matched']
    assert 'MATCHES' in net.try_compiler(agent, slug, 'answer', 'android-clang-3.5', source=source)
    assert 'score' in net.search_flags(agent, slug, 'answer', source=source)
    net.set_compiler_ranges(slug, [[0x1000, 0x1004]], 'android-clang-3.5', ['-O2', '-marm'])
    assert net._function('answer', slug)['status'] == 'matched'  # range regression checks kept the Thumb match
    assert net.readability.submit(agent, slug, 'answer', 'int answer(void) { return 2; }').startswith('❌')
    assert net.readability.submit(agent, slug, 'answer', 'int answer(void) { int result = 1; return result; }').startswith('✅')
    assert net.rename_symbol(agent, slug, 'answer', 'get_answer').startswith('✅')
    net.add_note(agent, slug, 'Returns the demo answer.', 'get_answer')
    net.set_instructions(slug, 'Keep the shared types in the header.')
    assert net.recheck(slug, log=lambda *_: None) is not None
    assert net._function('get_answer', slug)['status'] == 'matched'
    with pytest.raises(NetworkError):
        net.get_class(slug)
    with pytest.raises(NetworkError):
        net.guess_struct(slug, 'get_answer')
    with pytest.raises(NetworkError, match='draft'):
        net.redraft(slug, 'get_answer', 'int get_answer(void);')
    saved = tmp_path / 'saved'
    assert saves.save(net, slug, saved)['matched'] == 1
    assert (saved / 'README.md').exists() and not list(saved.rglob('*.so'))
    fresh_work = tmp_path / 'fresh'
    fresh_work.mkdir()
    fresh = RomNetwork(fresh_work / 'app.db', fresh_work)
    restored = saves.restore(fresh, str(saved), game, log=lambda *_: None)
    assert restored['matched'] == 1 and not restored['lost']
    assert fresh._function('get_answer', restored['slug'])['status'] == 'matched'
    assert fresh.instructions(restored['slug']) == 'Keep the shared types in the header.'
    assert fresh.reimport_rom(restored['slug'], game, confirmed=True, log=lambda *_: None)['matched'] == 1
    assert fresh._function('get_answer', restored['slug'])['status'] == 'matched'


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_android_rooms_judge_guest_code_on_the_host(tmp_path):
    from starlette.testclient import TestClient

    from decompnet.app import collab

    game = tmp_path / 'demo.apk'
    game.write_bytes(apk())
    host_work, guest_work = tmp_path / 'host', tmp_path / 'guest'
    host_work.mkdir()
    guest_work.mkdir()
    host = RomNetwork(host_work / 'app.db', host_work)
    slug = host.import_rom(game, confirmed=True, log=lambda *_: None)
    host.rooms[slug] = collab.Room(host, slug)
    code = host.rooms[slug].open_invite()
    guest = RomNetwork(guest_work / 'app.db', guest_work)
    with TestClient(collab.host_app(host, host.rooms), base_url='https://demo.trycloudflare.com') as client:
        gslug = collab.join_room(guest, f'https://demo.trycloudflare.com/join/{code}#room={slug}', 'demo',
                                game, log=lambda *_: None, client=client, start=False)
        agent, _ = guest.local_agent('room-test')
        guest.claim(agent, gslug, 'answer')
        assert guest.attempt(agent, 'answer', 'int answer(void) { return 1; }', gslug)['matched']
        assert host._function('answer', slug)['status'] == 'matched'


def test_android_boundaries_splits_and_remote_layout_keep_thumb2_bytes(tmp_path):
    data = native(bytes.fromhex('40 f2 01 00 70 47 00 bf'))
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(data), confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('boundary-test')
    assert 'Reported' in net.request_boundary(agent, slug, 'answer', '0x1006', 'Alignment after the return.')
    net.resolve_boundary(slug, 1, True)
    assert net._function('answer', slug)['size'] == 6
    assert net.split_function(agent, slug, 'answer', '0x1004', 'tail').startswith('✅')
    row = net._function('tail', slug)
    assert row['target'] == bytes.fromhex('70 47') and ANDROID.function_meta(row)['thumb']


def test_android_header_knowledge_and_agent_verdicts_use_the_shared_pipeline(tmp_path):
    from decompnet.app import tools

    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(), confirmed=True, log=lambda *_: None)
    first, _ = net.local_agent('claude-opus-5.5-1')
    second, _ = net.local_agent('codex-gpt-6.1-1')
    note = 'The game keeps menu state in a shared structure.'
    net.knowledge.share(first, slug, note)
    net.knowledge.share(second, slug, note)
    assert net.knowledge.listing(slug)['items']
    assert 'answer' in tools.call(net, first, slug, 'get_function', {'function': 'answer'})


@pytest.mark.skipif(toolchains.installed('android-clang-3.5') is None, reason='needs pinned Android Clang 3.5')
def test_android_autopilot_uses_twins_without_claiming_machine_drafts(tmp_path):
    import time

    code = struct.pack('<II', 0xE3A00001, 0xE12FFF1E) * 2
    entries = [('answer', 0x1000, 8, 1, 2, 1), ('twin', 0x1008, 8, 1, 2, 1)]
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(native(code, entries=entries)), confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('twin-test')
    net.claim(agent, slug, 'answer')
    assert net.attempt(agent, 'answer', 'int answer(void) { return 1; }', slug)['matched']
    state = net.start_autopilot(slug)
    until = time.monotonic() + 10
    while state['state'] == 'running' and time.monotonic() < until:
        time.sleep(0.02)
    assert state['state'] == 'done' and state['no_drafts']
    assert net._function('twin', slug)['status'] == 'matched'


def test_android_agent_verdicts_set_aside_only_after_three_agents_agree(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(), confirmed=True, log=lambda *_: None)
    for index, name in enumerate(('claude-opus-5.5-1', 'claude-opus-5.5-2', 'opencode-gpt-6.1-1')):
        agent, _ = net.local_agent(name)
        net.claim(agent, slug, 'answer')
        net.release(agent, 'answer', 'Hand-written assembly; no C compiler emits this sequence.', slug, not_c=True)
        assert (net._function('answer', slug)['status'] == 'asm') == (index == 2)
    net.recheck(slug, log=lambda *_: None)
    assert net._function('answer', slug)['status'] == 'asm'


@pytest.mark.parametrize('compiler', ['android-clang-3.5', 'android-gcc-4.8', 'android-gcc-4.9'])
def test_each_pinned_android_compiler_judges_arm_mode(compiler, tmp_path):
    if toolchains.installed(compiler) is None:
        pytest.skip(f'needs {compiler}')
    data = native(struct.pack('<II', 0xE3A00001, 0xE12FFF1E), thumb=False)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    slug = net.import_rom(apk(data), toolchain=compiler, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('compiler-test')
    net.claim(agent, slug, 'answer')
    assert not net.attempt(agent, 'answer', 'int answer(void) { return 2; }', slug)['matched']
    right = net.attempt(agent, 'answer', 'int answer(void) { return 1; }', slug)
    assert right['matched'], right['text']


def test_other_consoles_keep_custom_commands_and_implicit_flags():
    cfg = {'platform': 'n64', 'compiler': {'cmd': ['custom-compiler', '{src}']}}
    assert function_settings(cfg, {'vram': 0x80001000}) == cfg['compiler']
    assert 'flags' not in cfg['compiler']
