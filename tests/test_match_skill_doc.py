"""The match-function skill names only scripts that exist, and no longer tells agents to tag the library themselves."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "match-function"


def test_every_script_the_skill_names_exists():
    text = (SKILL / "SKILL.md").read_text()
    named = set(re.findall(r"scripts/([a-z_]+\.py)", text)) | {"setup.py", "fast_judge.py", "passes.py", "prove_chain.py",
                                                              "families.py", "judge.py", "compile_snippet.py",
                                                              "regions.py"}
    assert named and all((SKILL / "scripts" / name).exists() for name in named)
    assert (ROOT / "tools" / "learnings.py").exists()


def test_the_skill_leaves_tagging_and_the_pin_to_the_workflows_and_evidence_to_per_entry_files():
    text = (SKILL / "SKILL.md").read_text()
    assert "Never tag the library or bump its pin yourself" in text
    assert "git tag" not in text and "right before tagging" not in text
    assert "learnings.py new" in text and "setup.py" in text and "prove_chain.py" in text and "families.py" in text


def test_the_skill_documents_the_row_limit_and_the_venv_sync():
    text = (SKILL / "SKILL.md").read_text()
    assert "--rows" in text and "setup.py sync" in text


def test_the_skill_defaults_to_a_near_match_claim_and_keeps_random_available():
    text = (SKILL / "SKILL.md").read_text()
    assert 'strategy="nearest", max_insns=800' in text and "--random" in text and "--max-size" in text


def test_the_skill_hands_off_only_by_the_clock():
    text = (SKILL / "SKILL.md").read_text()
    assert "clock.py" in text and "hand-off allowed" in text and "45 minutes" in text and "handed off" in text


def test_the_skill_has_a_large_function_method():
    text = (SKILL / "SKILL.md").read_text()
    assert "regions.py" in text and "Fix the frame first" in text and "region by region" in text


def test_the_skill_names_the_grid_tool_and_its_marker_syntax():
    text = (SKILL / "SKILL.md").read_text()
    assert "grid.py" in text and "/*permute{*/" in text and "/*{*/" in text and "--count" in text


def test_the_skill_names_the_look_alike_tool_in_orientation():
    text = (SKILL / "SKILL.md").read_text()
    assert "similar.py SLUG FUNC" in text and "find_similar" in text and "--sources" in text


def test_the_skill_names_the_symptom_search_of_the_learnings():
    text = (SKILL / "SKILL.md").read_text()
    assert 'learnings.py search "register swap"' in text and "delay slot" in text


def test_the_skill_says_when_to_add_a_shared_type_and_that_the_app_checks_it():
    text = (SKILL / "SKILL.md").read_text()
    assert "shared_types.py add SLUG TYPE.h" in text and "edit_context" in text and "more than one function" in text
    assert "stops matching" in text


def test_the_skill_documents_the_mcp_caller_and_the_session_environment():
    text = (SKILL / "SKILL.md").read_text()
    assert "mcp.py <holder> --list" in text and "do not write your own MCP caller" in text.lower()
    assert "setup.py env <holder>" in text and (SKILL / "scripts" / "mcp.py").exists()
