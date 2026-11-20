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
SAME_IDEA_RATIO = 0.6  # word overlap above which a new proposal may duplicate an existing rule
MAX_TEXT = 600
SHOWN_IN_PROMPT = 2500  # characters of accepted knowledge in a session's prompt
OPEN_SHOWN = 8  # open proposals an agent is shown, newest first
COMPILER_MENTION = re.compile(r"\b(compil(er|ed)|toolchain|flags?|gcc|ido|mwcc|codewarrior|mwcps2|mwccpsp|snc|msvc)\b|(^|\s)-[OG]\d",
                           re.I)

# A flag can describe the conditions of a source technique. Reject selection language,
# not the mere presence of -O2/-g3; proposals still need the normal model/user review.
COMPILER_SELECTION = re.compile(r"built\s+(?:with|by|using)|compiler\s+(?:is|was)|\b(?:use|switch|change|select|try)\s+(?:(?:the|to)\s+)?(?:compiler|flags?|gcc|ido|mwcc|-[OG]\d)", re.I)

TOPICS = {
    'Grouped copies': r'(?:struct|aggregate|record|word|vector).{0,35}(?:cop(?:y|ies)|assignment)|grouped (?:loads|stores)',
    'Load width and indexing': r'\b(?:byte (?:offset|strid)|pointer scal|(?:lb|lbu|lh|lhu|lwc1)\b|halfword|typed (?:array|index))',
    'Call signatures': r'\b(?:prototyp|pass(?:es|ing)? through|forward(?:s|ing)? (?:all |incoming )?arg)',
    'Unsigned conversions': r'(?:float|double).{0,30}(?:to[ -](?:unsigned|u32)|conversion)|2\^31|unsigned (?:cast|conversion)',
    'Compiler settings': r'-(?:f(?:no-)?(?:schedule|strength-reduce|fast-math)|g[0-3]?\b|O[0-4]\b)',
    'Stack frames': r'\b(?:stack (?:slot|frame|space)|unused (?:automatic |local |volatile )?(?:array|aggregate|local))',
}
TOPIC_PATTERNS = {name: re.compile(pattern, re.I) for name, pattern in TOPICS.items()}


def topic(text):
    return next((name for name, pattern in TOPIC_PATTERNS.items() if pattern.search(text)), 'Other techniques')


def varied(rows):
    """Offer each recognized topic before filling the remaining space with newer entries."""
    heads, remaining, seen = [], [], set()
    for row in rows:
        group = topic(row['text'])
        (remaining if group in seen else heads).append(row)
        seen.add(group)
    return heads + remaining


def example_details(text):
    """Index references in historical prose without treating them as verified results."""
    revisions = re.findall(r'\b(?:revision|trial(?:_id)?|candidate)(?:\s+|=)`?([0-9a-f]{16,64})\b', text, re.I)
    flags = re.findall(r'(?<![\w-])-(?:[OG][0-9]|g[0-3]?|(?:m|f)[\w=-]+)\b', text)
    return {'revisions': list(dict.fromkeys(revisions)), 'flags': list(dict.fromkeys(flags))}


def trial_summaries(store, pid, keys):
    if not keys or not store.one("SELECT 1 FROM sqlite_master WHERE name='compiler_trial_evidence'"):
        return {}
    summaries = {}
    for row in store.all(
        'SELECT t.id,t.record,f.name FROM compiler_trial_evidence t JOIN functions f ON f.id=t.function_id '
        'WHERE t.project_id=? AND t.id IN (SELECT value FROM json_each(?))', pid, json.dumps(sorted(keys))
    ):
        record = json.loads(row['record'])
        result = record.get('result')
        if isinstance(result, dict):
            summaries[row['id']] = {'id':row['id'], 'function':row['name'], 'compiler':record.get('compiler'),
                                    'flags':record.get('flags'), 'score':result.get('score'),
                                    'exact':result.get('exact') is True}
    return summaries


def saved_trials(rows):
    """Read portable trial summaries as historical measurements."""
    if not isinstance(rows, list):
        return {}
    fields = ('id', 'function', 'compiler', 'flags', 'exact', 'score')
    return {row['id']:{key:row.get(key) for key in fields} for row in rows if isinstance(row, dict)
            and isinstance(row.get('id'), str) and re.fullmatch(r'[0-9a-f]{20}', row['id'])
            and all(isinstance(row.get(field), str) for field in ('function', 'compiler', 'flags'))
            and type(row.get('exact')) is bool
            and (row.get('score') is None or type(row['score']) is int)}


def saved_evidence(net, store, pid):
    """Keep the legacy list of accepted text and save the attributed records alongside it."""
    from .project_labels import label
    result = {}
    for table, field in (('knowledge_support', 'model'), ('knowledge_evidence', 'text')):
        rows = store.all(
            f'SELECT k.text AS rule,r.agent_id,r.{field} AS value FROM {table} r '
            'JOIN knowledge k ON k.id=r.knowledge_id WHERE k.project_id=? AND k.status=? '
            'ORDER BY k.id,r.created_at,r.agent_id,r.' + field, pid, ACCEPTED)
        for row in label(net, rows, {'by':'agent_id'}):
            item = {'by': row['by'] or 'restored', 'value': row['value']}
            records = result.setdefault(row['rule'], {}).setdefault(field, [])
            if item not in records:
                records.append(item)
    refs = {rule: {key for row in fields.get('text', []) for key in example_details(row['value'])['revisions']}
            for rule, fields in result.items()}
    trials = {}
    for row in store.all('SELECT value FROM settings WHERE key LIKE ?', f'knowledge-trials:{pid}:%'):
        trials.update(saved_trials(json.loads(row['value'])))
    trials.update(trial_summaries(store, pid, {key for keys in refs.values() for key in keys}))
    for rule, keys in refs.items():
        if linked := [trials[key] for key in sorted(keys) if key in trials]:
            result[rule]['trials'] = linked
    return result


def saved_rows(manifest):
    records = manifest.get('knowledge_evidence', {})
    if not isinstance(records, dict):
        return
    for rule in manifest.get('knowledge', []):
        item = records.get(rule, {})
        if not isinstance(item, dict):
            continue
        for field in ('model', 'text'):
            rows = item.get(field)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ('by', 'value')):
                        yield rule, field, row


def restore_evidence(db, pid, manifest, agents, check_cancel):
    ids = {row['text']: row['id'] for row in db.execute(
        'SELECT id,text FROM knowledge WHERE project_id=? AND status=?', (pid, ACCEPTED))}
    now = time.time()
    for rule, field, row in saved_rows(manifest):
        check_cancel()
        if rule in ids:
            table = 'knowledge_support' if field == 'model' else 'knowledge_evidence'
            db.execute(f'INSERT OR IGNORE INTO {table}(knowledge_id,agent_id,{field},created_at) VALUES(?,?,?,?)',
                       (ids[rule], agents[row['by'] or 'restored'], row['value'], now))
    records = manifest.get('knowledge_evidence')
    if not isinstance(records, dict):
        return
    for rule, item in records.items():
        check_cancel()
        if rule not in ids or not isinstance(item, dict):
            continue
        if trials := saved_trials(item.get('trials')):
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                       (f'knowledge-trials:{pid}:{ids[rule]}', json.dumps(list(trials.values()))))

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
PROFILE_FINDING = "EXISTS (SELECT 1 FROM settings WHERE key='knowledge-profile:' || k.project_id || ':' || k.id)"


def model_key(model: str) -> str:
    """One model, however a provider spells it: anthropic/claude-opus-5.5 and claude-opus-5.5 are the same."""
    return model.lower().rsplit("/", 1)[-1].strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def service(net):
    """Base Network callers need the same scoped knowledge as the GUI engine.

    Initialize once on demand, including when two first briefs arrive together.
    Never substitute the catalog for a function's operational project store.
    """
    existing = getattr(net, 'knowledge', None)
    if existing is not None:
        return existing
    with net.store.tx():
        existing = getattr(net, 'knowledge', None)
        if existing is None:
            existing = net.knowledge = Knowledge(net)
        return existing


class Knowledge:
    def __init__(self, net):
        self.net = net
        self.ready = set()
        self._prepare(net.store)

    def _prepare(self, store):
        if store in self.ready:return store
        from .. import store as storage
        if storage.ATTACH_ONLY:  # a reader process: the app makes these tables
            self.ready.add(store)
            return store
        with store.tx() as db:
            if store in self.ready:return store
            for sql in SCHEMA:db.execute(sql)
            store.after_commit(lambda: self.ready.add(store))
        return store

    def _store(self, project):
        return self._prepare(self.net.project_store(project))

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
            with self._store(slug).tx() as db:
                db.execute("UPDATE knowledge SET status=?, decided_at=? WHERE project_id=? AND status=?",
                           (ACCEPTED, time.time(), proj["id"], AGREED))

    def share(self, agent: Agent, slug: str, text: str = "", supports: int = 0, evidence: str = "") -> str:
        """An agent proposes something every agent on this game should know, or backs a proposal (supports=id)."""
        proj = self.net._project(slug)
        evidence = " ".join(str(evidence).split())[:2000]
        if supports:
            model = self._model_of(agent)
            with self._store(slug).tx():
                result = self._support(agent, proj, int(supports), model=model)
                self._evidence(proj, int(supports), agent, evidence)
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
        candidates = self._store(proj['id']).all(
            'SELECT id,text,status FROM knowledge WHERE project_id=? AND status!=? ORDER BY id DESC',
            proj['id'], REJECTED)
        for row in candidates:
            if difflib.SequenceMatcher(None, mine, _words(row["text"])).ratio() >= SAME_IDEA_RATIO:
                return (f"A similar {row['status']} rule exists, #{row['id']}: \"{row['text']}\". If it says the same, "
                        f"back it: share_knowledge(supports={row['id']}). If yours says something else, say what "
                        f"differs.")
        model = self._model_of(agent)
        with self._store(slug).tx() as db:
            kid = db.execute("INSERT INTO knowledge(project_id, text, status, created_at) VALUES(?,?,?,?)",
                             (proj["id"], text, PROPOSED, time.time())).lastrowid
            self._support(agent, proj, kid, model=model)
            self._evidence(proj, kid, agent, evidence)
        return (f"Proposed as #{kid}. It reaches every agent once a helper running another model backs it "
                f"(and the user accepts it, unless the project adds agreed knowledge by itself), or once the user "
                f"gives it to agents.")

    def _evidence(self, proj, kid, agent, evidence):
        if evidence:
            with self._store(proj['id']).tx() as db:
                added = db.execute(
                    'INSERT OR IGNORE INTO knowledge_evidence(knowledge_id,agent_id,text,created_at) VALUES(?,?,?,?)',
                    (kid,agent.id,evidence,time.time()))
                if added.rowcount:
                    db.execute('UPDATE knowledge SET text=text WHERE id=? AND status=?', (kid, ACCEPTED))

    def _support(self, agent: Agent, proj, kid: int, *, model: str | None = None) -> str:
        model = self._model_of(agent) if model is None else model
        with self._store(proj['id']).tx() as db:
            row = db.execute("SELECT * FROM knowledge WHERE id=? AND project_id=? AND status!=?",
                             (kid, proj["id"], REJECTED)).fetchone()
            if row is None:
                raise NetworkError(f"Finding #{kid} is no longer active. Refresh the project's findings.")
            added = db.execute("INSERT OR IGNORE INTO knowledge_support(knowledge_id, agent_id, model, created_at) VALUES(?,?,?,?)",
                               (kid, agent.id, model, time.time()))
            if added.rowcount and row['status'] == ACCEPTED:
                db.execute('UPDATE knowledge SET text=text WHERE id=? AND status=?', (kid, ACCEPTED))
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
        return self._store(proj['id']).all("SELECT * FROM knowledge WHERE project_id=? AND status=? ORDER BY id DESC",
                                  proj["id"], PROPOSED)

    def decide(self, slug: str, kid: int, accept: bool) -> None:
        """The user may accept any proposal, or reject/take back one, independently of model consensus."""
        proj = self.net._project(slug)
        with self._store(slug).tx() as db:
            db.execute("UPDATE knowledge SET status=?, decided_at=? WHERE id=? AND project_id=? AND status!=?",
                       (ACCEPTED if accept else REJECTED, time.time(), int(kid), proj["id"], REJECTED))

    def merge(self, slug: str, target: int, sources: list[int], text: str, *, profile: bool = False) -> None:
        """Consolidate equivalent findings while retaining the original records for audit."""
        if (type(target) is not int or not isinstance(sources, list) or not sources
                or any(type(kid) is not int for kid in sources)
                or target in sources or len(set(sources)) != len(sources)):
            raise NetworkError("Choose one finding to keep and at least one different finding to merge.")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_TEXT:
            raise NetworkError(f"Enter merged wording of up to {MAX_TEXT} characters.")
        proj = self.net._project(slug)
        pid = proj['id']
        condition = PROFILE_FINDING if profile else "k.status!='rejected'"
        with self._store(slug).tx() as db:
            ids = json.dumps([target, *sources])
            rows = db.execute(
                f"SELECT k.id,k.status FROM knowledge k WHERE k.project_id=? AND {condition} "
                "AND k.id IN (SELECT value FROM json_each(?))", (pid, ids),
            ).fetchall()
            if len(rows) != len(sources) + 1:
                raise NetworkError("A finding is no longer active on this project. Refresh and select it again.")
            archived = json.dumps(sources)
            db.execute(
                "INSERT OR IGNORE INTO knowledge_support SELECT ?,agent_id,model,created_at "
                "FROM knowledge_support WHERE knowledge_id IN (SELECT value FROM json_each(?)) "
                "ORDER BY created_at, knowledge_id", (target, archived),
            )
            if profile and db.execute(
                "SELECT agent_id,model FROM knowledge_support WHERE knowledge_id IN (SELECT value FROM json_each(?)) "
                "EXCEPT SELECT agent_id,model FROM knowledge_support WHERE knowledge_id=? LIMIT 1",
                (archived, target),
            ).fetchone():
                raise NetworkError("These findings record different models for the same helper. "
                                   "Keep the findings separate to retain both votes.")
            db.execute(
                "INSERT OR IGNORE INTO knowledge_evidence(knowledge_id,agent_id,text,created_at) "
                "SELECT ?,agent_id,text,created_at FROM knowledge_evidence "
                "WHERE knowledge_id IN (SELECT value FROM json_each(?)) ORDER BY id", (target, archived),
            )
            models = db.execute("SELECT count(DISTINCT model) FROM knowledge_support WHERE knowledge_id=?",
                                (target,)).fetchone()[0]
            status = REJECTED if profile else PROPOSED
            if not profile and any(row["status"] == ACCEPTED for row in rows):
                status = ACCEPTED
            elif not profile and models >= MODELS_TO_AGREE:
                status = ACCEPTED if self.auto(proj) else AGREED
            if profile:
                db.executemany("DELETE FROM settings WHERE key=?",
                               [(f"knowledge-profile:{pid}:{kid}",) for kid in sources])
            trials = {}
            for kid in [target, *sources]:
                row = db.execute("SELECT value FROM settings WHERE key=?", (f"knowledge-trials:{pid}:{kid}",)).fetchone()
                if row:
                    for key, trial in saved_trials(json.loads(row['value'])).items():
                        trials.setdefault(key, trial)
            if trials:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",
                           (f"knowledge-trials:{pid}:{target}", json.dumps(list(trials.values()))))
            now = time.time()
            db.execute("UPDATE knowledge SET text=?,status=?,decided_at=? WHERE id=?",
                       (text.strip(), status, now if status != PROPOSED else None, target))
            db.execute("UPDATE knowledge SET status=?,decided_at=? WHERE id IN (SELECT value FROM json_each(?))",
                       (REJECTED, now, archived))
            db.executemany("DELETE FROM settings WHERE key=?",
                           [(f"knowledge-review:{proj['id']}:{kid}",) for kid in sources])

    def move_to_profile(self, slug: str, ids: list[int]) -> None:
        """Keep reported compiler trials in the compiler panel, outside agent consensus."""
        if (not isinstance(ids, list) or not ids or any(type(kid) is not int for kid in ids)
                or len(set(ids)) != len(ids)):
            raise NetworkError("Choose at least one compiler-profile finding to move.")
        proj = self.net._project(slug)
        with self._store(slug).tx() as db:
            moved = db.execute(
                "UPDATE knowledge SET status=?, decided_at=? WHERE project_id=? AND status!=? "
                "AND id IN (SELECT value FROM json_each(?))", (REJECTED, time.time(), proj["id"], REJECTED,
                                                               json.dumps(ids)),
            )
            if moved.rowcount != len(ids):
                raise NetworkError("A finding is no longer active on this project. Refresh and select it again.")
            db.executemany("INSERT INTO settings(key,value) VALUES (?,?)",
                           [(f"knowledge-profile:{proj['id']}:{kid}", "1") for kid in ids])
            db.executemany("DELETE FROM settings WHERE key=?",
                           [(f"knowledge-review:{proj['id']}:{kid}",) for kid in ids])

    def forget(self, slug: str) -> None:
        """A deleted project's knowledge goes with it (its id may be a new project's later)."""
        pid = self.net._project(slug)["id"]
        with self._store(pid).tx() as db:
            db.execute("DELETE FROM knowledge_support WHERE knowledge_id IN (SELECT id FROM knowledge WHERE project_id=?)",
                       (pid,))
            db.execute("DELETE FROM knowledge WHERE project_id=?", (pid,))
            db.execute("DELETE FROM settings WHERE key LIKE ? OR key LIKE ?",
                       (f"knowledge-profile:{pid}:%", f"knowledge-review:{pid}:%"))

    def listing(self, slug: str, *, profile: bool = False) -> dict:
        """For the GUI: every proposal not rejected, with the models behind it, and the project's setting."""
        proj = self.net._project(slug)
        condition = PROFILE_FINDING if profile else "k.status != 'rejected'"
        rows = self._store(slug).all(
            f"SELECT k.* FROM knowledge k WHERE k.project_id=? AND {condition} ORDER BY k.id DESC", proj["id"])
        if not rows:
            return {"auto": self.auto(proj), "models_to_agree": MODELS_TO_AGREE, "items": []}
        support = {}
        for r in self._store(slug).all("SELECT s.knowledge_id, s.model FROM knowledge_support s JOIN knowledge k "
                                    "ON k.id=s.knowledge_id WHERE k.project_id=?", proj["id"]):
            support.setdefault(r["knowledge_id"], set()).add(r["model"])
        store = self._store(slug)
        from .project_labels import label
        evidence = {}
        rows_with_evidence = store.all(
            'SELECT e.knowledge_id,e.text,e.agent_id FROM knowledge_evidence e JOIN knowledge k ON k.id=e.knowledge_id '
            f'WHERE k.project_id=? AND {condition} ORDER BY e.id', proj['id'])
        words = {word for row in rows_with_evidence for word in re.findall(r'[A-Za-z_]\w*', row['text'])}
        functions = set()
        if words:
            functions = {row['name'] for row in store.all(
                'SELECT name FROM functions WHERE project_id=? AND name IN (SELECT value FROM json_each(?))',
                proj['id'], json.dumps(sorted(words)))}
        examples = [(row, example_details(row['text'])) for row in label(self.net, rows_with_evidence, {'agent':'agent_id'})]
        trials = trial_summaries(store, proj['id'], {key for _, example in examples for key in example['revisions']})
        history = {int(row['key'].rsplit(':',1)[1]):saved_trials(json.loads(row['value'])) for row in store.all(
            'SELECT key,value FROM settings WHERE key LIKE ?', f'knowledge-trials:{proj["id"]}:%')}
        for row, details in examples:
            saved = history.get(row['knowledge_id'], {})
            attached = [trials.get(key) or {**saved[key], 'restored':True} for key in details['revisions']
                        if key in trials or key in saved]
            evidence.setdefault(row['knowledge_id'], []).append({
                **row, **details, 'trials': attached,
                'functions': sorted(functions.intersection(re.findall(r'[A-Za-z_]\w*', row['text']))
                                    | {trial['function'] for trial in attached if not trial.get('restored')})})
        reviews = {int(row['key'].rsplit(':',1)[1]):json.loads(row['value']) for row in store.all(
            'SELECT key,value FROM settings WHERE key LIKE ?', f'knowledge-review:{proj["id"]}:%')}
        return {"auto": self.auto(proj), "models_to_agree": MODELS_TO_AGREE,
                "items": [{"id": r["id"], "text": r["text"], "status": r["status"], "t": r["created_at"],
                           "topic": topic(r["text"]),
                           "models": sorted(support.get(r["id"], ())),
                           "verification": reviews.get(r['id']),
                           "evidence": evidence.get(r['id'], [])} for r in rows]}

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
            with self._store(slug).tx() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps({'model':model,'agent':session.agent_name})))
            return {'message':f'Queued for {session.label} ({model}) at its next session. It must independently verify examples before backing the proposal.', 'model':model}

    def for_prompt(self, slug: str, agent=None) -> str:
        """What every agent starts with: the accepted knowledge, then the open proposals it can back."""
        proj = self.net._project(slug)
        out, used = [], 0
        accepted = self._store(slug).all("SELECT text FROM knowledge WHERE project_id=? AND status=? ORDER BY id DESC",
                                         proj["id"], ACCEPTED)
        for r in varied(accepted):
            if self._rule_mentions(proj, r["text"]):
                continue
            if used + len(r["text"]) > SHOWN_IN_PROMPT:
                continue
            out.append(f"- {r['text']}")
            used += len(r["text"])
        text = ("Accepted matching knowledge for this game (user-approved or model consensus):\n" + "\n".join(out)) if out else ""
        model = self._model_of(agent) if agent else None
        supported = {r['knowledge_id'] for r in self._store(slug).all('SELECT knowledge_id FROM knowledge_support WHERE model=?',model)} if model else set()
        queued = {}
        for r in self._store(slug).all('SELECT key,value FROM settings WHERE key LIKE ?',f'knowledge-review:{proj["id"]}:%'):
            item = json.loads(r['value'])
            if item['model'] == model and agent and item['agent'] == agent.name:
                queued[int(r['key'].rsplit(':',1)[1])] = item
        open_ = [r for r in self._open(proj) if r['id'] not in supported and not self._rule_mentions(proj, r["text"])]
        open_ = varied(open_)
        open_.sort(key=lambda r: r['id'] not in queued)
        open_ = open_[:OPEN_SHOWN]
        if open_:
            text += ("\n\nProposals waiting for a second model (back one with share_knowledge(supports=N) only when "
                     "your own work shows it):\n" + "\n".join(f"- #{r['id']}: {r['text']}" for r in open_))
        assigned = [r for r in open_ if r['id'] in queued]
        if assigned:
            text += '\nRequested independent verification (do not assume earlier claims are true):'
            for row in assigned:
                evidence = self._store(slug).all('SELECT text FROM knowledge_evidence WHERE knowledge_id=? ORDER BY id DESC LIMIT 3',row['id'])
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
        if not words:return []
        return [r['name'] for r in self._store(proj['id']).all(
            'SELECT name FROM functions WHERE project_id=? AND name IN (SELECT value FROM json_each(?))',
            proj['id'],json.dumps(sorted(words)))]

    def for_function(self, fn):
        """Retain legacy function-specific knowledge at its relevant function, without editing user data."""
        out = []
        for row in self._store(fn['project_id']).all('SELECT text,status FROM knowledge WHERE project_id=? AND status IN (?,?) ORDER BY id DESC',
                                      fn['project_id'], ACCEPTED, PROPOSED):
            if fn['name'] in set(re.findall(r'[A-Za-z_]\w*', row['text'])):
                out.append(f"{row['status']}: {row['text'][:600]}")
            if len(out) == 3:
                break
        return ['### Function-specific knowledge (historical reports)', *out] if out else []

    def relevant(self, fn, rows=()):
        from .repair_ranking import matching_topics
        store = self._store(fn['project_id'])
        accepted = store.all(
            'SELECT k.id,k.text FROM knowledge k WHERE k.project_id=? AND k.status=? ORDER BY k.id DESC',
            fn['project_id'], ACCEPTED)
        if not accepted:
            return []
        cited = {row['knowledge_id'] for row in store.all(
            'SELECT DISTINCT e.knowledge_id FROM knowledge_evidence e JOIN knowledge k ON k.id=e.knowledge_id '
            'WHERE k.project_id=? AND k.status=? AND instr(e.text,?)>0',
            fn['project_id'], ACCEPTED, fn['name'])}
        selected = set(matching_topics(rows))
        hits = [row for row in accepted if row['id'] in cited or topic(row['text']) in selected]
        hits.sort(key=lambda row: row['id'] not in cited)
        return ['### Relevant accepted matching tips', *(f"- {row['text']}" for row in hits[:3])] if hits else []
