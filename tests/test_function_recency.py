"""The function list dates describe attempt history, including failed checks."""

import json

from decompnet.app.engine import RomNetwork


def test_function_map_reports_last_check_and_last_real_improvement(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute(
            "INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
            ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"})),
        ).lastrowid
        ids = {}
        for name in ("improved", "never_improved", "untouched"):
            ids[name] = db.execute(
                "INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                (pid, name, 40, 10, "open", "a"),
            ).lastrowid
        for name, score, compiled, at in (
            ("improved", 900, 1, 10), ("improved", 950, 1, 20),
            ("improved", 700, 1, 30), ("improved", None, 0, 40),
            ("never_improved", None, 0, 50),
        ):
            db.execute(
                "INSERT INTO attempts(function_id,score,compiled,created_at) VALUES(?,?,?,?)",
                (ids[name], score, compiled, at),
            )
    rows = {row["name"]: row for row in net.function_map("game")}
    assert (rows["improved"]["last_improved"], rows["improved"]["last_checked"]) == (30, 40)
    assert (rows["never_improved"]["last_improved"], rows["never_improved"]["last_checked"]) == (None, 50)
    assert (rows["untouched"]["last_improved"], rows["untouched"]["last_checked"]) == (None, None)
