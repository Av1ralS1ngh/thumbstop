"""Core unit tests: parser, cut checks, validator, exports, LLM layer, ranking/policy, offline pipeline.
Everything runs offline; the only 'model' is a fake provider."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Callable

import pytest
from pydantic import BaseModel

import thumbstop.llm as llm_mod
from thumbstop.export import write_all, write_edl
from thumbstop.llm import LLM, Budget, BudgetExceeded, LLMError, Profile, Router, _is_throttle, load_config
from thumbstop.models import Beat, Episode, Segment, Show, fmt_tc, fmt_ts
from thumbstop.parse import ParseError, _split_label, parse_episode, parse_show
from thumbstop.pipeline import Options, run
from thumbstop.rank import policy_status, select
from thumbstop.search import OVERLAP_TOL, Promo, check_cut, validate

FPS = 30


# ---------------------------------------------------------------- builders
def mk_ep(num: int, specs: list[tuple[str, float, float, str]], speaker: str = "ANA") -> Episode:
    beats = [Beat(id=f"E{num:02d}-B{i:03d}", ep=num, idx=i, t0=t0, t1=t1, kind=kind, text=text,  # type: ignore[arg-type]
                  speaker=speaker if kind == "dialogue" else None)
             for i, (kind, t0, t1, text) in enumerate(specs, 1)]
    return Episode(num=num, file=f"e{num}.txt", beats=beats)


def grid_ep(num: int, n: int = 20) -> Episode:
    """Dialogue line i (1-based) runs [5(i-1), 5(i-1)+4]; 1 s of silence between lines."""
    return mk_ep(num, [("dialogue", 5.0 * i, 5.0 * i + 4.0, f"Line {i + 1}?") for i in range(n)])


def grid_show() -> Show:
    return Show(name="grid", episodes=[grid_ep(1), grid_ep(2)])


def mk_promo(segs: list[tuple[int, float, float]], hook: str = "", cliff: str = "", pid: str = "P001",
             **kw) -> Promo:
    return Promo(id=pid, source="anchored", segments=[Segment(ep=e, start=a, end=b, first_beat="", last_beat="")
                                                      for e, a, b in segs], hook_beat=hook, cliff_beat=cliff, **kw)


def tc_frames(tc: str) -> int:
    h, m, s, f = (int(x) for x in tc.split(":"))
    return ((h * 60 + m) * 60 + s) * FPS + f


# ---------------------------------------------------------------- parse
def test_parse_beat_ids_kinds_and_times(tiny_show: Show) -> None:
    ep1 = tiny_show.ep(1)
    assert [e.num for e in tiny_show.episodes] == [1, 2]
    assert [b.id for b in ep1.beats] == [f"E01-B{i:03d}" for i in range(1, len(ep1.beats) + 1)]
    b1, b2, b3 = ep1.beats[:3]
    assert (b1.kind, b2.kind, b3.kind) == ("camera", "action", "dialogue")
    assert (b3.t0, b3.t1, b3.src_line) == (3.2, 6.8, 22)
    assert b3.text == "Forty million dollars, gone. And your badge opened the vault."
    assert ep1.beat("E01-B006").kind == "sfx"
    assert {b.kind for b in ep1.beats} == {"camera", "action", "dialogue", "sfx"}
    assert tiny_show.beat("E02-B003").speaker == "NADIA VOSS"
    assert all(b.ep == e.num and b.id.startswith(f"E{e.num:02d}-") for e in tiny_show.episodes for b in e.beats)
    assert ep1.warnings == [] and tiny_show.ep(2).warnings == []


def test_split_label_speaker_tags_flags() -> None:
    assert _split_label("NAME (O.S.) (Cold, Furious)") == ("NAME", ["Cold", "Furious"], ["OS"])
    assert _split_label("NAME (V.O.)") == ("NAME", [], ["VO"])
    assert _split_label("THE STRANGER (Masked-like alias) (Cold)") == ("THE STRANGER", ["Masked-like alias", "Cold"], [])


def test_parse_speaker_tags_and_flags(tiny_show: Show) -> None:
    os_line = next(b for b in tiny_show.ep(1).beats if "OS" in b.flags)
    assert (os_line.speaker, os_line.tags, os_line.flags) == ("OWEN PIKE", ["Whispering"], ["OS"])
    vo_line = next(b for b in tiny_show.ep(2).beats if "VO" in b.flags)
    assert (vo_line.speaker, vo_line.tags) == ("NADIA VOSS", ["Breathless"])
    caller = next(b for b in tiny_show.ep(2).beats if b.speaker == "MYSTERY CALLER")
    assert caller.tags == ["Distorted Voice", "Calm"] and caller.flags == []
    assert all(b.speaker is None and b.tags == [] for e in tiny_show.episodes for b in e.beats if b.kind != "dialogue")


def test_inline_tags_removed_from_text(tiny_show: Show) -> None:
    b = tiny_show.beat("E01-B005")
    assert b.text == "Then explain the footage, Miss Voss. Take your time."
    assert b.tags == ["Cold", "Measured", "taps the screen"]
    scoff = tiny_show.beat("E02-B005")
    assert scoff.text == "You signed a confession this afternoon. You just don't remember it."
    assert "scoffs" in scoff.tags
    for e in tiny_show.episodes:
        for beat in e.beats:
            assert "[" not in beat.text and "  " not in beat.text
            if beat.kind == "dialogue":
                assert not beat.text.startswith('"') and not beat.text.endswith('"')


def test_parse_scene_timings_and_header(tiny_show: Show) -> None:
    ep1, ep2 = tiny_show.ep(1), tiny_show.ep(2)
    assert [(s.num, s.heading, s.t0, s.t1) for s in ep1.scenes] == [
        (1, "INT. GLASS BOARDROOM – DAY", 0.0, 48.0), (2, "INT. ELEVATOR LOBBY – NIGHT", 48.0, 92.0)]
    # upper-case field names (SCENE HEADING:, SCENE START:) parse the same way
    assert [(s.num, s.heading, s.t0, s.t1) for s in ep2.scenes] == [
        (1, "INT. ELEVATOR – NIGHT", 0.0, 46.0), (2, "INT. PARKING GARAGE – NIGHT", 46.0, 88.0)]
    assert (ep1.duration, ep2.duration) == (92.0, 88.0)
    assert {b.scene for b in ep1.beats if b.t0 < 48} == {1} and {b.scene for b in ep1.beats if b.t0 >= 48} == {2}
    assert ep2.continuity.startswith("Continues the Episode 1 cliffhanger")
    assert ep1.logline.startswith("A junior analyst")


def _write_ep(screenplay: Callable[..., str], folder: Path, name: str, blocks: list, num: int = 7) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(screenplay(num, [("INT. SERVER ROOM – NIGHT", 0.0, 30.0, blocks)]), encoding="utf-8")
    return p


def test_parse_missing_start_line_fails_with_file_and_line(tmp_path: Path, screenplay) -> None:
    blocks = [("ACTION", 0.0, 2.0, "Server fans roar."), ("IRA BLACKWOOD (Cold)", None, None, '"Nobody leaves."')]
    path = _write_ep(screenplay, tmp_path, "episode-007-broken.txt", blocks)
    line = path.read_text().splitlines().index("**IRA BLACKWOOD (Cold):**") + 1
    with pytest.raises(ParseError) as err:
        parse_episode(path)
    assert f"episode-007-broken.txt:{line}:" in str(err.value)
    assert "Start:" in str(err.value)


@pytest.mark.parametrize("blocks, needle", [
    ([("ACTION", 5.0, 4.0, "A door opens.")], "is before Start"),
    ([("ACTION", "00:75.00", "00:79.00", "A door opens.")], "seconds field >= 60"),
    (["Just prose, no timestamped blocks at all."], "no timestamped blocks"),
])
def test_parse_fails_loudly(tmp_path: Path, screenplay, blocks: list, needle: str) -> None:
    path = _write_ep(screenplay, tmp_path, "episode-007-bad.txt", blocks)
    with pytest.raises(ParseError, match=needle) as err:
        parse_episode(path)
    ts_lines = [i for i, ln in enumerate(path.read_text().splitlines(), 1) if ln.startswith("Start:")]
    if ts_lines:  # timestamp errors point at the timestamp line
        assert f"episode-007-bad.txt:{ts_lines[0]}:" in str(err.value)


def test_parse_show_rejects_duplicate_episode_numbers(tmp_path: Path, screenplay) -> None:
    blocks = [("ACTION", 0.0, 2.0, "A door opens.")]
    _write_ep(screenplay, tmp_path, "episode-003-a.txt", blocks, num=3)
    _write_ep(screenplay, tmp_path, "episode-004-b.txt", blocks, num=3)  # header wins over filename -> duplicate
    with pytest.raises(ParseError, match=r"duplicate episode numbers \[3\]"):
        parse_show(tmp_path)


# ---------------------------------------------------------------- check_cut
CUT_SPECS = [("dialogue", 2.0, 6.0, "First line."), ("dialogue", 6.5, 9.0, "Second line."),
             ("dialogue", 10.0, 16.0, "Third line, a long one."), ("action", 16.2, 18.0, "She turns."),
             ("dialogue", 18.5, 30.0, "A very long speech.")]


def test_check_cut_detects_mid_line_start() -> None:
    ep = mk_ep(1, CUT_SPECS)
    assert check_cut(ep, 4.0, 9.0) == (4.0, 9.0, ["MID_LINE_START:E01-B001"])
    assert check_cut(ep, 2.0, 9.0) == (2.0, 9.0, [])


def test_check_cut_tolerance_is_0_4s() -> None:
    ep = mk_ep(1, CUT_SPECS)
    assert OVERLAP_TOL == 0.4
    assert check_cut(ep, 2.39, 9.0)[2] == []                      # clips 0.39 s of the line's start: allowed
    assert check_cut(ep, 2.41, 9.0)[2] == ["MID_LINE_START:E01-B001"]
    assert check_cut(ep, 5.65, 9.0)[2] == []                      # previous line still talking 0.35 s: allowed
    assert check_cut(ep, 2.0, 15.7) == (2.0, 15.7, [])            # cuts 0.3 s before the line ends: allowed
    assert check_cut(ep, 2.0, 17.0) == (2.0, 17.0, [])            # inside an ACTION beat: not a spoken line


def test_check_cut_snaps_end_within_3s() -> None:
    ep = mk_ep(1, CUT_SPECS)
    assert check_cut(ep, 2.0, 14.0) == (2.0, 16.0, ["END_EXTENDED_TO:E01-B003"])
    assert check_cut(ep, 2.0, 13.0) == (2.0, 16.0, ["END_EXTENDED_TO:E01-B003"])  # exactly 3 s
    assert check_cut(ep, 2.0, 12.0) == (2.0, 12.0, ["MID_LINE_END:E01-B003"])    # 4 s left: refuse
    assert check_cut(ep, 2.0, 20.0) == (2.0, 20.0, ["MID_LINE_END:E01-B005"])


# ---------------------------------------------------------------- validate
def test_validate_accepts_a_clean_promo() -> None:
    show = grid_show()
    assert validate(show, mk_promo([(1, 10.0, 39.0)], "E01-B003", "E01-B008")) == []
    assert validate(show, mk_promo([(1, 5.0, 25.0)], "E01-B002", "E01-B005")) == []    # exactly 20 s
    assert validate(show, mk_promo([(1, 0.0, 60.0)], "E01-B001", "E01-B012")) == []    # exactly 60 s


def test_validate_duration_limits() -> None:
    show = grid_show()
    short = mk_promo([(1, 10.0, 24.0)], "E01-B003", "E01-B005")
    assert validate(show, short) == ["TOO_SHORT:14.0"]
    long_ = mk_promo([(1, 0.0, 64.0)], "E01-B001", "E01-B013")
    assert validate(show, long_) == ["TOO_LONG:64.0"]
    assert validate(show, short, lo=10, hi=15) == []


def test_validate_hook_must_land_within_3s() -> None:
    show = grid_show()
    assert validate(show, mk_promo([(1, 10.0, 39.0)], "E01-B004", "E01-B008")) == ["HOOK_TOO_LATE:5.0s"]
    ep = mk_ep(3, [("dialogue", 0.0, 2.5, "Open."), ("action", 3.0, 4.0, "A glass shatters."),
                   ("action", 3.2, 4.0, "Everyone turns."), ("dialogue", 4.5, 24.0, "Who did this?")])
    s = Show(name="s", episodes=[ep])
    assert validate(s, mk_promo([(3, 0.0, 24.0)], "E03-B002", "E03-B004")) == []          # at 3.0 s: ok
    assert validate(s, mk_promo([(3, 0.0, 24.0)], "E03-B003", "E03-B004")) == ["HOOK_TOO_LATE:3.2s"]
    assert "NO_HOOK_BEAT" in validate(s, mk_promo([(3, 0.0, 24.0)], "E03-B099", "E03-B004"))


def test_validate_cliff_must_end_the_clip() -> None:
    show = grid_show()
    two = [(1, 0.0, 14.0), (1, 40.0, 54.0)]
    assert validate(show, mk_promo(two, "E01-B001", "E01-B011")) == []
    assert validate(show, mk_promo(two, "E01-B001", "E01-B002")) == ["CLIFF_NOT_AT_END"]   # in the first segment
    assert validate(show, mk_promo(two, "E01-B001", "E02-B011")) == ["CLIFF_NOT_AT_END"]   # other episode
    # inside the last segment but followed by 40 s of other material: the clip does not END on it
    assert validate(show, mk_promo([(1, 10.0, 59.0)], "E01-B003", "E01-B004")) == ["CLIFF_NOT_AT_END"]


def test_validate_segments_overlap() -> None:
    show = grid_show()
    p = mk_promo([(1, 0.0, 14.0), (1, 10.0, 39.0)], "E01-B001", "E01-B008")
    assert "SEGMENTS_OVERLAP" in validate(show, p)


def test_validate_sums_multi_segment_duration() -> None:
    show = grid_show()
    two = mk_promo([(1, 0.0, 14.0), (2, 40.0, 54.0)], "E01-B001", "E02-B011")   # 14 s + 14 s, two episodes
    assert two.duration == 28.0 and two.episodes == [1, 2]
    assert validate(show, two) == []
    three = mk_promo([(1, 0.0, 24.0), (1, 30.0, 54.0), (2, 0.0, 24.0)], "E01-B001", "E02-B005")
    assert validate(show, three) == ["TOO_LONG:72.0"]
    late = mk_promo([(1, 0.0, 14.0), (1, 40.0, 54.0)], "E01-B009", "E01-B011")   # hook opens segment 2
    assert validate(show, late) == ["HOOK_TOO_LATE:14.0s"]


# ---------------------------------------------------------------- export
def test_fmt_tc_rounds_to_nearest_frame() -> None:
    assert fmt_tc(0.0) == "00:00:00:00"
    assert fmt_tc(10.5) == "00:00:10:15"
    assert fmt_tc(3725.2) == "01:02:05:06"
    assert fmt_tc(20.99) == "00:00:21:00"     # 629.7 frames -> frame 630, not 20 s + 29 f
    assert fmt_ts(72.5) == "01:12.50"


def test_write_edl_cmx3600_shape(tmp_path: Path) -> None:
    show = grid_show()
    p1 = mk_promo([(1, 10.0, 40.15)], pid="P001", rank=1, hook_type="humiliation", cliff_type="threat_delivered")
    p2 = mk_promo([(1, 50.5, 62.0), (2, 3.2, 20.99)], pid="P002", rank=2)
    write_edl(tmp_path / "selects.edl", show, [p1, p2])
    lines = (tmp_path / "selects.edl").read_text().splitlines()
    assert lines[0].startswith("TITLE: ") and lines[1] == "FCM: NON-DROP FRAME"
    rx = re.compile(r"^(\d{3})  (EP\d{3}) +([VA]) +C +((?:\d\d:){3}\d\d) ((?:\d\d:){3}\d\d) "
                    r"((?:\d\d:){3}\d\d) ((?:\d\d:){3}\d\d)$")
    events = [m.groups() for m in map(rx.match, lines) if m]
    assert len(events) == 6                                   # 3 segments x (video + audio)
    assert [e[0] for e in events] == ["001", "001", "002", "002", "003", "003"]
    assert [e[2] for e in events] == ["V", "A"] * 3
    assert [e[1] for e in events[::2]] == ["EP001", "EP001", "EP002"]
    assert [(e[3], e[4]) for e in events[2::2]] == [("00:00:50:15", "00:01:02:00"), ("00:00:03:06", "00:00:21:00")]
    assert events[0][3] == "00:00:10:00" and events[0][5] == "01:00:00:00"
    for _, _, _, si, so, ri, ro in events:                   # record length == source length, frame exact
        assert tc_frames(ro) - tc_frames(ri) == tc_frames(so) - tc_frames(si)
    assert events[4][5] == events[2][6]                       # segments of one promo are back to back
    assert tc_frames(events[2][5]) - tc_frames(events[0][6]) == 2 * FPS   # 2 s gap between promos
    assert any(ln.startswith("* PROMO #1 P001") for ln in lines)


def test_promos_csv_one_row_per_segment(tmp_path: Path) -> None:
    show = grid_show()
    p1 = mk_promo([(1, 10.0, 39.0)], "E01-B003", "E01-B008", pid="P001", rank=1, why="framed in public")
    p2 = mk_promo([(1, 0.0, 14.0), (2, 40.0, 54.0)], "E01-B001", "E02-B011", pid="P002", rank=2, why="a threat")
    write_all(show, tmp_path, [p1, p2], {}, [], {})
    with open(tmp_path / "promos.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert {"rank", "episode", "start", "end", "hook", "cliffhanger", "why_it_works"} <= set(rows[0])
    assert len(rows) == 3
    assert [(r["rank"], r["segment"], r["episode"], r["start"], r["end"]) for r in rows] == [
        ("1", "1", "1", "00:10.00", "00:39.00"), ("2", "1", "1", "00:00.00", "00:14.00"),
        ("2", "2", "2", "00:40.00", "00:54.00")]
    assert rows[0]["hook"] == 'ANA: "Line 3?"' and rows[0]["cliffhanger"] == 'ANA: "Line 8?"'
    assert [r["why_it_works"] for r in rows] == ["framed in public", "a threat", "a threat"]
    doc = json.loads((tmp_path / "promos.json").read_text())
    assert [len(p["segments"]) for p in doc["promos"]] == [1, 2]
    for name in ("report.md", "review.html", "selects.edl"):
        assert (tmp_path / name).stat().st_size > 0


# ---------------------------------------------------------------- llm
class Verdict(BaseModel):
    verdict: str
    score: int


class FakeProvider:
    def __init__(self, replies: list) -> None:
        self.replies, self.users = list(replies), []

    def __call__(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        self.users.append(user)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, 100, 20


def _fake_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list, budget: Budget | None = None,
              **prof) -> tuple[LLM, FakeProvider]:
    fake = FakeProvider(replies)
    monkeypatch.setattr(LLM, "_provider", lambda self: fake)
    p = Profile(name="fake", provider="openai_compat", model="fake-1", **prof)
    return LLM(p, "annotate", budget or Budget(max_calls=10, max_usd=1.0), tmp_path / "cache",
               tmp_path / "trace.jsonl"), fake


def test_llm_retries_once_on_invalid_json_then_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, fake = _fake_llm(tmp_path, monkeypatch, ["Sure! Here is my verdict: keep it.",
                                                  '```json\n{"verdict": "keep", "score": 3}\n```'])
    out = llm.json("system", "clip A", Verdict, tag="t1")
    assert out == Verdict(verdict="keep", score=3)
    assert len(fake.users) == 2                                   # exactly one repair retry
    assert "did not match the required JSON schema" in fake.users[1]
    u = llm.budget.usage
    assert (u.calls, u.cache_hits, u.tokens_in, u.tokens_out) == (2, 0, 200, 40)

    again = llm.json("system", "clip A", Verdict, tag="t1")       # identical request -> disk cache
    assert again == out and len(fake.users) == 2
    assert (u.calls, u.cache_hits) == (2, 1)
    trace = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [(t["ok"], t["cached"]) for t in trace] == [(False, False), (True, False), (True, True)]


def test_llm_gives_up_after_two_invalid_replies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, fake = _fake_llm(tmp_path, monkeypatch, ['{"verdict": "keep"}', "not json"])
    with pytest.raises(LLMError, match="schema validation failed twice"):
        llm.json("system", "clip B", Verdict)
    assert len(fake.users) == 2


def test_llm_budget_caps_calls_and_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, fake = _fake_llm(tmp_path, monkeypatch, ['{"verdict": "a", "score": 1}'], Budget(max_calls=1, max_usd=99))
    llm.json("system", "u1", Verdict)
    with pytest.raises(BudgetExceeded, match="call budget 1"):
        llm.json("system", "u2", Verdict)
    assert len(fake.users) == 1                                   # no provider call once the cap is hit
    assert llm.json("system", "u1", Verdict).verdict == "a"       # cached answers are still served

    llm2, _ = _fake_llm(tmp_path / "b", monkeypatch, ['{"verdict": "a", "score": 1}'],
                        Budget(max_calls=99, max_usd=0.0005), price_in=10.0)
    llm2.json("system", "u1", Verdict)                            # 100 tokens at $10/M = $0.001 > cap
    with pytest.raises(BudgetExceeded, match="cost budget"):
        llm2.json("system", "u2", Verdict)


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize("exc, expected", [
    (Exception("An error occurred (ThrottlingException) when calling the Converse operation: Too many requests"), True),
    (RuntimeError("Error code: 429 - {'error': 'slow down'}"), True),
    (RateLimitError("please retry"), True),
    (Exception("Error code: 529 - overloaded_error"), True),
    (Exception("ServiceUnavailableException: try later"), True),
    (ValueError("ValidationException: input is too long"), False),
    (Exception("ValidationException: prompt has 14290 tokens, limit is 8192"), False),
    (Exception("AccessDeniedException (RequestId: 5f3a4291-0c1e-4b5d-9a29-1f4c7e8d2a52)"), False),
])
def test_is_throttle(exc: Exception, expected: bool) -> None:
    assert _is_throttle(exc) is expected


def test_llm_backs_off_only_on_throttling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", sleeps.append)
    llm, fake = _fake_llm(tmp_path, monkeypatch, [Exception("ThrottlingException: Rate exceeded"),
                                                  '{"verdict": "ok", "score": 2}'])
    assert llm.json("system", "u1", Verdict).score == 2
    assert len(sleeps) == 1 and llm.budget.usage.calls == 1

    sleeps.clear()
    llm2, _ = _fake_llm(tmp_path / "b", monkeypatch,
                        [Exception("ValidationException (RequestId: 0a4290ff-1111-2222-3333-444455556666)")])
    with pytest.raises(LLMError, match="ValidationException"):
        llm2.json("system", "u1", Verdict)
    assert sleeps == []


def test_router_single_profile_mode(tmp_path: Path) -> None:
    cfg = {"profiles": {"offline": {"provider": "rules", "model": "rules"},
                        "remote": {"provider": "openai_compat", "model": "some-model"}},
           "roles": {"annotate": "remote", "gate": "remote", "repair": "remote", "propose": "remote"}}
    r = Router(cfg, single="offline", out_dir=tmp_path)
    r.cache_dir = tmp_path / "cache"
    assert r.roles == {"annotate": "offline", "gate": "offline", "repair": "offline", "propose": "offline"}
    assert {r[role].p.name for role in r.roles} == {"offline"}
    assert r["annotate"].budget is r["gate"].budget                # one budget for the whole run
    r2 = Router(cfg, single="offline", overrides={"gate": "remote", "repair": None})
    assert r2.roles == {"annotate": "offline", "gate": "remote", "repair": "offline", "propose": "offline"}
    real = load_config()                                          # shipped config: every role has a profile
    assert set(real["roles"].values()) <= set(real["profiles"])


# ---------------------------------------------------------------- rank / policy
def _policy_show(specs: list[tuple[str, float, float, str]]) -> Show:
    return Show(name="p", episodes=[mk_ep(1, specs)])


def test_policy_slap_is_check_never_hold() -> None:
    show = _policy_show([("action", 0.0, 2.0, "Victor slaps Mara across the face in front of the whole office."),
                         ("dialogue", 2.5, 5.0, "You will regret that slap."), ("dialogue", 5.5, 25.0, "Get out.")])
    pol = policy_status(show, mk_promo([(1, 0.0, 25.0)]))
    assert pol["status"] == "CHECK"
    assert {(e["level"], e["rule"], e["beat"]) for e in pol["evidence"]} == {
        ("CHECK", "violence", "E01-B001"), ("CHECK", "violence", "E01-B002")}


def test_policy_nudity_is_hold_with_verbatim_evidence() -> None:
    text = "She lets the robe fall and stands naked at the window."
    show = _policy_show([("dialogue", 0.0, 2.0, "Close the blinds."), ("action", 2.5, 6.0, text),
                         ("dialogue", 6.5, 25.0, "Who is watching?")])
    pol = policy_status(show, mk_promo([(1, 0.0, 25.0)]))
    assert pol["status"] == "HOLD"
    assert {"level": "HOLD", "rule": "nudity_sexual", "beat": "E01-B002", "evidence": text} in pol["evidence"]
    assert policy_status(show, mk_promo([(1, 6.5, 25.0)]))["status"] == "CLEAR"   # outside the clip: no flag


def test_policy_hold_evidence_survives_many_check_hits() -> None:
    fights = ["He shoves the intern.", "She punches the wall.", "Security kicks the door in.",
              "He chokes on the coffee.", "Blood on the carpet.", "A slap echoes.", "The guard whips round."]
    specs = [("action", 3.0 * i, 3.0 * i + 2.0, t) for i, t in enumerate(fights)]
    specs.append(("action", 22.0, 25.0, "She steps out of the shower, naked."))
    pol = policy_status(_policy_show(specs), mk_promo([(1, 0.0, 25.0)]))
    assert pol["status"] == "HOLD"
    hold = [e for e in pol["evidence"] if e["level"] == "HOLD"]
    assert hold and hold[0]["evidence"] == "She steps out of the shower, naked."


def _p(pid: str, ep: int, a: float, b: float, score: float, moment: str | None = None) -> Promo:
    return mk_promo([(ep, a, b)], pid=pid, score=score, moment=moment or pid)


def test_select_respects_per_episode_cap() -> None:
    a, b, c, d = _p("A", 1, 0, 30, 90), _p("B", 1, 40, 70, 80), _p("C", 1, 80, 110, 70), _p("D", 2, 0, 30, 60)
    chosen, _ = select([d, c, b, a], top=10, per_episode=2)
    assert [p.id for p in chosen] == ["A", "B", "D"] and [p.rank for p in chosen] == [1, 2, 3]
    chosen, _ = select([a, b, c, d], top=10, per_episode=3)
    assert [p.id for p in chosen] == ["A", "B", "C", "D"]
    chosen, _ = select([a, b, c, d], top=2, per_episode=3)
    assert [p.id for p in chosen] == ["A", "B"]


def test_select_overlap_rule_and_variants() -> None:
    x, y, z = _p("X", 1, 0, 30, 90), _p("Y", 1, 10, 40, 80), _p("Z", 1, 25, 55, 70)   # Y overlaps X by 2/3
    v = _p("V", 1, 0, 25, 85, moment="X")                                            # same moment as X
    other_ep = _p("W", 2, 0, 30, 60)                                                 # same times, other episode
    chosen, variants = select([x, y, z, v, other_ep], top=10, per_episode=5)
    assert [p.id for p in chosen] == ["X", "Z", "W"]
    assert [p.id for p in variants["X"]] == ["V"]


# ---------------------------------------------------------------- pipeline
def test_pipeline_offline_end_to_end(tiny_show_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "run"
    router = Router(load_config(), single="rules", out_dir=out)
    router.cache_dir = tmp_path / "cache"
    res = run(tiny_show_dir, out, router, Options(), log=lambda *a, **k: None)
    for name in ("promos.json", "promos.csv", "report.md", "review.html", "selects.edl"):
        assert (out / name).stat().st_size > 0, name
    ranked = res["ranked"]
    assert ranked and [p.rank for p in ranked] == list(range(1, len(ranked) + 1))
    for p in ranked:
        assert validate(res["show"], p) == [], p.id
        assert p.policy.get("status") in {"CLEAR", "CHECK", "HOLD"}
    doc = json.loads((out / "promos.json").read_text())
    assert [p["id"] for p in doc["promos"]] == [p.id for p in ranked]
    with open(out / "promos.csv", newline="") as f:
        assert len(list(csv.DictReader(f))) == sum(len(p.segments) for p in ranked)
    edl = (out / "selects.edl").read_text().splitlines()
    assert sum(1 for ln in edl if re.match(r"^\d{3}  EP\d{3}", ln)) == 2 * sum(len(p.segments) for p in ranked)
    assert router.budget.usage.calls == 0                          # offline: no model calls at all
