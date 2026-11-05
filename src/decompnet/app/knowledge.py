"""What agents learn about a game, shared with every agent once two different models agree on it.

Agents used to leave project-wide notes that every new session read as "what agents learned": anything one agent
wrote reached all the others, its guesses too ("it's this compiler", then "no, it's this one"). Now an agent proposes
(share_knowledge), and a proposal counts only when helpers running two different models back it on their own: two
Claude Opus sessions agreeing are one opinion; Claude Opus and GPT agreeing are two. That's also why running
helpers of several models pays: they check each other. An agreed proposal reaches every agent's prompt once the
user accepts it, or at once when the project adds agreed knowledge by itself (its setting).
The user can also explicitly accept a single-model proposal without waiting for consensus.

Reusable matching techniques for a known compiler belong here. Compiler selection questions do not:
which compiler built what is settled by the compiler map and range checks,
with evidence, not by agents agreeing.
"""

from __future__ import annotations

import difflib
import json
import re
import time

from ..network import Agent, NetworkError

MODELS_TO_AGREE = 2
SAME_IDEA_RATIO = 0.6  # word overlap above which a new proposal is probably one that's open already
MAX_TEXT = 600
SHOWN_IN_PROMPT = 2500  # characters of accepted knowledge in a session's prompt
OPEN_SHOWN = 8  # open proposals an agent is shown, newest first
COMPILER_MENTION = re.compile(r"\b(compil(er|ed)|toolchain|flags?|gcc|ido|mwcc|codewarrior|mwcps2|mwccpsp|snc|msvc)\b|(^|\s)-[OG]\d",
                           re.I)

# A flag can describe the conditions of a source technique. Reject selection language,
# not the mere presence of -O2/-g3; proposals still need the normal model/user review.
COMPILER_SELECTION = re.compile(r"built\s+(?:with|by|using)|compiler\s+(?:is|was)|\b(?:use|switch|change|select|try)\s+(?:(?:the|to)\s+)?(?:compiler|flags?|gcc|ido|mwcc|-[OG]\d)", re.I)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS knowledge(
      id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
      created_at REAL NOT NULL, decided_at REAL)""",
    """CREATE TABLE IF NOT EXISTS knowledge_support(
      knowledge_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, model TEXT NOT NULL, created_at REAL NOT NULL,
      UNIQUE(knowledge_id, agent_id))""",
    """CREATE TABLE IF NOT EXISTS knowledge_evidence(
      id INTEGER PRIMARY KEY, knowledge_id INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
      agent_id INTEGER NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL,
      UNIQUE(knowledge_id,agent_id,text))""",
    # a project's accepted knowledge is in its progress folder's manifest (progress.py)
    """CREATE TRIGGER IF NOT EXISTS progress_dirty_knowledge_insert AFTER INSERT ON knowledge
    WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
    BEGIN
      INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
      UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS progress_dirty_knowledge_update
    AFTER UPDATE OF project_id,text,status ON knowledge
    WHEN EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
    BEGIN
      INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(NEW.project_id);
      UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=NEW.project_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS progress_dirty_knowledge_delete AFTER DELETE ON knowledge
    WHEN EXISTS (SELECT 1 FROM projects WHERE id=OLD.project_id AND CASE WHEN json_valid(config) THEN json_extract(config,'$.kind') END='rom')
    BEGIN
      INSERT OR IGNORE INTO progress_dirty(project_id) VALUES(OLD.project_id);
      UPDATE progress_dirty SET token=lower(hex(randomblob(16))), metadata=1 WHERE project_id=OLD.project_id;
    END""",
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

    def share(self, agent: Agent, slug: str, text: str = "", supports: int = 0, evidence: str = "") -> str:
        """An agent proposes something every agent on this game should know, or backs a proposal (supports=id)."""
        proj = self.net._project(slug)
        evidence = " ".join(str(evidence).split())[:2000]
        if supports:
            result = self._support(agent, proj, int(supports))
            self._evidence(int(supports),agent,evidence)
            return result
        text = " ".join(text.split())[:4000]
        original = text
        # Move clearly labelled concrete examples out of the reusable rule, never silently generalize a claim.
        examples = []
        def example(match):
            fragment = match[0]
            if self._function_mentions(proj,fragment) and re.search(r'example|matched|improv|score',fragment,re.I):
                examples.append(fragment)
                return ''
            return fragment
        text = re.sub(r'\([^()]{0,700}\)',example,text)
        parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])',text)
        rule = []
        for part in parts:
            if (self._function_mentions(proj,part) and re.match(r'(?:For example|Example:|In [A-Za-z_]\w*)\b',part,re.I)
                and re.search(r'match|improv|score|example',part,re.I)):
                examples.append(part)
            else:
                rule.append(part)
        text = ' '.join(rule).strip()
        if examples and (len(text)<40 or not re.search(r'\b(?:if|when|compiler|cast|avoid|prefer|always|never|pattern|technique)\b',text,re.I)):
            text=original
        evidence = ' '.join([evidence]+examples).strip()[:2000]
        text = text[:MAX_TEXT]
        if not text:
            raise NetworkError("say what every agent on this game should know (text), or back a proposal (supports=id)")
        if COMPILER_MENTION.search(text) and COMPILER_SELECTION.search(text):
            raise NetworkError("which compiler built what isn't shared knowledge: the compiler map settles it with "
                               "evidence. Inspect compiler_map(); test a function with try_compiler or search_flags; "
                               "propose a setup change with suggest(text=..., function=...). This was not saved as shared knowledge.")
        scoped = self._rule_mentions(proj, text)
        if scoped:
            raise NetworkError('This describes a specific function ('+', '.join(scoped[:3])+'). '
                               'Use add_note or release_function(notes=...) for function notes, or report_candidate_issue '
                               'for a source-quality concern. Shared knowledge is for reusable game/compiler matching techniques.')
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
        self._evidence(kid,agent,evidence)
        return (f"Proposed as #{kid}. It reaches every agent once a helper running another model backs it "
                f"(and the user accepts it, unless the project adds agreed knowledge by itself).")

    def _evidence(self, kid, agent, evidence):
        if evidence:
            with self.net.store.tx() as db:
                db.execute('INSERT OR IGNORE INTO knowledge_evidence(knowledge_id,agent_id,text,created_at) VALUES(?,?,?,?)',
                           (kid,agent.id,evidence,time.time()))

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
        """The user may accept any proposal, or reject/take back one, independently of model consensus."""
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
                           "models": sorted(support.get(r["id"], ())),
                           "verification": self._verification(proj['id'],r['id']),
                           "evidence": [dict(e) for e in self.net.store.all('SELECT e.text,a.name AS agent FROM knowledge_evidence e LEFT JOIN agents a ON a.id=e.agent_id WHERE e.knowledge_id=? ORDER BY e.id',r['id'])]} for r in rows]}

    def _verification(self, pid, kid):
        row = self.net.store.one('SELECT value FROM settings WHERE key=?',f'knowledge-review:{pid}:{kid}')
        return json.loads(row['value']) if row else None

    def request_verification(self, slug, kid, sessions):
        from .agent_review import view
        with view(self.net):
            proj = self.net._project(slug)
            item = next((i for i in self.listing(slug)['items'] if i['id']==kid), None)
            if not item or item['status'] != PROPOSED:
                raise NetworkError('Choose a proposal still waiting for a second model.')
            def model_for(session):
                return self._model_of(Agent(0, session.agent_name, None))
            eligible = [s for s in sessions if s.slug==slug and s.state in {'working','starting','waiting'}
                        and not s.stop.is_set() and not s.options.get('focus_id') and model_for(s) not in item['models']]
            if not eligible:
                raise NetworkError('No active helper with a different model is available. Start one, then request verification again. Focused agents stay on their function.')
            session = min(eligible, key=lambda s: s.id)
            model = model_for(session)
            key = f'knowledge-review:{proj["id"]}:{kid}'
            with self.net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps({'model':model,'agent':session.agent_name})))
            return {'message':f'Queued for {session.label} ({model}) at its next session. It must independently verify examples before backing the proposal.', 'model':model}

    def for_prompt(self, slug: str, agent=None) -> str:
        """What every agent starts with: the accepted knowledge, then the open proposals it can back."""
        proj = self.net._project(slug)
        out, used = [], 0
        for r in self.net.store.all("SELECT text FROM knowledge WHERE project_id=? AND status=? ORDER BY id DESC",
                                    proj["id"], ACCEPTED):
            if self._rule_mentions(proj, r["text"]):
                continue
            if used + len(r["text"]) > SHOWN_IN_PROMPT:
                break
            out.append(f"- {r['text']}")
            used += len(r["text"])
        text = ("Accepted matching knowledge for this game (user-approved or model consensus):\n" + "\n".join(out)) if out else ""
        model = self._model_of(agent) if agent else None
        supported = {r['knowledge_id'] for r in self.net.store.all('SELECT knowledge_id FROM knowledge_support WHERE model=?',model)} if model else set()
        queued = {}
        for r in self.net.store.all('SELECT key,value FROM settings WHERE key LIKE ?',f'knowledge-review:{proj["id"]}:%'):
            item = json.loads(r['value'])
            if item['model'] == model and agent and item['agent'] == agent.name:
                queued[int(r['key'].rsplit(':',1)[1])] = item
        open_ = [r for r in self._open(proj) if r['id'] not in supported and not self._rule_mentions(proj, r["text"])]
        open_.sort(key=lambda r: r['id'] not in queued)
        open_ = open_[:OPEN_SHOWN]
        if open_:
            text += ("\n\nProposals waiting for a second model (back one with share_knowledge(supports=N) only when "
                     "your own work shows it):\n" + "\n".join(f"- #{r['id']}: {r['text']}" for r in open_))
        assigned = [r for r in open_ if r['id'] in queued]
        if assigned:
            text += '\nRequested independent verification (do not assume earlier claims are true):'
            for row in assigned:
                evidence = self.net.store.all('SELECT text FROM knowledge_evidence WHERE knowledge_id=? ORDER BY id DESC LIMIT 3',row['id'])
                text += f"\n- #{row['id']}: inspect cited functions and reproduce the relevant compile/diff evidence. Back with share_knowledge(supports=N, evidence=...) only if your own results support the rule; otherwise report the counterexample."
                text += ''.join('\n  Prior example to verify: '+e['text'][:1000] for e in evidence)
        return text.strip()


    def _rule_mentions(self, proj, text):
        # Generic compiler tips may naturally name calls inside code examples (e.g. sqrtf).
        # Keep concrete progress claims out even if an agent puts the function name in backticks.
        if (re.search(r'\b(?:when|if|compiler|cast|avoid|prefer|pattern|technique)\b',text,re.I)
            and not re.search(r'\b(?:score|improved|remaining|stuck)\b|\b(?:I|we)\s+tried',text,re.I)):
            text=re.sub(r'`[^`]*`','',text)
        return self._function_mentions(proj,text)

    def _function_mentions(self, proj, text):
        words = set(re.findall(r'[A-Za-z_]\w*', text))
        return [r['name'] for r in self.net.store.all('SELECT name FROM functions WHERE project_id=?', proj['id'])
                if r['name'] in words]

    def for_function(self, fn):
        """Retain legacy function-specific knowledge at its relevant function, without editing user data."""
        out = []
        for row in self.net.store.all('SELECT text,status FROM knowledge WHERE project_id=? AND status IN (?,?) ORDER BY id DESC',
                                      fn['project_id'], ACCEPTED, PROPOSED):
            if fn['name'] in set(re.findall(r'[A-Za-z_]\w*', row['text'])):
                out.append(f"{row['status']}: {row['text'][:600]}")
            if len(out) == 3:
                break
        return ['### Function-specific knowledge (historical reports)', *out] if out else []
