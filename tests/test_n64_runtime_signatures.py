"""Runtime assembly that cartridges share but no SDK archive covers is found by its shipped fingerprints."""
import re
from pathlib import Path
from types import SimpleNamespace

from decompnet import consoles
from decompnet.app.engine import RomNetwork
from decompnet.app.sdk_identity import identify
from tests.test_sdk_builtin import _project

TABLE = Path(consoles.get("n64").sdk_table).with_name("runtime_signatures.tsv")


def _rows():
    return [line.split("\t") for line in TABLE.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]


def test_the_table_is_registered_and_holds_only_hashes_sizes_and_names():
    assert str(TABLE) in consoles.get("n64").sdk_tables
    rows = _rows()
    assert {name for _, _, name, _ in rows} == {"f64ToFixed60", "fixed60ToF64"}
    for fingerprint, size, name, _ in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", fingerprint) and int(size) >= 32 and name


def test_a_game_with_a_shared_runtime_function_gets_its_name_with_no_sdk_of_the_users(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    proj = _project(net, tmp_path)
    (fingerprint, size, name, _), *_ = _rows()
    net.twins.prints = lambda p: {7: SimpleNamespace(key=fingerprint, size=int(size), asm="hand-written assembly")}
    found = identify(net, proj, {}, include_asm=True)
    assert found[7]["names"] == [name] and found[7]["corpora"] == ["built in / n64-runtime-assembly 1"]
    # the same bytes as a little-endian game's are not the N64's
    assert identify(net, _project(net, tmp_path, "other", big_endian=0), {}, include_asm=True) == {}
