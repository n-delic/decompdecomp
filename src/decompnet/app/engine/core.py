"""The engine's ground floor: the database rows of a project (its settings, its ROM, its symbols), finding a
function by any name an agent uses for it, and the identities of the agents that run on this computer.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ... import consoles, hostenv
from ...network import Agent, Network, NetworkError, hash_token
from ...rom import split
from ...rom.symbols import Symbol, SymbolTable
from .common import _ADDRESS

FORK_SLUG_SECONDS = 10.0  # how long fork_slug keeps its answer (a project's kind never changes)

class Core(Network):
    def sdk_functions(self, proj) -> dict:
        from ..sdk_identity import identify
        return identify(self, proj, super().sdk_functions(proj))

    def __init__(self, db_path: str | Path, workspace: Path, claim_ttl: int = 30 * 60, *, project_databases=False):
        super().__init__(db_path, claim_ttl=claim_ttl,project_databases=project_databases)
        self.workspace = Path(workspace)
        from .. import repair_rules
        repair_rules.configure(self.workspace)   # the library's declarative rules live in the home (DECOMP_REPAIR_RULES)
        self._syms: dict[int, SymbolTable] = {}
        self._sym_override: dict[int, SymbolTable] = {}
        # project id -> held by one rename at a time, from reading the symbols to writing the new name: two at once
        # replaced each other's trial symbol table and checked sources the other had just changed.
        self._rename_locks: dict[int, threading.RLock] = {}
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
        self._compiler_comparison_locks: dict[str, threading.Lock] = {}
        from ..forkengine import ForkEngine  # forks build on this class's claims, attempts and live view
        from ..permute import Permuter
        from ..twins import Twins
        self.forks = ForkEngine(self)
        self.twins = Twins(self)
        from ..match_library import Library
        self.match_library = Library(self)
        self.permuter = Permuter(self)
        from ..rangecheck import RangeChecks
        self.rangechecks = RangeChecks(self)
        from ..filecompilers import FileCompilers
        self.filecompilers = FileCompilers(self)
        from ..knowledge import Knowledge
        self.knowledge = Knowledge(self)
        from ..readable import Readability
        self.readability = Readability(self)
        from ..drafts import Drafter
        self.drafter = Drafter(self)
        from ..reuse_cascade import Cascade
        self.cascades = Cascade(self)
        from ... import store as storage
        from ..progress import Progress
        if storage.ATTACH_ONLY:
            # A reader process (app/tool_reader.py): the app moves projects, runs repair analyses and keeps the
            # progress folders; another process doing any of it would race the app's own.
            self.progress = Progress(self)
            return
        self.find_moved_projects()
        self.progress = Progress(self)
        from .. import repair_outcomes
        repair_outcomes.prepare(self.repair_feedback)
        repair_outcomes.resume(self.repair_feedback)

    def _project(self, slug: str | None):
        row = super()._project(slug)
        if hasattr(self, "progress") and not self.progress.viewing and self.project_missing(row) is None:
            self.progress.sync(row["slug"], background=True)  # (a look at it, not use: Progress.hot)
            row = super()._project(row["slug"])
        return row

    @staticmethod
    def cfg(proj) -> dict:
        """A project's settings. Shared by everyone who reads the same version of them: read it, never change it
        (_update_cfg changes settings; code that edits a copy parses its own, json.loads(proj["config"]))."""
        from ...project import settings
        return settings(proj["config"])

    def find_moved_projects(self) -> list[str]:
        """Projects whose folder isn't where it was, found again in this workspace by their name: a whole Decomp
        folder copied to another computer, user name or Linux keeps its projects (their folders were stored as
        absolute paths: /home/decomp/Decomp/... inside the old Windows install's WSL)."""
        moved = []
        for catalog in self.store.all("SELECT id, slug, root FROM projects"):
            store = self.project_store(catalog['id'])
            row = catalog if store is self.store else store.one('SELECT id,slug,root FROM projects WHERE id=?', catalog['id'])
            here = self.workspace / row["slug"]
            if row["root"] and not Path(row["root"]).exists() and here.is_dir():
                with store.tx() as db:
                    cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (row["id"],)).fetchone()["config"])
                    old_root = Path(row["root"])
                    for key in ("progress_folder", "save_folder"):
                        if cfg.get(key) and Path(cfg[key]).is_relative_to(old_root):
                            cfg[key] = str(here / Path(cfg[key]).relative_to(old_root))
                        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), row["id"]))
                    db.execute("UPDATE projects SET root=? WHERE id=?", (str(here), row["id"]))
                moved.append(row["slug"])
        return moved

    def project_missing(self, proj) -> str | None:
        """Why the project's folder isn't on disk, in a sentence, or None when it is there.

        Its folder holds the game and everything made from it, so a project without one can't be opened; its database
        records are all that is left, and removing the project is the way out. A folder that moved within this
        workspace is found again by its name first (find_moved_projects): that project isn't missing any more."""
        root = str(proj["root"] or "")
        if root and Path(root).is_dir():
            return None
        if root and self.find_moved_projects():
            row = self.store.one("SELECT root FROM projects WHERE id=?", proj["id"])
            if row is not None and row["root"] and Path(row["root"]).is_dir():
                return None
        where = f": {hostenv.shown_path(root)}" if root else ""
        return (f"Its folder is missing{where}. Put it back where it was (with the game in it), or remove the project "
                "from decompdecomp: its matches and notes go with its database records.")

    def is_rom(self, proj) -> bool:
        return self.cfg(proj).get("kind") == "rom"

    def console(self, proj) -> consoles.Console:
        """The project's console (consoles/): what the engine asks, instead of comparing platform names."""
        return consoles.of(self.cfg(proj))

    def project_row(self, slug: str, *, sync: bool = True):
        """A project's row. `sync=False`: as stored, without first syncing saved progress, which queues behind any
        saved-file verification running (for a look that doesn't need it: the claim order, twins.order)."""
        return self._project(slug) if sync else Network._project(self, slug)

    def recheck(self, slug: str, log=print, only_if_updated: bool = False) -> str | None:
        """Look at a project again with what this version of decompdecomp knows: its console's work for projects made
        before (Console.on_start: a DS project's functions found and its pieces of functions merged), the compiler
        range checks to weigh again, and code no compiler makes set aside (by its shape, or as agents' notes agree no C
        makes it). The "Check again" button; and before helpers start, once after each update (only_if_updated), so
        what a new version finds reaches every project that gets work, not every project at once. Returns what changed, as a sentence; None when nothing was done
        (checked by this version already, or a room member's copy: its host checks)."""
        from ... import __version__

        row = self.project_row(slug)
        if slug in self.links:
            return None
        from .. import assembly_reconsideration, score_version
        # saved bests scored under older penalties: rescored first, so none blocks a better source (score_version.py)
        rescored = score_version.refresh(self, row, log)
        rescored_said = f"{rescored} saved best score{'' if rescored == 1 else 's'} updated to this version's scoring"
        restored = 0
        if self.is_rom(row) and row['arch'] == 'mips':
            # Boundary decisions saved before they carried the split's data references dropped them (boundary_repairs
            # restore_relocs reads them back from the originals each decision keeps); cheap and idempotent, so it runs
            # on every check, like the score refresh above.
            from ..boundary_repairs import restore_relocs
            restored = restore_relocs(self, row)
        restored_said = (f"{restored} function{'' if restored == 1 else 's'} given back the data references a boundary "
                         "fix had dropped")
        if only_if_updated and self.cfg(row).get("checked") == __version__:
            # Capability fixes can ship without a package-version change. Only this
            # narrow, self-retiring review runs again; console/range work stays gated.
            reconsidered = assembly_reconsideration.run(self, row, log)
            said = [rescored_said] if rescored else []
            if restored:
                said.append(restored_said)
            if reconsidered:
                said.append(f'{reconsidered} obsolete tooling verdicts reconsidered with supported C')
            return "; ".join(said) or None
        if self.is_rom(row):
            self.rangechecks.reopen(slug)
        last = self.project_store(row['id']).one("SELECT COALESCE(MAX(id), 0) AS id FROM events WHERE project_id=?", row["id"])["id"]
        self.console(row).on_start(self, slug, log=log)
        # what the console's work did, it said in the feed ("8 pieces of functions ... joined"): in the answer too
        done = [rescored_said] if rescored else []
        if restored:
            done.append(restored_said)
        if self.is_rom(row):
            # A start or end the bytes prove wrong, in a project imported before the importer fixed them.
            from ..boundary_repairs import apply_entry_fixes
            try:
                fixed = len(apply_entry_fixes(self, slug, log)["fixed"])
            except NetworkError as e:
                fixed = 0
                log(f"function boundaries were not fixed automatically: {e}")
            if fixed:
                done.append(f"{fixed} function boundar{'y' if fixed == 1 else 'ies'} fixed (undo in Review function boundaries)")
        done += [r["detail"] for r in self.project_store(row['id']).all("SELECT detail FROM events WHERE project_id=? AND id>? AND "
                                                     "kind='analysis' ORDER BY id", row["id"], last)]
        reconsidered = assembly_reconsideration.run(self, self.project_row(slug), log)
        if reconsidered:
            done.append(f'{reconsidered} obsolete tooling verdicts reconsidered with supported C')
        n = self.twins.set_aside_asm(self.project_row(slug)) + self.not_c_from_notes(slug)
        # library code this version recognises: named, then set aside, every function in one go (not the 3 seconds
        # a turn background naming takes)
        from ..sdk_assembly import set_aside_c
        from ..sdk_names import name_functions
        library = 0
        if self.is_rom(row):  # (an imported build renames through rebuilds: its background naming does that)
            before = set_aside_c(self, row)
            name_functions(self, slug, SimpleNamespace(stop=threading.Event()))
            library = set_aside_c(self, row) - before
        if library > 0:
            done.append(f"{library} library function{'' if library == 1 else 's'} set aside, found by library detection")
        self._update_cfg(slug, lambda cfg: cfg.update(checked=__version__))
        if n:
            done.append(f"{n} more function{'' if n == 1 else 's'} set aside, as no C makes them")
        said = "; ".join(done) or "nothing new"
        with self.project_store(row['id']).tx() as db:
            self._event(db, row["id"], None, "analysis", f"decompdecomp {__version__}: {said}")
        return said

    def fork_slug(self, project: str | None) -> str | None:
        """The slug when `project` is a fork of a public decomp, else None. (A project's kind never changes, so this
        reads the row without syncing its saved files: twins and the other release's code ask it on every try, and
        each sync of a finished 2,000-function game took seconds.)"""
        # Asked several times for every candidate of a search (scopes, policy profiles, twins) and each ask read the
        # project row's revision and parsed its settings: 19% of a permuter worker's CPU in a sampled run. A kind
        # never changes, so an answer is kept for FORK_SLUG_SECONDS; removing a project forgets them all.
        cache = self.__dict__.setdefault('_fork_slugs', {})
        kept = cache.get(project)
        now = time.monotonic()
        if kept is not None and now - kept[0] < FORK_SLUG_SECONDS:
            return kept[1]
        try:
            proj = Network._project(self, project)
        except NetworkError:
            return None
        answer = proj["slug"] if self.cfg(proj).get("kind") == "fork" else None
        if len(cache) > 256:
            cache.clear()
        cache[project] = (now, answer)
        return answer

    def _rom_only(self, project: str | None, what: str) -> None:
        if self.fork_slug(project):
            raise NetworkError(f"{what} is for ROM projects. In a fork, the project's own headers and source files are "
                               "the shared context: use read_file, search_code and edit_file")

    def symbols(self, project_id: int) -> SymbolTable:
        if project_id in self._sym_override:
            return self._sym_override[project_id]
        if project_id not in self._syms:
            rows = self.project_store(project_id).all(
                "SELECT s.name, s.address, s.size, s.kind, f.id IS NOT NULL AS fn FROM symbols s "
                "LEFT JOIN functions f ON f.project_id=s.project_id AND f.name=s.name "
                "WHERE s.project_id=?", project_id)
            table = SymbolTable([Symbol(r["name"], r["address"], r["size"], r["kind"]) for r in rows])
            for row in rows:
                if row["fn"] and row["name"] not in table.by_name:
                    table.alias(Symbol(row["name"], row["address"], row["size"], row["kind"]))
            self._syms[project_id] = table
        return self._syms[project_id]

    def _rom(self, proj) -> tuple[bytes, list[split.Segment]]:
        if proj["id"] not in self._roms:
            cfg = self.cfg(proj)
            rom = (Path(proj["root"]) / cfg["rom"]["file"]).read_bytes()
            segs = [split.Segment(**s) for s in cfg["segments"]]
            self._roms[proj["id"]] = (rom, segs)
        return self._roms[proj["id"]]

    def reader(self, proj, function=None):
        """Read helpers and constants from the caller's overlay (its verified memory regions first), then the other
        segments, then data after the code in its loaded image. #230: with a caller, an address that several other
        overlays cover at different ROM offsets fails instead of guessing one, since a wrong read skips nothing."""
        rom, segs = self._rom(proj)
        offset = None
        if function is not None:
            if "rom_offset" in function.keys():
                offset = function["rom_offset"]
            else:
                row = self.project_store(proj["id"]).one("SELECT rom_offset FROM functions WHERE project_id=? AND name=?",
                                     proj["id"], function["name"])
                offset = row["rom_offset"] if row else None
        owner = next((s for s in segs if offset is not None and s.rom_start <= offset < s.rom_end), None)
        rooted = "root" in proj.keys()
        extra = None
        if owner is not None and rooted:
            from ...rom.memory import overlay_reader
            extra = overlay_reader(Path(proj["root"]), self.cfg(proj)["rom"]["sha1"], owner.name)

        images = _load_images(self, proj) if rooted else []

        def read(addr: int, n: int) -> bytes | None:
            if extra is not None and (data := extra(addr, n)) is not None:
                return data
            if owner is not None and owner.contains(addr, n):
                candidates = [owner]
            else:
                candidates = [s for s in segs if s.contains(addr, n)]
            offsets = {s.rom_start + addr - s.vram for s in candidates}
            # None means unbacked memory to the linker and skips verification. Ambiguity must fail instead.
            if function is not None and len(offsets) > 1:
                raise NetworkError(f"Cannot verify bytes at 0x{addr:X}: multiple ROM overlays cover this address "
                                   "and the caller's ROM offset does not select one.")
            if candidates:
                at = candidates[0].rom_start + addr - candidates[0].vram
                return rom[at:at + n]
            for start, vram, end in images:  # data after the code, in the same loaded image
                if vram <= addr and addr + n <= end and start + addr - vram + n <= len(rom):
                    at = start + addr - vram
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
            row = self.project_store(proj["id"]).one("SELECT * FROM functions WHERE project_id=? AND vram=?", proj["id"], int(m.group(1), 16))
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
        from ..metadata_rows import rows as metadata_rows
        rows = metadata_rows(self.project_store(proj["id"]), ('name',), "SELECT name FROM functions WHERE project_id=? AND (name LIKE ? OR name LIKE ?) LIMIT 4000",
                              proj["id"], f"%{sym[-6:]}%", f"{sym[:4]}%")
        names = [r["name"] for r in rows]
        return difflib.get_close_matches(sym, names, n=n, cutoff=0.6) or [x for x in names if sym in x][:n]

    def _loose_functions(self, name: str, project: str | None) -> list:
        """Functions an agent may mean by a name that isn't stored as such: the plain symbol of a function whose
        name repeats across a fork's files (stored as symbol@unit), symbol@unit for one that doesn't repeat,
        Class::method, a name with its parameter list or in quotes."""
        name, project = self._function_reference(name, project)
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
        rows = self.project_store(proj["id"]).all(
            "SELECT * FROM functions WHERE project_id=? AND (name=? OR substr(name,1,?)=? "
            "OR json_extract(meta,'$.symbol')=? OR json_extract(meta,'$.real')=? "  # (a PC game's: rename_symbol)
            "OR substr(json_extract(meta,'$.demangled'),1,?)=? "
            "OR json_extract(meta,'$.demangled') LIKE ? ESCAPE '\\') ORDER BY name",
            proj["id"], sym, len(sym) + 1, sym + "@", sym, sym, len(sym) + 1, sym + "(", f"% {like}(%")
        if not rows and "::" not in sym and len(sym) >= 4:  # a method by its own name: Class::sym
            rows = self.project_store(proj["id"]).all("SELECT * FROM functions WHERE project_id=? AND json_extract(meta,'$.demangled') "
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
            held = []
            if agent:
                # Loose-name candidates already identify the relevant projects.
                for pid in {row['project_id'] for row in rows}:
                    held.extend(self.project_store(pid).all(
                        'SELECT f.id,f.unit FROM claims c JOIN functions f ON f.id=c.function_id '
                        'WHERE c.agent_id=? AND f.project_id=?', agent.id, pid))
            if held and len(rows) > 1:  # one it holds, else the one in the file (or module) it works in
                ids, units = {h["id"] for h in held}, {h["unit"] for h in held}
                modules = {u.split("/", 1)[0] for u in units}
                for pick in ([r for r in rows if r["id"] in ids], [r for r in rows if r["unit"] in units],
                             [r for r in rows if r["unit"].split("/", 1)[0] in modules]):
                    if len(pick) == 1:
                        return pick[0]["name"]
            raise

    def _update_cfg(self, slug: str, change, *, synchronize=True) -> dict:
        """Change a project's config in one transaction (change(cfg) edits it in place)."""
        if synchronize:
            proj = self._project(slug)
        else:
            with self.progress.view():
                proj = self._project(slug)
        with self.project_store(proj["id"]).tx() as db:
            cfg = json.loads(db.execute("SELECT config FROM projects WHERE id=?", (proj["id"],)).fetchone()["config"])
            change(cfg)
            db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        if synchronize:self.progress.sync(slug, force=True)
        return cfg

    def _local_identity_name(self, name: str) -> str:
        row = self.store.one("SELECT id FROM agents WHERE name=?", name)
        # Saves can credit room members (@name) or older helpers with long names. Those labels aren't valid
        # new runner names; keep existing identities, and give new ones a stable name without merging credits.
        if row is None and (not name or len(name) > 40 or not all(c.isalnum() or c in "-_." for c in name)):
            suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
            label = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._") or "restored"
            name = f"{label[:27]}-{suffix}"
        return name

    def attribution_agent(self, name: str) -> Agent:
        """Credit saved work without issuing, rotating or exposing a runner's credentials."""
        name = self._local_identity_name(name)
        with self.store.tx() as db:
            row = db.execute("SELECT id,name,owner,trusted FROM agents WHERE name=?", (name,)).fetchone()
            if row is None:
                now = time.time()
                # This identity has no usable bearer token. Starting a runner explicitly issues one later.
                db.execute("INSERT INTO agents(name,owner,token_hash,created_at,trusted,origin,terms_accepted_at) "
                           "VALUES(?,?,?,?,?,?,?)", (name, "you", "attribution:" + secrets.token_hex(24), now, 1, "local", now))
                row = db.execute("SELECT id,name,owner,trusted FROM agents WHERE name=?", (name,)).fetchone()
        return Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))

    def local_agent(self, name: str) -> tuple[Agent, str]:
        """Issue credentials for a runner; attribution-only callers must use attribution_agent."""
        name = self._local_identity_name(name)
        row = self.store.one("SELECT id FROM agents WHERE name=?", name)
        if row is not None:
            token = "dn_" + secrets.token_urlsafe(24)
            with self.store.tx() as db:
                db.execute("UPDATE agents SET token_hash=?, origin='local' "
                           "WHERE id=?", (hash_token(token), row["id"]))
        else:
            token = self.register_agent(name, "you", trusted=True, origin="local")
        return self.authenticate(token), token

    def remove_project(self, slug):
        # Archive before deleting any records or files. An archive failure keeps the project intact.
        self.match_library.archive_project(self.project_row(slug))
        self.__dict__.pop('_fork_slugs', None)
        return super().remove_project(slug)


def _load_images(net, proj) -> list[tuple[int, int, int]]:
    """The ROM project's loaded images (split.load_images), from its splat config."""
    cache = net.__dict__.setdefault("_images", {})
    rom_entry = id(net._rom(proj))  # rebuilt with the ROM's segments (a reimport drops both)
    key = proj["root"]  # (a project's folder: every row the reader is given has one)
    if cache.get(key, (None,))[0] != rom_entry:
        import yaml
        images = []
        for config in sorted(Path(proj["root"]).glob("*.yaml")):
            try:
                data = yaml.safe_load(config.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and isinstance(data.get("segments"), list):
                # The raw data after the code is read from the ROM the split describes, only when that is the
                # project's: a packed game's split is of its unpacked copy, at other offsets.
                own = (data.get("options") or {}).get("target_path") in (None, net.cfg(proj)["rom"].get("file"))
                images = split.load_images(data, net._rom(proj)[0] if own else None,
                                           big_endian=bool(net.cfg(proj).get("big_endian", True)))
                break
        cache[key] = (rom_entry, images)
    return cache[key][1]
