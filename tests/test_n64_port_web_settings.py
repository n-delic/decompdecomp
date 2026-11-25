"""The browser build's settings menu (consoles/n64/port_wasm/web/settings.js) offers the window build's rows
(window/settings.h) with the same choices, in the same order, so a player finds the same menu on either; its model
(navigation, wrapping, what is kept) runs under node, with no page."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent / "src" / "decompnet" / "consoles" / "n64" / "port_wasm"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(script: str):
    done = subprocess.run(["node", "--input-type=module", "-e",
                           f'import * as s from "{(KIT / "web" / "settings.js").as_uri()}";\n{script}'],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _c_rows():
    text = (KIT / "window" / "settings.h").read_text()
    rows = re.findall(r'\{"(\w+)", "(\w+)", "([^"]+)", PS_CHOICE, (\d+), \{([^}]*)\},\s*(\d+), (\d+),', text)
    return [{"section": s.lower(), "key": k, "label": label.lower(), "values": re.findall(r'"([^"]*)"', v),
             "fallback": int(fallback)} for s, k, label, _n, v, _value, fallback in rows]


def test_the_browser_menu_offers_the_window_builds_rows_in_the_same_order():
    js = _node("console.log(JSON.stringify(s.ROWS));")
    web = [{"section": r["section"].lower(), "key": r["key"], "label": r["label"].lower(), "values": r["values"],
            "fallback": r["fallback"]} for r in js]
    assert web == _c_rows()


def test_the_model_moves_wraps_changes_and_keeps_only_what_a_browser_can_restore():
    out = _node("""
const m = s.menuModel({ volume: "30", smooth: "NOPE", fullscreen: "ON" });
const log = {};
log.start = [m.get("volume"), m.get("smooth"), m.get("fullscreen"), m.get("fps")];  // bad and unkeepable values ignored
m.open = true;
log.up = (m.input(s.NAV.UP), m.row);                       // wraps to Resume
log.resume = m.input(s.NAV.ACCEPT);                        // Resume closes
m.open = true; m.row = 1;
log.right = [m.input(s.NAV.RIGHT).changed, m.get("smooth")];
log.left = [m.input(s.NAV.LEFT).changed, m.get("smooth")];
m.row = 3; for (let i = 0; i < 11; i++) m.input(s.NAV.RIGHT);   // eleven choices: back where it began
log.wrap = m.get("volume");
log.kept = m.kept();
log.set = [m.set("fullscreen", "ON"), m.set("fullscreen", "ON"), m.set("fullscreen", "MAYBE")];
log.back = (m.open = true, m.input(s.NAV.BACK).action, m.open);
console.log(JSON.stringify(log));
""")
    assert out["start"] == ["30", "SHARP", "OFF", "ON"]
    assert out["up"] == 4 and out["resume"] == {"changed": None, "action": "resume"}
    assert out["right"] == ["smooth", "SMOOTH"] and out["left"] == ["smooth", "SHARP"]
    assert out["wrap"] == "30"
    assert out["kept"] == {"smooth": "SHARP", "fps": "ON", "volume": "30"}  # full screen is not kept
    assert out["set"] == [True, False, False] and out["back"] is False


def test_the_page_wires_the_menu_to_its_controls_and_the_export_ships_it():
    play = (KIT / "web" / "play.js").read_text()
    page = (KIT / "web" / "index.html").read_text()
    assert 'import { menuModel, mountMenu } from "./settings.js"' in play
    assert "if (menu && menu.model.open) return 0;" in play      # the game gets no input while it is open
    assert 'id="settings"' in page and "#play:fullscreen" in page  # a button for touch, and the menu is in the full screen element
    assert '"settings.js"' in (KIT.parents[2] / "port" / "wasm" / "web.py").read_text()
