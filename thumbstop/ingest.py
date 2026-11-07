"""Adapters for inputs that are not Kuku screenplays: SRT and WebVTT subtitles (read with pysubs2) and plain
timed transcripts ("[00:01:02] Name: line"). Each produces the same Episode/Beat objects as parse.py, so
annotate, search, gate and the exports run unchanged.

The content decides the adapter, never the file extension, and anything unrecognised or malformed fails
loudly with file:line, so a bad input never silently produces wrong cut points.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Literal

from .models import Beat, Episode, Kind, Scene, fmt_ts
from .parse import KIND_LABELS, ParseError, _clean_text, _split_label, parse_episode, parse_time

Format = Literal["kuku", "srt", "vtt", "transcript"]
SUFFIXES = (".txt", ".srt", ".vtt")

RX_KUKU = re.compile(r"^\s*Start\s*:\s*\(|^\s*-{2,}\s*SCRIPT\s+START\s*-{2,}", re.I | re.M)
RX_SRT_CUE = re.compile(r"^\s*\d+\s*\n\s*[\d:.,]+\s*-->", re.M)
RX_CUE_TIME = re.compile(r"^\s*([\d:.,]+)\s*-->\s*([\d:.,]+)")
RX_VOICE = re.compile(r"^\s*<v(?:\.[\w.-]+)*\s+([^>]+)>")
TIME = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
RX_TIMED = re.compile(rf"^\s*\[?\s*({TIME})\s*\]?[ \t]+(?:[-–][ \t]+)?(\S.*)$")
# Subtitles mark speakers in capitals ("MAYA (O.S.): ..."); transcripts use any capitalised name (1-4 words).
RX_SUB_SPEAKER = re.compile(r"^([A-Z][A-Z0-9.'\- ]{0,30}?(?:\s*\([^)]*\))*)\s*:\s+(\S.*)$")
RX_TX_SPEAKER = re.compile(r"^([A-Z][\w.'\-]*(?: [A-Z0-9][\w.'\-]*){0,3}(?:\s*\([^)]*\))*)\s*:\s+(\S.*)$")
RX_SOUND_ONLY = re.compile(r"^\s*(?:\[([^\]]+)\]|\(([^)]+)\)|♪+\s*(.*?)\s*♪*)\s*$")
RX_FILE_NUM = (re.compile(r"episode[-_ ]?0*(\d+)", re.I), re.compile(r"(?<![a-z])ep?[-_ ]?0*(\d+)", re.I),
               re.compile(r"(\d+)"))


def sniff(text: str) -> Format | None:
    """Decide the adapter from the content (CRLF already normalised to LF)."""
    if text.lstrip("\ufeff \t\n").startswith("WEBVTT"):
        return "vtt"
    if RX_KUKU.search(text):
        return "kuku"
    if "-->" in text:
        return "srt" if RX_SRT_CUE.search(text) else None
    lines = [ln for ln in text.split("\n") if ln.strip()]
    timed = sum(1 for ln in lines if RX_TIMED.match(ln))
    return "transcript" if timed >= 2 and 2 * timed >= len(lines) else None


def episode_num(path: Path) -> int:
    """Episode number from the filename digits ('episode-003', 'ep03', 'S01E03', '03'); fails loudly if none."""
    for rx in RX_FILE_NUM:
        if (m := rx.search(path.stem)) and int(m.group(1)) > 0:
            return int(m.group(1))
    raise ParseError(f"{path.name}: no episode number in the filename (name it like 'episode-003.srt')")


def load_episode(path: Path) -> Episode:
    """Parse one episode file of any supported format."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError(f"{path.name}: not UTF-8 text ({e.reason} at byte {e.start}); re-save it as UTF-8") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    fmt = sniff(text)
    if fmt == "kuku":
        return parse_episode(path)
    if fmt == "srt" or fmt == "vtt":
        return parse_subtitles(path, text, fmt)
    if fmt == "transcript":
        return parse_transcript(path, text)
    hint = " (it has '-->' cue timings but no WEBVTT header or numbered SRT cues)" if "-->" in text else ""
    raise ParseError(f"{path.name}: unrecognised format{hint}. Expected a Kuku screenplay ('Start: (MM:SS.ss) | "
                     "End: (MM:SS.ss)' blocks), SRT (numbered cues with '-->'), WebVTT (starts with 'WEBVTT') "
                     "or a timed transcript ('[00:01:02] Name: line')")


# ---------------------------------------------------------------- subtitles
def _cue_lines(name: str, text: str) -> list[tuple[int, str | None]]:
    """Check every cue timing ourselves (pysubs2 accepts 75 s or an end before the start) and keep each cue's
    line number and WebVTT <v Speaker> voice, which pysubs2 strips."""
    lines = text.split("\n")
    cues: list[tuple[int, str | None]] = []
    for i, line in enumerate(lines):
        if "-->" not in line:
            continue
        where = f"{name}:{i + 1}"
        m = RX_CUE_TIME.match(line)
        if not m:
            raise ParseError(f"{where}: unreadable cue timing '{line.strip()[:60]}'")
        t0, t1 = parse_time(m.group(1), where), parse_time(m.group(2), where)
        if t1 < t0:
            raise ParseError(f"{where}: cue end {m.group(2)} is before its start {m.group(1)}")
        voice = RX_VOICE.match(lines[i + 1]) if i + 1 < len(lines) else None
        cues.append((i + 1, voice.group(1).strip() if voice else None))
    return cues


def parse_subtitles(path: Path, text: str, fmt: Literal["srt", "vtt"]) -> Episode:
    try:
        import pysubs2  # only subtitle inputs need it, so a screenplay-only setup runs without it
    except ImportError:
        raise ParseError(f"{path.name}: reading subtitles needs pysubs2 (pip install pysubs2)") from None
    ep = Episode(num=episode_num(path), file=path.name)
    cues = _cue_lines(path.name, text)
    try:
        events = list(pysubs2.SSAFile.from_string(text, format_=fmt))
    except Exception as e:  # pysubs2 raises several types; all mean the same thing here
        raise ParseError(f"{path.name}: could not read as {fmt.upper()}: {e}") from None
    if len(events) != len(cues):
        ep.warnings.append(f"{path.name}: {len(events)} cues read but {len(cues)} timing lines; line numbers dropped")
        cues = [(0, None)] * len(events)
    for ev, (line, voice) in zip(events, cues):
        body = html.unescape(ev.plaintext) if fmt == "vtt" else ev.plaintext
        chunks: list[str] = []          # "- Hi\n- Hey" is two speakers sharing one cue
        for part in (p.strip() for p in body.split("\n")):
            if part and (part.startswith("-") or not chunks):
                chunks.append(part.lstrip("-").strip())
            elif part:
                chunks[-1] += " " + part
        t0, span = ev.start / 1000, (ev.end - ev.start) / 1000
        total = sum(len(c) for c in chunks) or 1
        for c in chunks:                # split the cue's time by text length
            t1 = t0 + span * len(c) / total
            _add(ep, t0, t1, c, voice, RX_SUB_SPEAKER, line)
            t0 = t1
    return _finish(ep, f"{fmt} input: dialogue only, no scenes or action lines")


# ---------------------------------------------------------------- transcripts
def parse_transcript(path: Path, text: str) -> Episode:
    """'[00:01:02] Name: line' or '00:01:02 Name: line'. Untimed lines continue the previous line; lines
    before the first timestamp are a header. Only start times are given, so ends are estimated."""
    ep = Episode(num=episode_num(path), file=path.name)
    rows: list[tuple[float, str, int]] = []
    for i, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        if (m := RX_TIMED.match(line)):
            where = f"{path.name}:{i}"
            t0 = parse_time(m.group(1), where)
            if rows and t0 < rows[-1][0]:
                raise ParseError(f"{where}: time {m.group(1)} is before the previous line's {fmt_ts(rows[-1][0])}")
            rows.append((t0, m.group(2).strip(), i))
        elif rows:
            t0, body, at = rows[-1]
            rows[-1] = (t0, f"{body} {line.strip()}", at)
    for k, (t0, body, line) in enumerate(rows):
        nxt = rows[k + 1][0] if k + 1 < len(rows) else float("inf")
        est = max(1.0, 0.4 * len(body.split()) + 0.3)     # ~2.5 words per second
        _add(ep, t0, min(nxt, t0 + est), body, None, RX_TX_SPEAKER, line)
    return _finish(ep, "transcript input: start times only, line ends estimated from word count")


# ---------------------------------------------------------------- shared
def _add(ep: Episode, t0: float, t1: float, raw: str, voice: str | None, rx_speaker: re.Pattern[str],
         line: int) -> None:
    """Turn one timed line into a beat: a sound cue ('[door slams]'), a labelled ACTION/SFX line, or dialogue."""
    if not raw.strip():
        return
    kind: Kind = "dialogue"
    speaker: str | None = None
    tags: list[str] = []
    flags: list[str] = []
    if (m := RX_SOUND_ONLY.match(raw)):
        kind, text = "sfx", (m.group(1) or m.group(2) or m.group(3) or "music").strip()
    else:
        label = voice
        if label is None and (m := rx_speaker.match(raw)):
            label, raw = m.group(1), m.group(2)
        if label and (k := KIND_LABELS.get(label.split("(")[0].strip().upper())):
            kind = k  # type: ignore[assignment]
        elif label:
            speaker, tags, flags = _split_label(label)
            speaker = speaker.upper()
        text, inline = _clean_text(raw, kind)
        tags = tags + inline
        if not text:                    # "MAYA: [gasps]" -> a sound, not a line
            kind, text, speaker = "sfx", " ".join(filter(None, [speaker, *inline])), None
    n = len(ep.beats) + 1
    ep.beats.append(Beat(id=f"E{ep.num:02d}-B{n:03d}", ep=ep.num, idx=n, t0=round(t0, 3), t1=round(t1, 3),
                         kind=kind, text=text, speaker=speaker, tags=tags, flags=flags, scene=1, src_line=line))


def _finish(ep: Episode, note: str) -> Episode:
    dlg = [b for b in ep.beats if b.kind == "dialogue"]
    if not dlg:
        raise ParseError(f"{ep.file}: no timed dialogue lines found")
    ep.scenes = [Scene(num=1, heading="", t0=min(b.t0 for b in ep.beats), t1=max(b.t1 for b in ep.beats))]
    ep.warnings.append(f"{ep.file}: {note}")
    if len(dlg) < 3:
        ep.warnings.append(f"{ep.file}: only {len(dlg)} dialogue lines")
    return ep

