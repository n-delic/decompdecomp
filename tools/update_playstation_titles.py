"""Regenerate offline title metadata from a pinned, CC BY-SA 4.0 libretro database snapshot."""
import json
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

REVISION = "c4e17d41ec1bb744fb064f7cefeb61b878fc5d64"
BASE = f"https://raw.githubusercontent.com/libretro/libretro-database/{REVISION}/"
DEST = Path(__file__).resolve().parents[1] / "src/decompnet/consoles/playstation_titles"


def extract(text):
    candidates = {}
    for block in re.split(r"\n?game \(\n", text)[1:]:
        name = re.search(r'^\s*name "([^"]+)"', block, re.M)
        serial = re.search(r'^\s*serial "([^"]+)"', block, re.M)
        if not name or not serial:
            continue
        # Revisions of one release share a serial; they should still get its title.
        title = re.sub(r" \((?:Rev|v) [^)]*\)", "", name[1])
        for code in re.findall(r"[A-Z]{4}-\d{5}(?![\d-])", serial[1]):
            candidates.setdefault(code, set()).add(title)
    # A reused serial is not sufficient evidence to choose between different games.
    return {code: next(iter(names)) for code, names in sorted(candidates.items()) if len(names) == 1}


def main():
    DEST.mkdir(exist_ok=True)
    for platform, system in (("ps", "PlayStation"), ("ps2", "PlayStation 2")):
        path = f"metadat/redump/Sony - {system}.dat"
        with urlopen(BASE + quote(path), timeout=60) as response:
            titles = extract(response.read().decode("utf-8"))
        (DEST / f"{platform}.json").write_text(json.dumps(titles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{platform}: {len(titles)} titles")
    with urlopen(BASE + "LICENSE", timeout=60) as response:
        (DEST / "LICENSE").write_bytes(response.read())


if __name__ == "__main__":
    main()
