"""What agents learn about a game, shared with every agent once two different models agree on it.

Agents used to leave project-wide notes that every new session read as "what agents learned": anything one agent
wrote reached all the others, its guesses too ("it's this compiler", then "no, it's this one"). Now an agent proposes
(share_knowledge), and a proposal counts only when helpers running two different models back it on their own: two
Claude Opus sessions agreeing are one opinion; Claude Opus and GPT agreeing are two. That's also why running
helpers of several models pays: they check each other. An agreed proposal reaches every agent's prompt once the
user accepts it, or at once when the project adds agreed knowledge by itself (its setting).

Compiler questions don't belong here: which compiler built what is settled by the compiler map and range checks,
with evidence, not by agents agreeing.
"""

from __future__ import annotations

import difflib
import re
import time

from ..network import Agent, NetworkError

MODELS_TO_AGREE = 2
SAME_IDEA_RATIO = 0.6  # word overlap above which a new proposal is probably one that's open already
MAX_TEXT = 600
SHOWN_IN_PROMPT = 2500  # characters of accepted knowledge in a session's prompt
OPEN_SHOWN = 8  # open proposals an agent is shown, newest first
COMPILER_TALK = re.compile(r"\b(compil(er|ed)|toolchain|flags?|gcc|ido|mwcc|codewarrior|mwcps2|msvc)\b|(^|\s)-[OG]\d",
                           re.I)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS knowledge(
      id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
      created_at REAL NOT NULL, decided_at REAL)""",
    """CREATE TABLE IF NOT EXISTS knowledge_support(
      knowledge_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, model TEXT NOT NULL, created_at REAL NOT NULL,
      UNIQUE(knowledge_id, agent_id))""",
)
PROPOSED, AGREED, ACCEPTED, REJECTED = "proposed", "agreed", "accepted", "rejected"


def model_key(model: str) -> str:
    """One model, however a provider spells it: anthropic/claude-opus-5.5 and claude-opus-5.5 are the same."""
    return model.lower().rsplit("/", 1)[-1].strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


class Knowledge:
    def __init__(self, net):
        self.net = net
        with net.store.tx() as db:
            for sql in SCHEMA:
                db.execute(sql)

    def _model_of(self, agent: Agent) -> str:
        """The model behind a helper (its record), or its own name when the app doesn't know one."""
        row = self.net.store.one("SELECT model FROM helpers WHERE name=? AND model != '' ORDER BY id DESC LIMIT 1",
                                 agent.name) if self._has_helpers() else None
        # without a record: the helper's name less its number (claude-opus-5.5-1 and -2 are one model)
        return model_key(row["model"] if row else re.sub(r"-\d+$", "", agent.name))

    def _has_helpers(self) -> bool:
        return self.net.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='helpers'") is not None

    def auto(self, proj) -> bool:
        return bool(self.net.cfg(proj).get("knowledge_auto"))

    def set_auto(self, slug: str, on: bool) -> None:
        self.net._update_cfg(slug, lambda cfg: cfg.__setitem__("knowledge_auto", bool(on)))
        if on:
            proj = self.net._project(slug)
            with self.net.store.tx() as db:
                db.execute("UPDATE knowledge SET status=?, decided_at=? WHERE project_id=? AND status=?",
                           (ACCEPTED, time.time(), proj["id"], AGREED))

    def share(self, agent: Agent, slug: str, text: str = "", supports: int = 0) -> str:
        """An agent proposes something every agent on this game should know, or backs a proposal (supports=id)."""
        proj = self.net._project(slug)
        if supports:
            return self._support(agent, proj, int(supports))
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise NetworkError("say what every agent on this game should know (text), or back a proposal (supports=id)")
        if COMPILER_TALK.search(text):
            raise NetworkError("which compiler built what isn't shared knowledge: the compiler map settles it with "
                               "evidence. Use try_compiler, compiler_map or suggest(...) instead")
        mine = _words(text)
        for row in self._open(proj):
            if difflib.SequenceMatcher(None, mine, _words(row["text"])).ratio() >= SAME_IDEA_RATIO:
                return (f"A proposal like it is open already, #{row['id']}: \"{row['text']}\". If it says the same, "
                        f"back it: share_knowledge(supports={row['id']}). If yours says something else, say what "
                        f"differs.")
        with self.net.store.tx() as db:
            kid = db.execute("INSERT INTO knowledge(project_id, text, status, created_at) VALUES(?,?,?,?)",
                             (proj["id"], text, PROPOSED, time.time())).lastrowid
        self._support(agent, proj, kid)
        return (f"Proposed as #{kid}. It reaches every agent once a helper running another model backs it "
                f"(and the user accepts it, unless the project adds agreed knowledge by itself).")

    def _support(self, agent: Agent, proj, kid: int) -> str:
        row = self.net.store.one("SELECT * FROM knowledge WHERE id=? AND project_id=?", kid, proj["id"])
        if row is None:
            raise NetworkError(f"no proposal #{kid} on this project")
        model = self._model_of(agent)
        with self.net.store.tx() as db:
            db.execute("INSERT OR IGNORE INTO knowledge_support(knowledge_id, agent_id, model, created_at) VALUES(?,?,?,?)",
                       (kid, agent.id, model, time.time()))
            models = {r["model"] for r in db.execute("SELECT model FROM knowledge_support WHERE knowledge_id=?", (kid,))}
            if row["status"] == PROPOSED and len(models) >= MODELS_TO_AGREE:
                status = ACCEPTED if self.auto(proj) else AGREED
                db.execute("UPDATE knowledge SET status=?, decided_at=? WHERE id=?", (status, time.time(), kid))
                self.net._event(db, proj["id"], agent.id, "knowledge", f"#{kid} agreed by {len(models)} models")
                return (f"#{kid} is agreed by {len(models)} different models"
                        + (": every agent gets it from now on." if status == ACCEPTED else ": the user decides now."))
        if row["status"] != PROPOSED:
            return f"#{kid} is {row['status']} already."
        return f"Backed #{kid}. It still needs a helper running a model other than {model}."

    def _open(self, proj) -> list:
        return self.net.store.all("SELECT * FROM knowledge WHERE project_id=? AND status=? ORDER BY id DESC",
                                  proj["id"], PROPOSED)

    def decide(self, slug: str, kid: int, accept: bool) -> None:
        """The user accepts an agreed proposal, or rejects (or takes back) one."""
        proj = self.net._project(slug)
        with self.net.store.tx() as db:
            db.execute("UPDATE knowledge SET status=?, decided_at=? WHERE id=? AND project_id=?",
                       (ACCEPTED if accept else REJECTED, time.time(), int(kid), proj["id"]))

    def forget(self, slug: str) -> None:
        """A deleted project's knowledge goes with it (its id may be a new project's later)."""
        pid = self.net._project(slug)["id"]
        with self.net.store.tx() as db:
            db.execute("DELETE FROM knowledge_support WHERE knowledge_id IN (SELECT id FROM knowledge WHERE project_id=?)",
                       (pid,))
            db.execute("DELETE FROM knowledge WHERE project_id=?", (pid,))

    def listing(self, slug: str) -> dict:
        """For the GUI: every proposal not rejected, with the models behind it, and the project's setting."""
        proj = self.net._project(slug)
        rows = self.net.store.all("SELECT * FROM knowledge WHERE project_id=? AND status != ? ORDER BY id DESC",
                                  proj["id"], REJECTED)
        support = {}
        for r in self.net.store.all("SELECT s.knowledge_id, s.model FROM knowledge_support s JOIN knowledge k "
                                    "ON k.id=s.knowledge_id WHERE k.project_id=?", proj["id"]):
            support.setdefault(r["knowledge_id"], set()).add(r["model"])
        return {"auto": self.auto(proj), "models_to_agree": MODELS_TO_AGREE,
                "items": [{"id": r["id"], "text": r["text"], "status": r["status"], "t": r["created_at"],
                           "models": sorted(support.get(r["id"], ()))} for r in rows]}

    def for_prompt(self, slug: str) -> str:
        """What every agent starts with: the accepted knowledge, then the open proposals it can back."""
        proj = self.net._project(slug)
        out, used = [], 0
        for r in self.net.store.all("SELECT text FROM knowledge WHERE project_id=? AND status=? ORDER BY id DESC",
                                    proj["id"], ACCEPTED):
            if used + len(r["text"]) > SHOWN_IN_PROMPT:
                break
            out.append(f"- {r['text']}")
            used += len(r["text"])
        text = ("What agents learned about this game (agreed by different models):\n" + "\n".join(out)) if out else ""
        open_ = self._open(proj)[:OPEN_SHOWN]
        if open_:
            text += ("\n\nProposals waiting for a second model (back one with share_knowledge(supports=N) only when "
                     "your own work shows it):\n" + "\n".join(f"- #{r['id']}: {r['text']}" for r in open_))
        return text.strip()
