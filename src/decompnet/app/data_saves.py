"""The game's data in C, saved with the project's progress and restored from it (docs/PORTING.md, phase 1).

Matched data units live in the project's database (app/data_units.py); without this a save, a restore or a clone of
the progress repository lost them. They go beside the functions' C, in files of their own so the progress folder's
two-way sync (app/progress.py) and its manifest stay as they are:
- `data.json`: which data units are in C, by name, with the address, size and extent the judge kept;
- `data/<name>.c`: each one's C.
A match writes its file and the index at once; a full save writes them all and removes what isn't matched any more.
A restore judges every saved definition again, as it does matched functions: a datum counts because it holds the
game's bytes, not because a file says so. Progress commits own these files (repository_progress.owned).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..network import NetworkError
from . import data_units, saves

INDEX = "data.json"
FOLDER = "data"
FORMAT = 1
log = logging.getLogger("decompdecomp")


def _units(net, proj) -> dict[str, dict]:
    """Matched units by their current name: {"address", "size", "span", "source"}."""
    symbols = net.symbols(proj["id"])
    rows = data_units._store(net, proj).all(
        "SELECT address, size, span, source FROM data_units WHERE project_id=? AND status='matched' ORDER BY address",
        proj["id"])
    return {symbols.name_at(r["address"])[0]: {"address": r["address"], "size": r["size"], "span": r["span"],
                                                "source": r["source"]} for r in rows}


def _file(name: str) -> str:
    return f"{FOLDER}/{saves._source_file(name)}"


def render(net, proj) -> dict[str, str]:
    """Every file the save owns for the project's data in C."""
    units = _units(net, proj)
    index = {"format": FORMAT, "game": net.cfg(proj).get("rom", {}).get("sha1"),
             "data": {name: {"address": f"0x{u['address']:08X}", "size": u["size"], "span": u["span"],
                             "file": _file(name)} for name, u in units.items()}}
    files = {INDEX: json.dumps(index, indent=1) + "\n"}
    files.update({_file(name): saves._source_text(u["source"]) for name, u in units.items()})
    return files


def owned(folder: Path) -> set[str]:
    """The files a previous save's data index says it wrote."""
    path = folder / INDEX
    if not path.is_file():
        return set()
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        return {INDEX} | {u["file"] for u in index["data"].values() if isinstance(u.get("file"), str)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise NetworkError(f"Fix {INDEX} in the progress folder before saving its data again.") from None


def publish(net, slug: str, folder: Path | None = None, *, names: list[str] | None = None) -> bool:
    """Write the data's C to the progress folder: every file, or (`names`) those units' and the index. Only a
    folder that already holds a save (its manifest): the project's first save makes it."""
    proj = data_units._project(net, slug)
    folder = Path(folder) if folder is not None else saves.default_folder(net, slug)
    if not (folder / saves.MANIFEST).is_file():
        return False
    files = render(net, proj) if _units(net, proj) else {}  # nothing in C: no index (and an old one goes)
    previous = {name: (folder / name).read_text(encoding="utf-8") for name in owned(folder) if (folder / name).is_file()}
    if names is not None:  # a match: its file and the index, nothing else read or removed
        wanted = {INDEX} | {_file(n) for n in names}
        files = {k: v for k, v in files.items() if k in wanted}
        previous = {k: v for k, v in previous.items() if k in files}
    return saves._publish(folder, files, previous, chosen=True)


def matched(net, slug: str, name: str) -> None:
    """After a match: save it at once, like a matched function. A save that can't be written doesn't undo the match;
    the next full save writes it."""
    try:
        publish(net, slug, names=[name])
    except (OSError, NetworkError) as error:
        log.warning("couldn't save data unit %s of %s to its progress folder: %s", name, slug, error)


def restore(net, slug: str, folder: Path, log=print) -> dict:
    """Judge every saved data unit again and keep what holds the game's bytes: {"restored", "failed", "saved"}."""
    path = Path(folder) / INDEX
    if not path.is_file():
        return {"restored": 0, "failed": 0, "saved": 0}
    proj = data_units._project(net, slug)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        saved = index["data"]
    except (OSError, ValueError, KeyError, TypeError):
        raise NetworkError(f"The saved {INDEX} can't be read. Fix it, or remove it to restore without the data's C.") from None
    if index.get("game") not in (None, net.cfg(proj).get("rom", {}).get("sha1")):
        raise NetworkError(f"The saved {INDEX} belongs to another game.")
    restored = failed = 0
    for name, unit in saved.items():
        file = Path(folder) / str(unit.get("file", ""))
        if not file.is_file() or not file.resolve().is_relative_to(Path(folder).resolve() / FOLDER):
            failed += 1
            continue
        address = int(str(unit.get("address", "0")), 16)
        symbol = net.symbols(proj["id"]).name_at(address)[0]  # by address: the name may have changed since
        with net.progress.view():
            result = data_units.compile_data(net, slug, symbol, file.read_text(encoding="utf-8"), save=False)
        if result.exact:
            restored += 1
        else:
            failed += 1
            log(f"saved data {name} doesn't hold the game's bytes any more: {result.problems[0] if result.problems else ''}")
    if saved:
        log(f"data in C: {restored} of {len(saved)} saved units restored" + (f", {failed} didn't match" if failed else ""))
    return {"restored": restored, "failed": failed, "saved": len(saved)}
