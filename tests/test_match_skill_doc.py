"""The match-function skill names only scripts that exist, and no longer tells agents to tag the library themselves."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "match-function"


def test_every_script_the_skill_names_exists():
    text = (SKILL / "SKILL.md").read_text()
    named = set(re.findall(r"scripts/([a-z_]+\.py)", text)) | {"setup.py", "fast_judge.py", "passes.py", "prove_chain.py",
                                                              "families.py", "judge.py", "compile_snippet.py"}
    assert named and all((SKILL / "scripts" / name).exists() for name in named)
    assert (ROOT / "tools" / "learnings.py").exists()


def test_the_skill_leaves_tagging_and_the_pin_to_the_workflows_and_evidence_to_per_entry_files():
    text = (SKILL / "SKILL.md").read_text()
    assert "Never tag the library or bump its pin yourself" in text
    assert "git tag" not in text and "right before tagging" not in text
    assert "learnings.py new" in text and "setup.py" in text and "prove_chain.py" in text and "families.py" in text
