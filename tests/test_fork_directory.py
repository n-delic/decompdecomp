"""The community decomp directory offers only decompilations, newest activity first (forks/directory.py)."""

from decompnet.forks import directory


def test_parse_keeps_only_decompilations():
    data = {"projects": {
        "a": {"name": "Old", "repository_url": "https://github.com/x/old", "project_type": "Decompilation",
              "activity": {"last_commit_at": "2020-01-01T00:00:00Z", "status": "Inactive"}},
        "b": {"name": "Port", "repository_url": "https://github.com/x/port", "project_type": "Port"},
        "c": {"name": "New", "repository_url": "https://gitlab.com/x/new", "project_type": "Decompilation",
              "activity": {"last_commit_at": "2026-01-01T00:00:00Z", "archived": True}},
        "d": {"name": "No url", "project_type": "Decompilation"},
    }}
    got = directory.parse(data, "n64")
    assert [p["name"] for p in got] == ["New", "Old"]
    assert got[0]["archived"] and got[0]["platform"] == "n64"
    assert got[1]["status"] == "Inactive"
