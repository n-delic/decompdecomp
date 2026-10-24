"""The engine's ground floor: the database rows of a project (its settings, its ROM, its symbols), finding a
function by any name an agent uses for it, and the identities of the agents that run on this computer.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from pathlib import Path

from ... import consoles
from ...network import Agent, Network, NetworkError, hash_token
from ...rom import split
from ...rom.symbols import Symbol, SymbolTable
from .common import _ADDRESS

# Settings parsed, by their text: GTA San Andreas's are 193 KB, and one request read them 4 to 12 times (a range
# check after a match, 462 times). A few versions are kept: each change of a project's settings is a new text.
SETTINGS_KEPT = 8
_settings: dict[str, dict] = {}
_settings_lock = threading.Lock()


class Core(Network):
    def __init__(self, db_path: str | Path, workspace: Path, claim_ttl: int = 30 * 60):
        super().__init__(db_path, claim_ttl=claim_ttl)
        self.workspace = Path(workspace)
        self._syms: dict[int, SymbolTable] = {}
        self._sym_override: dict[int, SymbolTable] = {}
        self._roms: dict[int, tuple[bytes, list[split.Segment]]] = {}
        self._xrefs: dict[int, dict] = {}
        self._draft_locks: dict[int, threading.Lock] = {}  # function id -> held while its machine draft is made
        self._headers: dict[tuple, object] = {}  # (project id, header version, for m2c) -> its declarations
        self._asm_names: dict[int, dict] = {}  # project id -> {function id: (size, the names its assembly mentions)}
        self.links: dict = {}  # slug -> collab.Link, for projects joined from a friend's room
        self.rooms: dict = {}  # slug -> collab.Room, for projects this computer shares
        self._remote_ids: dict[str, int] = {}
        self._ops: dict[int, tuple] = {}  # project id -> (the game's layout, look-alike index)
        self._warmed: dict[int, tuple] = {}  # project id -> the layout its briefs' caches were built for
        self._warming: dict[int, threading.Lock] = {}  # project id -> held while they build
        self._diffs: dict[tuple, dict] = {}  # (function, source, context, compiler) -> diff for the GUI
        self.autopilots: dict[str, dict] = {}  # slug -> progress of trying every function's machine draft
        self._cmaps: dict[int, tuple] = {}  # project id -> (what it was built from, compiler map)
        self._cmap_builds: dict[int, threading.Lock] = {}  # project id -> held while its compiler map builds
        self.surveys: dict[str, dict] = {}  # slug -> the judge's compiler survey, running or last
        from ..forkengine import ForkEngine  # forks build on this class's claims, attempts and live view
        from ..permute import Permuter
        from ..twins import Twins
        self.forks = ForkEngine(self)
        self.twins = Twins(self)
        self.permuter = Permuter(self)
        from ..rangecheck import RangeChecks
        self.rangechecks = RangeChecks(self)
        from ..knowledge import Knowledge
        self.knowledge = Knowledge(self)
        from ..readable import Readability
        self.readability = Readability(self)
        from ..drafts import Drafter
        self.drafter = Drafter(self)
        self.find_moved_projects()
        from ..progress import Progress
        self.progress = Progress(self)

    def _project(self, slug: str | None):
        row = super()._project(slug)
        if hasattr(self, "progress"):
            self.progress.sync(row["slug"])
            row = super()._project(row["slug"])
        return row

    @staticmethod
    def cfg(proj) -> dict:
        """A project's settings. Shared by everyone who reads the same version of them: read it, never change it
        (_update_cfg changes settings; code that edits a copy parses its own, json.loads(proj["config"]))."""
        text = proj["config"]
        with _settings_lock:
            parsed = _settings.get(text)
        if parsed is None:
            parsed = json.loads(text)
            with _settings_lock:
                if len(_settings) >= SETTINGS_KEPT:
                    _settings.pop(next(iter(_settings)))  # the oldest version
                _settings[text] = parsed
        return parsed

    def find_moved_projects(self) -> list[str]:
        """Projects whose folder isn't where it was, found again in this workspace by their name: a whole Decomp
        folder copied to another computer, user name or Linux keeps its projects (their folders were stored as
        absolute paths: /home/decomp/Decomp/... inside the old Windows install's WSL)."""
        moved = []
        for row in self.store.all("SELECT id, slug, root FROM projects"):
            here = self.workspace / row["slug"]
            if row["root"] and not Path(row["root"]).exists() and here.is_dir():
                with self.store.tx() as db:
                    cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (row["id"],)).fetchone()["config"])
                    if cfg.get("progress_folder") and Path(cfg["progress_folder"]).is_relative_to(Path(row["root"])):
                        cfg["progress_folder"] = str(here / Path(cfg["progress_folder"]).relative_to(Path(row["root"])))
                        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), row["id"]))
                    db.execute("UPDATE projects SET root=? WHERE id=?", (str(here), row["id"]))
                moved.append(row["slug"])
        return moved

    def is_rom(self, proj) -> bool:
        return self.cfg(proj).get("kind") == "rom"

    def console(self, proj) -> consoles.Console:
        """The project's console (consoles/): what the engine asks, instead of comparing platform names."""
        return consoles.of(self.cfg(proj))

    def project_row(self, slug: str):
        return self._project(slug)

    def recheck(self, slug: str, log=print, only_if_updated: bool = False) -> str | None:
        """Look at a project again with what this version of decompdecomp knows: its console's work for projects made
        before (Console.on_start: a DS project's functions found and its pieces of functions merged), the compiler
        range checks to weigh again, and code no compiler makes set aside (by its shape, or as agents' notes agree no C
        makes it). The "Check again" button; and before helpers start, once after each update (only_if_updated), so
        what a new version finds reaches every project that gets work, not every project at once. Returns what changed, as a sentence; None when nothing was done
        (checked by this version already, or a room member's copy: its host checks)."""
        from ... import __version__

        row = self.project_row(slug)
        if slug in self.links or (only_if_updated and self.cfg(row).get("checked") == __version__):
            return None
        if self.is_rom(row):
            self.rangechecks.reopen(slug)
        last = self.store.one("SELECT COALESCE(MAX(id), 0) AS id FROM events WHERE project_id=?", row["id"])["id"]
        self.console(row).on_start(self, slug, log=log)
        # what the console's work did, it said in the feed ("8 pieces of functions ... joined"): in the answer too
        done = [r["detail"] for r in self.store.all("SELECT detail FROM events WHERE project_id=? AND id>? AND "
                                                     "kind='analysis' ORDER BY id", row["id"], last)]
        n = self.not_c_from_notes(slug) + self.twins.set_aside_asm(self.project_row(slug))
        self._update_cfg(slug, lambda cfg: cfg.update(checked=__version__))
        if n:
            done.append(f"{n} more function{'' if n == 1 else 's'} set aside, as no C makes them")
        said = "; ".join(done) or "nothing new"
        with self.store.tx() as db:
            self._event(db, row["id"], None, "analysis", f"decompdecomp {__version__}: {said}")
        return said

    def fork_slug(self, project: str | None) -> str | None:
        """The slug when `project` is a fork of a public decomp, else None."""
        try:
            proj = self._project(project)
        except NetworkError:
            return None
        return proj["slug"] if self.cfg(proj).get("kind") == "fork" else None

    def _rom_only(self, project: str | None, what: str) -> None:
        if self.fork_slug(project):
            raise NetworkError(f"{what} is for ROM projects. In a fork, the project's own headers and source files are "
                               "the shared context: use read_file, search_code and edit_file")

    def symbols(self, project_id: int) -> SymbolTable:
        if project_id in self._sym_override:
            return self._sym_override[project_id]
        if project_id not in self._syms:
            rows = self.store.all("SELECT name, address, size, kind FROM symbols WHERE project_id=?", project_id)
            self._syms[project_id] = SymbolTable([Symbol(r["name"], r["address"], r["size"], r["kind"]) for r in rows])
        return self._syms[project_id]

    def _rom(self, proj) -> tuple[bytes, list[split.Segment]]:
        if proj["id"] not in self._roms:
            cfg = self.cfg(proj)
            rom = (Path(proj["root"]) / cfg["rom"]["file"]).read_bytes()
            segs = [split.Segment(**s) for s in cfg["segments"]]
            self._roms[proj["id"]] = (rom, segs)
        return self._roms[proj["id"]]

    def reader(self, proj):
        rom, segs = self._rom(proj)

        def read(addr: int, n: int) -> bytes | None:
            for seg in segs:
                if seg.contains(addr, n):
                    at = seg.rom_start + addr - seg.vram
                    return rom[at:at + n]
            return None
        return read

    def _function(self, name: str, project: str | None):
        """Functions can also be named by address (0x80001234), handy for agents reading assembly, and the way
        agents write names they read elsewhere (see _loose_functions)."""
        if project:
            self._project(project)
        m = _ADDRESS.match(name.strip()) if name else None
        if m:
            proj = self._project(project)
            row = self.store.one("SELECT * FROM functions WHERE project_id=? AND vram=?", proj["id"], int(m.group(1), 16))
            if row is not None:
                return row
        try:
            return super()._function(name, project)
        except NetworkError as e:
            rows = self._loose_functions(name, project) if name else []
            if len(rows) == 1:
                return rows[0]
            if rows:
                raise NetworkError(self._ambiguous(name, rows)) from None
            near = self._near_names(name, project) if name else []
            if near:
                raise NetworkError(f"{e}. Did you mean: {', '.join(near)}?") from None
            raise

    def _near_names(self, name: str, project: str | None, n: int = 5) -> list[str]:
        """Function names close to one that doesn't exist (a typo, another spelling of the symbol)."""
        import difflib

        try:
            proj = self._project(project)
        except NetworkError:
            return []
        sym = name.strip().strip("`'\"").split("@", 1)[0].split("(", 1)[0].strip()
        if len(sym) < 3:
            return []
        rows = self.store.all("SELECT name FROM functions WHERE project_id=? AND (name LIKE ? OR name LIKE ?) LIMIT 4000",
                              proj["id"], f"%{sym[-6:]}%", f"{sym[:4]}%")
        names = [r["name"] for r in rows]
        return difflib.get_close_matches(sym, names, n=n, cutoff=0.6) or [x for x in names if sym in x][:n]

    def _loose_functions(self, name: str, project: str | None) -> list:
        """Functions an agent may mean by a name that isn't stored as such: the plain symbol of a function whose
        name repeats across a fork's files (stored as symbol@unit), symbol@unit for one that doesn't repeat,
        Class::method, a name with its parameter list or in quotes."""
        try:
            proj = self._project(project)
        except NetworkError:
            return []
        text = name.strip().strip("`'\"").strip()
        sym, _, unit = text.partition("@")
        unit, _, nth = unit.partition("#")
        sym = sym.split("(", 1)[0].strip()
        if not sym:
            return []
        # A C++ function as written (`InitFog`, `CDriver::CalcSpeed`) is found by its demangled name, which starts
        # with its return type and convention (`bool __cdecl InitFog(...)`, `public: float __thiscall
        # CDriver::CalcSpeed(float)`): agents sent those and got "no function" back
        like = sym.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.store.all(
            "SELECT * FROM functions WHERE project_id=? AND (name=? OR substr(name,1,?)=? "
            "OR json_extract(meta,'$.symbol')=? OR json_extract(meta,'$.real')=? "  # (a PC game's: rename_symbol)
            "OR substr(json_extract(meta,'$.demangled'),1,?)=? "
            "OR json_extract(meta,'$.demangled') LIKE ? ESCAPE '\\') ORDER BY name",
            proj["id"], sym, len(sym) + 1, sym + "@", sym, sym, len(sym) + 1, sym + "(", f"% {like}(%")
        if not rows and "::" not in sym and len(sym) >= 4:  # a method by its own name: Class::sym
            rows = self.store.all("SELECT * FROM functions WHERE project_id=? AND json_extract(meta,'$.demangled') "
                                  "LIKE ? ESCAPE '\\' ORDER BY name", proj["id"], f"%::{like}(%")
        if unit:
            rows = ([r for r in rows if r["unit"] == unit] or [r for r in rows if r["unit"].endswith(unit)]
                    or [r for r in rows if r["unit"].startswith(unit)] or [r for r in rows if unit in r["unit"]])
        if nth.isdigit() and len(rows) > 1:
            rows = [r for r in rows if (json.loads(r["meta"] or "{}").get("nth", 0) + 1) == int(nth)] or rows
        return [r for r in rows if r["status"] != "nosource"] or rows  # ones in files without source can't be worked on

    @staticmethod
    def _ambiguous(name: str, rows: list) -> str:
        listing = "\n".join(f"- {r['name']} ({r['status']}, {r['n_insns']} instructions)" for r in rows[:12])
        more = f"\n... and {len(rows) - 12} more" if len(rows) > 12 else ""
        return (f"{name!r} is the name of {len(rows)} functions in different files; pass the full name:\n"
                f"{listing}{more}")

    def resolve_function(self, agent: Agent | None, project: str | None, name: str) -> str:
        """The stored name for what an agent calls a function: when a plain name fits several, the one the agent
        holds a claim on."""
        try:
            return self._function(name, project)["name"]
        except NetworkError:
            rows = self._loose_functions(name, project) if name else []
            held = self.store.all("SELECT f.id, f.unit FROM claims c JOIN functions f ON f.id=c.function_id "
                                  "WHERE c.agent_id=?", agent.id) if agent else []
            if held and len(rows) > 1:  # one it holds, else the one in the file (or module) it works in
                ids, units = {h["id"] for h in held}, {h["unit"] for h in held}
                modules = {u.split("/", 1)[0] for u in units}
                for pick in ([r for r in rows if r["id"] in ids], [r for r in rows if r["unit"] in units],
                             [r for r in rows if r["unit"].split("/", 1)[0] in modules]):
                    if len(pick) == 1:
                        return pick[0]["name"]
            raise

    def _update_cfg(self, slug: str, change) -> dict:
        """Change a project's config in one transaction (change(cfg) edits it in place)."""
        proj = self._project(slug)
        with self.store.tx() as db:
            cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
            change(cfg)
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        self.progress.sync(slug, force=True)
        return cfg

    def local_agent(self, name: str) -> tuple[Agent, str]:
        """An agent identity for one runner on this machine (created on first use)."""
        row = self.store.one("SELECT id FROM agents WHERE name=?", name)
        # Saves can credit room members (@name) or older helpers with long names. Those labels aren't valid
        # new runner names; keep existing identities, and give new ones a stable name without merging credits.
        if row is None and (not name or len(name) > 40 or not all(c.isalnum() or c in "-_." for c in name)):
            suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
            label = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._") or "restored"
            name = f"{label[:27]}-{suffix}"
            row = self.store.one("SELECT id FROM agents WHERE name=?", name)
        if row is not None:
            token = "dn_" + secrets.token_urlsafe(24)
            with self.store.tx() as db:
                db.execute("UPDATE agents SET token_hash=?, origin='local' "
                           "WHERE id=?", (hash_token(token), row["id"]))
        else:
            token = self.register_agent(name, "you", trusted=True, origin="local")
        return self.authenticate(token), token
