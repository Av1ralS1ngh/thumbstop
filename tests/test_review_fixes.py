"""Regression tests for the independent code review (one per fixed finding)."""
from __future__ import annotations

import argparse

import pytest

from thumbstop.__main__ import _range
from thumbstop.export import _say, run_fingerprint
from thumbstop.models import Beat, Episode, Segment, Show, fmt_ts
from thumbstop.rank import final_score, select, spoiler_note
from thumbstop.search import Promo, validate


def _promo(segs, pid="P1", score=50.0, moment=None, hook="", **kw) -> Promo:
    return Promo(id=pid, source="anchored", segments=[Segment(ep=e, start=a, end=b, first_beat="", last_beat="")
                                                      for e, a, b in segs], hook_beat=hook, cliff_beat="",
                 score=score, moment=moment or pid, **kw)


def _ep(num: int, n: int = 12) -> Episode:
    beats = [Beat(id=f"E{num:02d}-B{i + 1:03d}", ep=num, idx=i + 1, t0=5.0 * i, t1=5.0 * i + 4.0, kind="dialogue",
                  text=f"Line {i + 1}?", speaker="ANA") for i in range(n)]
    return Episode(num=num, file=f"e{num}.txt", beats=beats)


def test_fmt_ts_never_shows_60_seconds() -> None:            # finding 11
    assert fmt_ts(59.996) == "01:00.00"
    assert fmt_ts(119.999) == "02:00.00"
    assert fmt_ts(83.45) == "01:23.45"


def test_spoiler_note_uses_whole_show_length() -> None:      # finding 3
    show = Show(name="s", episodes=[_ep(8), _ep(9), _ep(10)], total_episodes=10)   # --episodes 8-10 of 10
    assert spoiler_note(show, _promo([(9, 0, 30)])).startswith("late-season clip (ep 9 of 10)")
    early = Show(name="s", episodes=[_ep(i) for i in range(1, 16)], total_episodes=50)  # --episodes 1-15 of 50
    assert spoiler_note(early, _promo([(12, 0, 30)])) == ""
    tiny = Show(name="s", episodes=[_ep(1), _ep(2), _ep(3)], total_episodes=3)
    assert spoiler_note(tiny, _promo([(3, 0, 30)])) == ""


def test_select_resets_stale_ranks_on_variants() -> None:    # finding 12
    x, v = _promo([(1, 0, 30)], "X", 90), _promo([(1, 0, 25)], "V", 80, moment="X")
    select([x, v], top=5)                                     # shortlist pass ranks V somewhere
    v.rank, v.rank_reason = 1, "ranked above #2: stale"
    chosen, variants = select([x, v], top=5)
    assert [p.id for p in chosen] == ["X"] and variants["X"] == [v]
    assert v.rank == 0 and v.rank_reason == ""


def test_per_episode_cap_counts_the_cliffhanger_episode() -> None:   # finding 4
    a = _promo([(3, 0, 30)], "A", 90)
    b = _promo([(3, 40, 60)], "B", 85)
    c = _promo([(2, 0, 5), (3, 70, 95)], "C", 80)            # setup from E2, cliffhanger in E3
    chosen, _ = select([a, b, c], top=10, per_episode=2)
    assert [p.id for p in chosen] == ["A", "B"]


def test_unchecked_promo_ranks_like_a_failed_one() -> None:   # finding 1
    passed = _promo([(1, 0, 30)], "OK", 60, gate={"passed": True, "hook_score": 2, "cliff_score": 2,
                                                   "standalone_score": 2})
    unchecked = _promo([(2, 0, 30)], "U", 80, gate={"passed": None, "unchecked": True})
    failed = _promo([(3, 0, 30)], "F", 80, gate={"passed": False})
    assert final_score(unchecked) == final_score(failed) < final_score(passed)
    assert final_score(_promo([(4, 0, 30)], "NG", 80)) == 80   # --no-gate: nothing marked, no penalty


def test_speakerless_subtitle_lines_have_no_none_label() -> None:   # finding 5
    b = Beat(id="E01-B001", ep=1, idx=1, t0=0.0, t1=2.0, kind="dialogue", text="Run!", speaker=None)
    assert _say(b) == '"Run!"'
    assert "None" not in b.render() and '"Run!"' in b.render()


def test_validate_rejects_segments_out_of_story_order() -> None:   # finding 9 (story order)
    show = Show(name="s", episodes=[_ep(2), _ep(3)])
    p = _promo([(3, 0.0, 14.0), (2, 20.0, 34.0)], hook="E03-B001")
    assert "OUT_OF_STORY_ORDER" in validate(show, p)
    ok = _promo([(2, 20.0, 34.0), (3, 0.0, 14.0)], hook="E02-B005")
    assert "OUT_OF_STORY_ORDER" not in validate(show, ok)


def test_cli_episode_range_parsing() -> None:                # finding 14
    assert _range("3-10") == (3, 10) and _range("7") == (7, 7) and _range("10-3") == (3, 10)
    assert _range(None) is None
    for bad in ("x", "1-y", "0-3"):
        with pytest.raises(argparse.ArgumentTypeError):
            _range(bad)


def test_review_page_key_changes_with_the_clips() -> None:   # finding 13
    rec = {"id": "P001", "segments": [{"episode": 1, "in_ts": "00:10.00", "out_ts": "00:40.00"}]}
    moved = {"id": "P001", "segments": [{"episode": 1, "in_ts": "00:12.00", "out_ts": "00:40.00"}]}
    assert run_fingerprint([rec]) == run_fingerprint([dict(rec)])
    assert run_fingerprint([rec]) != run_fingerprint([moved])
