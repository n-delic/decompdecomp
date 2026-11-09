"""Local archive signatures name functions without counting signatures as verified C."""

from pathlib import Path

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.libultra import name_functions, report
from decompnet.app.sdk_library import choose

from .test_rom import ROM, built  # noqa: F401


@pytest.mark.parametrize("conflicting,library_name,expected", [(False, "osDemoClamp", 1),
                                                             (True, "osDemoClamp", 0), (False, "memcpy", 0)])
def test_local_library_names_need_one_unambiguous_signature(built, tmp_path, conflicting, library_name, expected):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("tester")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    proj = net.project_row(slug)
    fn = net._function("func_800007D0", slug)
    p = net.twins.prints(proj)[fn["id"]]
    with net.store.tx() as db:
        for name in ([library_name, "osOtherMeaning"] if conflicting else [library_name]):
            db.execute("INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)",
                       ("libultra 2.0I / libultra_rom.a", "mips", 1, name, p.key, p.size))
    choose(net, slug, [])
    assert name_functions(net, slug) == 0
    choose(net, slug, None)
    assert name_functions(net, slug) == expected
    if expected:
        detail = net.function_detail(slug, library_name)
        assert detail["named"] and detail["renamed"]
        assert detail["naming"]["method"] == "sdk_signature"
        assert detail["status"] == "open"  # a name is not a decompilation match
        assert name_functions(net, slug) == 0
    path = Path(proj["root"]) / net.cfg(proj)["rom"]["file"]
    data = bytearray(path.read_bytes())
    data[12:16] = bytes.fromhex("00001449")
    path.write_bytes(data)
    choose(net, slug, [])
    assert report(net, net.project_row(slug))["signature_candidates"] == []
    choose(net, slug, None)
    result = report(net, net.project_row(slug))
    assert result["header_version"] == "2.0I" and result["header_raw"] == "00001449"
    assert result["signature_candidates"][0]["fingerprints"] == 1
    assert "not proof" in result["note"]
    data[:4] = b"nope"
    path.write_bytes(data)
    assert report(net, proj) is None


def test_non_libultra_sdk_name_preserves_a_real_linked_match(built, tmp_path):  # noqa: F811
    from decompnet.app.sdk_names import name_functions as name_sdk_functions

    from .test_app_engine import CLAMP

    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('tester')
    net.split_function(agent, slug, 'func_800007C4', '0x800007D0')
    assert net.compile_and_diff(agent, 'func_800007D0', CLAMP, slug).startswith('✅ MATCH')
    proj = net.project_row(slug)
    fn = net._function('func_800007D0', slug)
    p = net.twins.prints(proj)[fn['id']]
    with net.store.tx() as db:
        db.execute('INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES(?,?,?,?,?,?)',
                   ('Example SDK / support.a', 'mips', 1, 'sdkClamp', p.key, p.size))
    assert name_sdk_functions(net, slug) == 1
    renamed = net._function('sdkClamp', slug)
    assert renamed['status'] == 'matched' and renamed['best_score'] == 0
    assert 'sdkClamp' in renamed['matched_source']
    current = net.project_row(slug)
    compiler = net.cfg(current)['compiler']
    assert net._judge_with(current, renamed, compiler['toolchain'], compiler['flags'], renamed['matched_source'])[0]
