"""Format-drift and new-input tests. Every fixture is original fiction (scripts/make_synthetic_show.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from thumbstop.annotate import rules_annotation
from thumbstop.ingest import load_episode, sniff
from thumbstop.llm import Router, load_config
from thumbstop.models import Episode
from thumbstop.parse import ParseError, ingest_report, parse_show
from thumbstop.pipeline import Options, run
from thumbstop.search import validate

FIX = Path(__file__).parent / "fixtures"
NEW_SHOW = FIX / "new_show"


def new_ep(num: int) -> Path:
    return next(NEW_SHOW.glob(f"episode-{num:03d}-*.txt"))


def dialogue(ep: Episode) -> list[tuple[str, float, float]]:
    return [(b.text, b.t0, b.t1) for b in ep.beats if b.kind == "dialogue"]


def kuku(*blocks: tuple[str, str, str, str], continuity: bool = True) -> str:
    """A minimal Kuku screenplay from (label, start, end, body) blocks."""
    head = ["EPISODE NUMBER: Episode 4", "EPISODE LOGLINE: A courier is framed for a missing parcel."]
    head += ["CONTINUITY NOTES: Picking up at the Episode 3 cliffhanger in the depot."] if continuity else []
    body = [f"**{lab}:**\nStart: ({t0}) | End: ({t1})\n{txt}\n" for lab, t0, t1, txt in blocks]
    return "\n".join(head + ["", "--- SCRIPT START ---", "", "SCENE 01", "Scene heading: INT. DEPOT – NIGHT",
                             "Scene start: 00:00.00", "Scene end: 00:20.00", "", *body, "--- SCRIPT END ---", ""])


SMALL = [("ACTION", "00:00.00", "00:02.00", "Rhea slams the parcel onto the counter."),
         ("RHEA (Furious)", "00:02.40", "00:05.10", '"You signed for it. Where is it?"'),
         ("DEV (Nervous)", "00:05.50", "00:08.00", '"I never saw that box in my life!"'),
         ("SOUND FX", "00:08.00", "00:10.00", "An alarm wails in the warehouse.")]


def write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------- Kuku screenplay drift
def test_new_show_shape():
    show = parse_show(NEW_SHOW)
    assert [e.num for e in show.episodes] == list(range(1, 11))
    for e in show.episodes:
        assert 60 <= e.duration <= 120 and 25 <= len(e.beats) <= 45 and not e.warnings


def test_upper_case_scene_fields():
    assert "SCENE HEADING:" in new_ep(2).read_text()
    ep = load_episode(new_ep(2))
    assert ep.scenes and all(s.heading and s.t0 is not None and s.t1 is not None for s in ep.scenes)


def test_hhmmss_timestamps_match_mmss(tmp_path):
    assert "Start: (00:00:" in new_ep(3).read_text()
    assert 60 <= load_episode(new_ep(3)).duration <= 120
    hh = [(lab, f"00:{a}", f"00:{b}", txt) for lab, a, b, txt in SMALL]
    a = load_episode(write(tmp_path, "episode-004-mmss.txt", kuku(*SMALL)))
    b = load_episode(write(tmp_path, "episode-004-hhmmss.txt", kuku(*hh)))
    assert [(x.t0, x.t1, x.text) for x in a.beats] == [(x.t0, x.t1, x.text) for x in b.beats]


def test_crlf_line_endings(tmp_path):
    raw = new_ep(4).read_bytes()
    assert b"\r\n" in raw
    lf = tmp_path / new_ep(4).name
    lf.write_bytes(raw.replace(b"\r\n", b"\n"))
    a, b = load_episode(new_ep(4)), load_episode(lf)
    assert [(x.id, x.t0, x.text) for x in a.beats] == [(x.id, x.t0, x.text) for x in b.beats]
    assert not any("\r" in x.text for x in a.beats)


def test_missing_continuity_notes(tmp_path):
    assert load_episode(new_ep(5)).continuity == ""
    assert load_episode(write(tmp_path, "episode-004.txt", kuku(*SMALL, continuity=False))).continuity == ""
    assert "Continuity notes present: 9/10" in ingest_report(parse_show(NEW_SHOW))


def test_missing_camera_blocks_still_annotates():
    ep = load_episode(new_ep(7))
    assert not [b for b in ep.beats if b.kind == "camera"]
    ann = rules_annotation(ep)
    assert ann.cliffhangers and ann.hooks


def test_inline_tags_stripped():
    ep = load_episode(new_ep(6))
    lines = [b for b in ep.beats if b.kind == "dialogue"]
    assert not any("[" in b.text or "]" in b.text for b in lines)
    assert any("laughs" in b.tags for b in lines)


def test_bom_and_stacked_speaker_parentheticals():
    assert new_ep(10).read_bytes().startswith(b"\xef\xbb\xbf")
    assert load_episode(new_ep(10)).num == 10
    ep8 = load_episode(new_ep(8))
    theo = next(b for b in ep8.beats if b.speaker == "THEO" and "OS" in b.flags)
    assert theo.tags == ["Pleading"]


def test_end_before_start_fails_with_file_line(tmp_path):
    bad = SMALL[:1] + [("RHEA (Furious)", "00:05.10", "00:02.40", '"Where is it?"')]
    with pytest.raises(ParseError, match=r"episode-004\.txt:\d+: End 00:02\.40 is before Start"):
        load_episode(write(tmp_path, "episode-004.txt", kuku(*bad)))
    srt = "1\n00:00:01,000 --> 00:00:03,000\nRHEA: Fine.\n\n2\n00:00:09,000 --> 00:00:04,000\nDEV: Not fine.\n"
    with pytest.raises(ParseError, match=r"episode-004\.srt:6: cue end .* before its start"):
        load_episode(write(tmp_path, "episode-004.srt", srt))


def test_seconds_over_60_fails(tmp_path):
    bad = SMALL[:1] + [("RHEA (Furious)", "00:75.00", "00:77.00", '"Where is it?"')]
    with pytest.raises(ParseError, match=r"episode-004\.txt:\d+: seconds field >= 60"):
        load_episode(write(tmp_path, "episode-004.txt", kuku(*bad)))
    srt = "1\n00:00:75,000 --> 00:00:76,000\nRHEA: Where is it?\n"
    with pytest.raises(ParseError, match=r"episode-004\.srt:2: seconds field >= 60"):
        load_episode(write(tmp_path, "episode-004.srt", srt))


# ---------------------------------------------------------------- subtitles and transcripts
SRT = """1
00:00:01,250 --> 00:00:03,900
RHEA: You signed for it.
Where is it?

2
00:00:04,300 --> 00:00:06,000
[alarm wails]

3
00:00:06,400 --> 00:00:09,100
DEV (O.S.): I never saw that box! [stammers]

4
00:00:09,500 --> 00:00:11,500
- Then who did?
- Ask your manager.
"""

VTT = """WEBVTT - depot fixture

NOTE written by hand, mirrors the SRT above

cue-1
00:01.250 --> 00:03.900 align:start position:10%
<v Rhea>You signed for it.
Where is it?</v>

00:00:04.300 --> 00:00:06.000
[alarm wails]

00:00:06.400 --> 00:00:09.100
<v.loud Dev>I never saw that box! [stammers]

00:00:09.500 --> 00:00:11.500
- Then who did?
- Ask your manager.
"""


def test_srt_and_vtt_parse_to_same_dialogue(tmp_path):
    a = load_episode(write(tmp_path, "episode-012.srt", SRT))
    b = load_episode(write(tmp_path, "ep12.vtt", VTT))
    assert a.num == b.num == 12
    da, db = dialogue(a), dialogue(b)
    assert [t for t, _, _ in da] == [t for t, _, _ in db] == [
        "You signed for it. Where is it?", "I never saw that box!", "Then who did?", "Ask your manager."]
    assert all(abs(x[1] - y[1]) <= 0.01 and abs(x[2] - y[2]) <= 0.01 for x, y in zip(da, db))
    assert [x.speaker for x in a.beats if x.kind == "dialogue"][:2] == ["RHEA", "DEV"]
    assert [x.speaker for x in b.beats if x.kind == "dialogue"][:2] == ["RHEA", "DEV"]
    dev = next(x for x in a.beats if x.speaker == "DEV")
    assert "OS" in dev.flags and "stammers" in dev.tags
    assert [x.text for x in a.beats if x.kind == "sfx"] == ["alarm wails"]
    assert a.beats[0].id == "E12-B001" and a.beats[0].src_line == 2


def test_subtitle_fixtures_match_the_screenplay():
    subs, show = parse_show(FIX / "subs_show"), parse_show(NEW_SHOW)
    assert [e.file.rsplit(".", 1)[1] for e in subs.episodes] == ["srt", "vtt", "txt"]
    for num in (1, 2):                  # SRT and VTT: same lines, same times (within 10 ms)
        got, want = dialogue(subs.ep(num)), dialogue(show.ep(num))
        assert [t for t, _, _ in got] == [t for t, _, _ in want]
        assert all(abs(g[1] - w[1]) <= 0.01 and abs(g[2] - w[2]) <= 0.01 for g, w in zip(got, want))
    tx, kk = subs.ep(3), show.ep(3)     # transcript: whole-second starts, estimated ends
    assert [t for t, _, _ in dialogue(tx)] == [t for t, _, _ in dialogue(kk)]
    assert [b.speaker for b in tx.beats] == [b.speaker for b in kk.beats if b.kind == "dialogue"]
    assert all(abs(g[1] - w[1]) < 1.0 for g, w in zip(dialogue(tx), dialogue(kk)))


def test_transcript_both_styles(tmp_path):
    text = ("Depot call, take 2\n\n[00:00:01] Rhea: You signed for it.\n00:00:04 Dev (O.S.): I never saw it!\n"
            "   it was never on my van.\n00:00:09.5 (door slams)\n00:00:10 Where is it?\n")
    ep = load_episode(write(tmp_path, "episode-005.txt", text))
    assert [(b.kind, b.speaker, b.text) for b in ep.beats] == [
        ("dialogue", "RHEA", "You signed for it."), ("dialogue", "DEV", "I never saw it! it was never on my van."),
        ("sfx", None, "door slams"), ("dialogue", None, "Where is it?")]
    assert ep.beats[0].t0 == 1.0 and 1.0 < ep.beats[0].t1 <= 4.0 and ep.beats[1].t1 <= 9.5
    back = "[00:00:05] Rhea: One.\n[00:00:02] Dev: Two.\n"
    with pytest.raises(ParseError, match=r"episode-006\.txt:2: time 00:00:02 is before"):
        load_episode(write(tmp_path, "episode-006.txt", back))


def test_content_sniffing_not_extension(tmp_path):
    assert load_episode(write(tmp_path, "episode-012.txt", SRT)).warnings[0].startswith("episode-012.txt: srt")
    assert load_episode(write(tmp_path, "episode-004.srt", kuku(*SMALL))).continuity  # a screenplay named .srt
    assert sniff(VTT) == "vtt" and sniff(SRT) == "srt" and sniff(kuku(*SMALL)) == "kuku"


def test_unknown_format_and_missing_episode_number_fail_loudly(tmp_path):
    notes = write(tmp_path, "episode-009-notes.txt", "Ideas for the finale:\nmore rain, bigger slap.\n")
    with pytest.raises(ParseError, match=r"episode-009-notes\.txt: unrecognised format"):
        load_episode(notes)
    with pytest.raises(ParseError, match="unrecognised format"):
        parse_show(tmp_path)
    with pytest.raises(ParseError, match=r"finale\.srt: no episode number in the filename"):
        load_episode(write(tmp_path, "finale.srt", SRT))
    with pytest.raises(ParseError, match=r"no WEBVTT header or numbered SRT cues"):
        load_episode(write(tmp_path, "episode-010.vtt", "00:01.000 --> 00:02.000\nHi.\n"))


# ---------------------------------------------------------------- end to end (offline, no network)
@pytest.mark.parametrize("folder,at_least", [(NEW_SHOW, 3), (FIX / "subs_show", 0), (FIX / "mini_show", 1)])
def test_offline_pipeline_end_to_end(tmp_path, folder, at_least):
    router = Router(load_config(), single="rules", out_dir=tmp_path)
    res = run(folder, tmp_path, router, Options(), log=lambda *_: None)
    ranked = res["ranked"]
    assert len(ranked) >= at_least
    assert all(validate(res["show"], p) == [] for p in ranked)
    assert (tmp_path / "promos.csv").exists()
