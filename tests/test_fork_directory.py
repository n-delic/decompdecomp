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


def test_directory_rom_checksums_are_validated():
    digest = "a1" * 20
    entry = {"name": "G", "repository_url": "https://github.com/example/game", "project_type": "Decompilation",
             "platforms": ["Nintendo 64"], "supported_rom_sha1": [digest.upper(), digest, "invalid", None]}
    assert directory.parse([entry])[0]["rom_sha1s"] == [digest]
    assert directory.parse([entry | {"supported_rom_sha1": digest}])[0]["rom_sha1s"] == []


def test_illustrated_directory_joins_artwork_by_repository(monkeypatch):
    import httpx

    cover = "https://raw.githubusercontent.com/libretro-thumbnails/example/master/cover.png"
    projects = [
        {"name": "Game", "repository_url": "https://github.com/example/game",
         "project_type": "Decompilation", "platforms": ["Nintendo 64"]},
        {"name": "Alternate decomp", "repository_url": "https://github.com/example/alternate",
         "project_type": "Decompilation", "platforms": ["Nintendo 64"]},
        {"name": "Game", "repository_url": "https://github.com/example/other",
         "project_type": "Decompilation", "platforms": ["Nintendo 64"]},
        {"name": "Port", "repository_url": "https://github.com/example/port",
         "project_type": "Native port", "platforms": ["Nintendo 64"]},
        {"name": "Recomp", "repository_url": "https://github.com/example/recomp",
         "project_type": "Static recompilation", "platforms": ["Nintendo 64"]},
    ]
    html = f"""
    <table><tr><td><div class="rr-decomp-artwork"><img src="{cover}" /></div>
    <div class="rr-decomp-name"><a href="https://github.com/example/game.git/">Game</a></div></td>
    <td><a href="https://github.com/example/alternate">Matching decompilation</a>
    <a href="https://github.com/example/port">Native port</a>
    <a href="https://github.com/example/recomp">Static recompilation</a></td></tr>
    <tr><td><div class="rr-decomp-artwork"><img src="https://untrusted.example/cover.png" /></div>
    <a href="https://github.com/example/other">Game</a></td></tr></table>
    """

    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        if url == directory.DIRECTORY:
            return httpx.Response(200, json=projects, request=request)
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(directory.httpx, "get", get)
    assert {p["repo_url"].rsplit("/", 1)[-1]: p.get("artwork_url") for p in directory.listing()} == {
        "game": cover, "alternate": cover, "other": None,
    }


def test_directory_still_loads_when_artwork_page_is_unavailable(monkeypatch):
    import httpx

    entry = {"name": "Game", "repository_url": "https://github.com/example/game",
             "project_type": "Decompilation", "platforms": ["Nintendo 64"]}

    def get(url, **kwargs):
        if url == directory.DIRECTORY_PAGE:
            raise httpx.ReadTimeout("Artwork timed out")
        return httpx.Response(200, json=[entry], request=httpx.Request("GET", url))

    monkeypatch.setattr(directory.httpx, "get", get)
    assert directory.listing() == directory.parse([entry])
