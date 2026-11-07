"""Intent check: does a stranger leave with the question the writer meant? (editor warning, never a filter)"""
from pathlib import Path

import pytest

from thumbstop.gate import DRIFT_BELOW, drift_flag, intent_match
from thumbstop.llm import LLM, Budget, Profile
from thumbstop.rank import final_score
from thumbstop.search import Promo
from thumbstop.models import Segment


def _llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str, provider: str = "openai_compat") -> LLM:
    monkeypatch.setattr(LLM, "_provider", lambda self: (lambda system, user, schema, max_tokens: (reply, 50, 10)))
    p = Profile(name="fake", provider=provider, model="fake-1")
    return LLM(p, "gate", Budget(max_calls=5, max_usd=1.0), tmp_path / "cache", tmp_path / "trace.jsonl")


def test_intent_match_clamps_and_skips_empty(tmp_path, monkeypatch):
    llm = _llm(tmp_path, monkeypatch, '{"reason": "same", "match": 7}')
    assert intent_match(llm, "Who is at the door?", "Who is coming?") == {"intent_match": 3, "intent_note": "same"}
    assert intent_match(llm, "", "Who is coming?") is None
    assert intent_match(llm, "Who is at the door?", "  ") is None


def test_rules_mode_makes_no_intent_call(tmp_path, monkeypatch):
    llm = _llm(tmp_path, monkeypatch, "not json", provider="rules")
    assert intent_match(llm, "Who is at the door?", "Who is coming?") is None


def test_drift_flag_threshold():
    assert drift_flag(None) is None
    assert drift_flag({"passed": True}) is None                     # no intent check ran
    assert drift_flag({"intent_match": DRIFT_BELOW}) is None
    assert drift_flag({"intent_match": DRIFT_BELOW - 1}).startswith("QUESTION_DRIFT")


def test_drift_flag_never_changes_the_ranking_score():
    seg = Segment(ep=1, start=0.0, end=40.0, first_beat="E01-B001", last_beat="E01-B020")
    a = Promo(id="A", source="anchored", segments=[seg], hook_beat="E01-B001", cliff_beat="E01-B020", score=70.0,
              gate={"passed": True, "hook_score": 2, "cliff_score": 2, "standalone_score": 2, "intent_match": 0})
    b = Promo(id="B", source="anchored", segments=[seg], hook_beat="E01-B001", cliff_beat="E01-B020", score=70.0,
              gate={"passed": True, "hook_score": 2, "cliff_score": 2, "standalone_score": 2, "intent_match": 3})
    a.flags.append(drift_flag(a.gate))
    assert final_score(a) == final_score(b)
