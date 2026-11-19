"""Function-map colours: map-only overrides, validation and their settings page hooks."""

import json

import pytest

from decompnet.app.support import MAP_COLOR_KEYS, Settings

from .test_app_server import app  # noqa: F401  shared isolated server fixture

HEADERS = {"X-Decomp": "1"}


def test_map_colors_are_validated_persisted_and_never_part_of_other_settings(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(path)
    assert settings.public()["map_colors"] == {}

    colors = settings.save_map_colors({"open": "#FF0000", "cc0": "#123aBc"})
    assert colors == {"open": "#ff0000", "cc0": "#123abc"}
    assert settings.public()["map_colors"] == colors
    settings.save()
    assert json.loads(path.read_text())["map_colors"] == colors

    for bad in ({"open": "red"}, {"open": "#fff"}, {"unknown": "#112233"}, {"cc0": "url(#x)"}, []):
        with pytest.raises(ValueError):
            settings.save_map_colors(bad)
    assert settings.public()["map_colors"] == colors  # a rejected palette changes nothing

    # A corrupt or old settings file cannot leak arbitrary text into the page.
    path.write_text(json.dumps({"map_colors": {"open": "expression(alert(1))", "tried": "#abcdef"}}))
    assert Settings(path).public()["map_colors"] == {"tried": "#abcdef"}
    assert settings.save_map_colors(None) == {}


def test_settings_route_rejects_bad_colors_before_saving_anything(request):
    client, _, _ = request.getfixturevalue("app")
    bad = client.post("/api/settings", headers=HEADERS, json={"per_session": 4, "map_colors": {"match": "#fff"}})
    assert bad.status_code == 400, bad.text
    assert client.get("/api/state").json()["settings"]["per_session"] != 4

    saved = client.post("/api/settings", headers=HEADERS,
                        json={"map_colors": {"match": "#22AA55", "cc1": "#123456"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["map_colors"] == {"match": "#22aa55", "cc1": "#123456"}
    assert client.get("/api/state").json()["settings"]["map_colors"] == saved.json()["map_colors"]

    reset = client.post("/api/settings", headers=HEADERS, json={"map_colors": {}})
    assert reset.status_code == 200 and reset.json()["map_colors"] == {}


def test_each_map_state_has_its_own_overridable_css_variable():
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "src/decompnet/app/static/app.css").read_text()
    expected = ["open", "tried", "close", "work", "match", "polished", "aside", "not_c", "ink"] + [
        f"cc{i}" for i in range(8)
    ]
    assert list(MAP_COLOR_KEYS) == expected
    for key in expected:
        name = f"cc-{key[2:]}" if key.startswith("cc") else key.replace("_", "-")
        assert f"--map-{name}:" in css
    for selector in (".sw.open", ".sw.tried", ".sw.close", ".sw.work", ".sw.match", ".sw.polished",
                     ".sw.asm", ".sw.not-c", ".s-open", ".s-tried", ".s-match"):
        assert "var(--map-" in css.split(selector, 1)[1].split("}", 1)[0], selector
