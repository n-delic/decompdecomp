"""Public build compiler declarations are read as data, with unresolved values left visible."""

import json

from decompnet.app.engine import RomNetwork
from decompnet.forks.compiler_info import describe
from decompnet.forks.project import ForkProject

MAKEFILE = """VERSION ?= us
ifeq ($(VERSION),pal)
  C_VERSION = 1
endif
RECOMP_FILE := ido-5.3-recomp-$(DETECTED_OS).tar.gz
RECOMP_DIR := tools/ido-recomp/$(DETECTED_OS)
RECOMP_CC := $(RECOMP_DIR)/cc
CC := $(RECOMP_CC)
CFLAGS := -c -G 0 $(OPT_FLAGS) $(MIPSBIT) -DVERSION=$(C_VERSION)
CFLAGS += -woff 649,654
OPT_FLAGS := -O2
MIPSBIT := -mips2
build/$(VERSION)/src/audio/%.c.o: OPT_FLAGS = -O3
"""


def fixture(root):
    (root / "Makefile").write_text(MAKEFILE)
    (root / "decompressed.pal.yaml").write_text(
        "options:\n  platform: n64\n  compiler: IDO\n  target_path: decompressed.pal.z64\nsegments: []\n")
    return ForkProject(root, create_state=False)


def test_reports_version_default_flags_and_target_overrides(tmp_path):
    info = describe(fixture(tmp_path), "pal")
    assert info["name"] == "IDO 5.3"
    flags = {f["variable"]: f["value"] for f in info["flags"]}
    assert "-O2 -mips2" in flags["CFLAGS"]
    assert "-woff 649,654" in flags["CFLAGS"]
    assert "$(C_VERSION)" in flags["CFLAGS"]  # conditional declarations are not guessed
    assert info["command"] == "tools/ido-recomp/$(DETECTED_OS)/cc"
    assert info["overrides"] == [{"pattern": "build/pal/src/audio/%.c.o", "variable": "OPT_FLAGS",
                                  "operator": "=", "value": "-O3"}]
    assert info["sources"] == ["Makefile", "decompressed.pal.yaml"]


def test_never_executes_shell_expansions_and_bounds_recursive_variables(tmp_path):
    project = fixture(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "Makefile").write_text(
        f"CC = $(shell touch {marker})\nA = $(B)\nB = $(A)\nCFLAGS = $(A) -O2\n")
    info = describe(project)
    assert not marker.exists()
    assert "$(shell touch" in info["command"]
    assert "$(A)" in info["flags"][0]["value"]


def test_manifest_precedence_and_malformed_splat(tmp_path):
    project = fixture(tmp_path)
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps(
        {"compiler": {"toolchain": "ido-7.1", "flags": ["-O1", "-mips2"]}}))
    info = describe(project)
    assert "7.1" in info["name"]
    assert info["flags"] == [{"variable": "flags", "value": "-O1 -mips2"}]
    (tmp_path / ".decomp.wtf.json").unlink()
    (tmp_path / "decompressed.pal.yaml").write_text("options: [\nsegments: []\n")
    assert describe(ForkProject(tmp_path, create_state=False))["name"] == "IDO 5.3"


def test_settings_overview_includes_declared_compiler_without_building(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    fixture(root)
    net = RomNetwork(tmp_path / "app.db", tmp_path / "workspace")
    net.forks._register("demo", "Demo", "", root,
                        {"kind": "fork", "platform": "n64", "version": "pal", "ready": False}, "test")
    overview = net.forks.overview("demo")
    assert overview["project_compiler"]["name"] == "IDO 5.3"
    assert overview["project_compiler"]["overrides"][0]["value"] == "-O3"
    assert overview["compiler"]["toolchain"] == "project"  # the build remains responsible for compiler choice


def test_conditional_ido_paths_agree_without_guessing_selected_command(tmp_path):
    project = fixture(tmp_path)
    (tmp_path/'Makefile').write_text('''TOOLS_DIR = tools
# old ido-7.1 is not evidence
ifeq ($(USE_QEMU_IRIX),1)
	CC := $(QEMU_IRIX) -L $(TOOLS_DIR)/ido5.3_compiler $(TOOLS_DIR)/ido5.3_compiler/usr/bin/cc
else
	CC := $(TOOLS_DIR)/ido-static-recomp/build/5.3/out/cc
endif
''')
    assert describe(project)['name'] == 'IDO 5.3'
    assert describe(project)['command'] == ''
    with (tmp_path/'Makefile').open('a') as stream:
        stream.write('OTHER_COMPILER = ido-7.1/cc\n')
    assert describe(project)['name'] == 'IDO'


def test_imported_n64_settings_include_header_and_target_signature_evidence(tmp_path, monkeypatch):
    from decompnet.app.twins import Print
    root = tmp_path/'checkout'; root.mkdir(); fixture(root)
    (root/'decompressed.pal.z64').write_bytes(bytes.fromhex('80371240') + bytes(8) + bytes.fromhex('00001446'))
    net = RomNetwork(tmp_path/'app.db', tmp_path/'workspace')
    net.forks._register('demo', 'Demo', '', root,
                        {'kind': 'fork', 'platform': 'n64', 'ready': False}, 'test')
    monkeypatch.setattr(net.twins, 'prints', lambda proj: {1: Print('sdk-key', [], 64)})
    with net.store.tx() as db:
        db.execute("INSERT INTO sdk_signatures(corpus,arch,big_endian,name,fingerprint,size) VALUES('libultra 2.0F / local','mips',1,'osHelper','sdk-key',64)")
    info = net.forks.overview('demo')['libultra']
    assert info['header_version'] == '2.0F'
    assert info['signature_candidates'][0]['fingerprints'] == 1
    (root/'decompressed.pal.z64').unlink()
    assert net.forks.overview('demo')['libultra']['header_version'] is None


def test_imported_libultra_reads_selected_original_instead_of_generated_target(tmp_path, monkeypatch):
    root = tmp_path/'checkout'; root.mkdir(); fixture(root)
    net = RomNetwork(tmp_path/'app.db', tmp_path/'workspace')
    net.forks._register('demo', 'Demo', '', root,
                        {'kind': 'fork', 'platform': 'n64', 'version': 'jp', 'ready': False}, 'test')
    from decompnet.forks.project import ForkProject
    monkeypatch.setattr(ForkProject, 'needs', lambda self, version=None: {'path': 'baserom.' + version + '.z64'})
    monkeypatch.setattr(net.twins, 'prints', lambda proj: {})
    (root/'baserom.jp.z64').write_bytes(bytes.fromhex('80371240') + bytes(8) + bytes.fromhex('00001446'))
    (root/'decompressed.pal.z64').write_bytes(bytes.fromhex('80371240') + bytes(8) + bytes.fromhex('00001449'))
    assert net.forks.overview('demo')['libultra']['header_version'] == '2.0F'


def test_safe_yaml_loader_fallback_preserves_compiler_declarations(tmp_path,monkeypatch):
    import yaml
    project = fixture(tmp_path)
    expected = describe(project)
    monkeypatch.delattr(yaml,'CSafeLoader',raising=False)
    assert describe(project) == expected
