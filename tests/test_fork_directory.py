"""The community decomp directory offers only decompilations on supported consoles, newest first."""

from decompnet.forks import directory


def test_parse_keeps_only_supported_decompilations():
    data = {"projects": {
        "a": {"name": "Old", "repository_url": "https://github.com/x/old", "project_type": "Decompilation",
              "platforms": ["Nintendo 64"], "activity": {"last_commit_at": "2020-01-01T00:00:00Z", "status": "Inactive"}},
        "b": {"name": "Port", "repository_url": "https://github.com/x/port", "project_type": "Port",
              "platforms": ["Nintendo 64"]},
        "c": {"name": "New", "repository_url": "https://gitlab.com/x/new", "project_type": "Decompilation",
              "platforms": ["PlayStation 1"], "activity": {"last_commit_at": "2026-01-01T00:00:00Z", "archived": True}},
        "d": {"name": "No url", "project_type": "Decompilation", "platforms": ["Nintendo 64"]},
        "e": {"name": "Amiga", "repository_url": "https://github.com/x/a", "project_type": "Decompilation",
              "platforms": ["Amiga"]},
    }}
    got = directory.parse(data)
    assert [p["name"] for p in got] == ["New", "Old"]
    assert got[0]["archived"] and got[0]["platform"] == "ps" and got[1]["platform"] == "n64"
    assert got[1]["status"] == "Inactive"


def test_parse_accepts_a_plain_list():
    got = directory.parse([{"name": "G", "repository_url": "https://github.com/x/g", "project_type": "Decompilation",
                            "platforms": ["Nintendo GameCube"]}])
    assert got[0]["platform"] == "gc"


def test_parse_reads_the_combined_file_by_console():
    entry = {"name": "G", "repository_url": "https://github.com/x/g", "project_type": "Decompilation",
             "platforms": ["Nintendo 64"]}
    got = directory.parse({"nintendo-64": {"schema_version": 1, "projects": {"g": entry}},
                           "amiga": {"schema_version": 1, "projects": {}}})
    assert [p["platform"] for p in got] == ["n64"]
