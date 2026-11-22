"""search_learnings: the recorded compiler-matching evidence, searched by symptom (guide "near-match").

Hand sessions with the match-function skill start a stuck near-match with `tools/learnings.py search "<symptom>"`:
it ranks every docs/learnings/ entry and the older COMPILER_MATCHING_LEARNINGS.md sections by the symptom's words
(with synonyms) and gives each one's fix paragraph and the repair families it names, which prove_chain then runs.
App helpers had no way to read that evidence and repeated experiments it already records. This runs the same search
(the script is loaded by path, so the two can't drift) and returns the same text, bounded.

The evidence lives in the repository's docs/, beside the package: a copy running from a checkout (the usual way the
app runs here) has it. An installed wheel carries only the package, so the tool says so there (ROADMAP.md)."""
import importlib.util
from pathlib import Path

from ..network import NetworkError

TOP = 5
MAX_TOP = 10
MAX_QUERY = 200


def repository(start=None):
    """The checkout the package runs from (the folder holding tools/learnings.py and docs/learnings), or None."""
    here = Path(start or __file__).resolve()
    for parent in here.parents:
        if (parent / "tools" / "learnings.py").is_file() and (parent / "docs" / "learnings").is_dir():
            return parent
    return None


def _module(root):
    spec = importlib.util.spec_from_file_location("decompnet_learnings_script", root / "tools" / "learnings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def search(symptom, top=TOP, root=None):
    symptom = " ".join(str(symptom or "").split())[:MAX_QUERY]
    if not symptom:
        raise NetworkError('Describe the symptom in a few words, e.g. search_learnings("register swap s0 s1"), '
                           '"store too early", "frame size", "hoisted constant".')
    root = Path(root) if root else repository()
    if root is None:
        raise NetworkError("The matching evidence (docs/learnings) isn't in this installed copy of the app, only in a "
                           "checkout. Read guide(\"near-match\") instead; it carries the main rules.")
    module = _module(root)
    top = max(1, min(MAX_TOP, int(top or TOP)))
    hits = module.search(root, symptom, top=top, known=module.known_families(root))
    text = module.render_search(hits)
    if hits:
        text += ("\n\nEach entry's families are decomp-repair rewrites: prove_chain(function, revision, families=[...]) "
                 "runs them on your revision; try_local_repairs(families=[one]) tries one.")
    return text


def run(net, agent, slug, symptom, top=TOP):
    return search(symptom, top)
