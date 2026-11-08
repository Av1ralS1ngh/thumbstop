"""The head-to-head judge: group plans, the Bradley-Terry pooling, and that a consistent judge's order comes back."""
from __future__ import annotations

import re
from types import SimpleNamespace

from thumbstop.judge import GroupRanking, bradley_terry, groups_for, rank_group, tournament
from thumbstop.llm import LLMError


class FakeJudge:
    """Ranks promos by a hidden quality written in their transcript ("Q=7"), like a judge with stable taste."""

    def __init__(self, fail_every: int = 0):
        self.p = SimpleNamespace(max_concurrency=4, provider="fake", model="fake")
        self.calls, self.fail_every = 0, fail_every

    def json(self, system, user, schema, **kw):
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise LLMError("bad reply")
        qs = [int(q) for q in re.findall(r"Q=(\d+)", user)]
        order = sorted(range(1, len(qs) + 1), key=lambda i: -qs[i - 1])
        return GroupRanking(notes=[], ranking=order, reason="sharper ending")


def test_groups_cover_everyone_each_round_and_are_deterministic():
    ids = [f"P{i:02d}" for i in range(30)]
    gs = groups_for(ids, rounds=4, size=4)
    assert gs == groups_for(ids, rounds=4, size=4)
    per_round = len(gs) // 4
    for r in range(4):
        seen = [i for g in gs[r * per_round:(r + 1) * per_round] for i in g]
        assert sorted(seen) == ids
    assert all(3 <= len(g) <= 4 for g in gs)


def test_bradley_terry_orders_by_wins_and_stays_finite():
    wins = {("a", "b"): 3, ("b", "c"): 3, ("a", "c"): 3}
    s = bradley_terry(["a", "b", "c"], wins)
    assert s["a"] > s["b"] > s["c"] and all(abs(v) < 10 for v in s.values())


def test_rank_group_maps_numbers_back_and_keeps_omissions_last():
    class Partial(FakeJudge):
        def json(self, system, user, schema, **kw):
            return GroupRanking(notes=[], ranking=[3, 3, 9], reason="x")
    order, reason = rank_group(Partial(), [("a", 30, "x"), ("b", 30, "y"), ("c", 30, "z")])
    assert order == ["c", "a", "b"] and reason == "x"


def test_tournament_recovers_a_consistent_judges_order():
    items = {f"P{i:02d}": (30.0, f"line one Q={i}") for i in range(12)}
    res = tournament(FakeJudge(), items, rounds=4, size=4, log=lambda m: None)
    order = sorted(items, key=lambda i: -res["strength"][i])
    assert order[:3] == ["P11", "P10", "P09"] and order[-1] == "P00"
    assert res["failed"] == 0 and all(res["games"][i] > 0 for i in items)


def test_tournament_survives_failed_groups():
    items = {f"P{i:02d}": (30.0, f"Q={i}") for i in range(12)}
    res = tournament(FakeJudge(fail_every=4), items, rounds=4, size=4, log=lambda m: None)
    assert 0 < res["failed"] < res["calls"]
    order = sorted(items, key=lambda i: -res["strength"][i])
    assert order[0] in ("P11", "P10")
