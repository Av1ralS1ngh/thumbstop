"""Deterministic parser for Kuku's timestamped screenplay format.

Tolerant of case drift and spacing, but fails loudly (file:line) on anything it cannot place,
so a bad input never silently produces wrong cut points.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Beat, Episode, Scene, Show

RX_EPNUM = re.compile(r"^\s*EPISODE\s+NUMBER\s*:\s*(?:Episode\s*)?(\d+)", re.I)
RX_LOGLINE = re.compile(r"^\s*EPISODE\s+LOGLINE\s*:\s*(.*)$", re.I)
RX_CONT = re.compile(r"^\s*CONTINUITY\s+NOTES\s*:\s*(.*)$", re.I)
RX_SCENE = re.compile(r"^\s*SCENE\s+(\d+)\s*$", re.I)
RX_SC_HEAD = re.compile(r"^\s*Scene\s+heading\s*:\s*(.*)$", re.I)
RX_SC_START = re.compile(r"^\s*Scene\s+start\s*:\s*\(?([\d:.,]+)\)?\s*$", re.I)
RX_SC_END = re.compile(r"^\s*Scene\s+end\s*:\s*\(?([\d:.,]+)\)?\s*$", re.I)
RX_LABEL = re.compile(r"^\s*\*\*(.+?):?\*\*:?\s*$")
RX_TS = re.compile(
    r"^\s*Start\s*:\s*\(?\s*([\d:.,]+)\s*\)?\s*(?:\||-|–|to|\t)\s*End\s*:\s*\(?\s*([\d:.,]+)\s*\)?\s*$", re.I
)
RX_START = re.compile(r"^\s*-{2,}\s*SCRIPT\s+START\s*-{2,}\s*$", re.I)
RX_SKIP = re.compile(r"^\s*(actions?\s+and\s+dialogue\s*:?|-{2,}.*-{2,})\s*$", re.I)
RX_END = re.compile(r"^\s*-{2,}\s*SCRIPT\s+END\s*-{2,}\s*$", re.I)
RX_FILE_EP = re.compile(r"episode[-_ ]?0*(\d+)", re.I)
RX_INLINE_TAG = re.compile(r"\[([^\]]{1,40})\]")
FLAG_WORDS = {"o.s.": "OS", "os": "OS", "v.o.": "VO", "vo": "VO", "off-screen": "OS", "offscreen": "OS",
              "off screen": "OS", "continuing": "CONT", "cont'd": "CONT", "continues": "CONT"}
KIND_LABELS = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx",
               "SOUND": "sfx", "MUSIC": "sfx"}


class ParseError(ValueError):
    pass


def parse_time(s: str, where: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            m, sec = int(parts[0]), float(parts[1])
        elif len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
            m += 60 * h
        else:
            raise ValueError
    except ValueError:
        raise ParseError(f"{where}: unreadable timestamp '{s}'") from None
    if sec >= 60:
        raise ParseError(f"{where}: seconds field >= 60 in '{s}' (frames or bad clock?)")
    return m * 60 + sec


def _split_label(label: str) -> tuple[str, list[str], list[str]]:
    speaker = label.split("(")[0].strip()
    tags: list[str] = []
    flags: list[str] = []
    for group in re.findall(r"\(([^)]*)\)", label):
        for tok in re.split(r"[,/]", group):
            tok = tok.strip()
            if not tok:
                continue
            flag = FLAG_WORDS.get(tok.lower())
            if flag:
                flags.append(flag)
            else:
                tags.append(tok)
    return speaker, tags, flags


def _clean_text(raw: str, kind: str) -> tuple[str, list[str]]:
    inline = [m.strip() for m in RX_INLINE_TAG.findall(raw)]
    text = RX_INLINE_TAG.sub("", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if kind == "dialogue" and len(text) >= 2 and text[0] in "\"“" and text[-1] in "\"”":
        text = text[1:-1].strip()
    return text, inline


def parse_episode(path: Path) -> Episode:
    lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    m = RX_FILE_EP.search(path.name)
    ep = Episode(num=int(m.group(1)) if m else 0, file=path.name)
    header_num: int | None = None
    scene: Scene | None = None
    i, n, idx = 0, len(lines), 0
    in_script = False
    while i < n:
        line = lines[i]
        where = f"{path.name}:{i + 1}"
        if (mm := RX_EPNUM.match(line)):
            header_num = int(mm.group(1))
        elif (mm := RX_LOGLINE.match(line)):
            ep.logline = mm.group(1).strip()
        elif (mm := RX_CONT.match(line)):
            ep.continuity = mm.group(1).strip()
        elif (mm := RX_SCENE.match(line)):
            scene = Scene(num=int(mm.group(1)), heading="", t0=None, t1=None)
            ep.scenes.append(scene)
        elif scene and (mm := RX_SC_HEAD.match(line)):
            scene.heading = mm.group(1).strip()
        elif scene and (mm := RX_SC_START.match(line)):
            scene.t0 = parse_time(mm.group(1), where)
        elif scene and (mm := RX_SC_END.match(line)):
            scene.t1 = parse_time(mm.group(1), where)
        elif RX_END.match(line):
            break
        elif (mm := RX_LABEL.match(line)):
            label = mm.group(1).strip()
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            ts = RX_TS.match(lines[j]) if j < n else None
            if not ts:
                got = lines[j].strip()[:60] if j < n else "<EOF>"
                raise ParseError(f"{where}: block '**{label}:**' must be followed by "
                                 f"'Start: (MM:SS.ss) | End: (MM:SS.ss)', got '{got}'")
            t0 = parse_time(ts.group(1), f"{path.name}:{j + 1}")
            t1 = parse_time(ts.group(2), f"{path.name}:{j + 1}")
            if t1 < t0:
                raise ParseError(f"{path.name}:{j + 1}: End {ts.group(2)} is before Start {ts.group(1)}")
            k = j + 1
            body: list[str] = []
            while k < n and lines[k].strip() and not RX_LABEL.match(lines[k]) and not RX_SCENE.match(lines[k]) \
                    and not RX_END.match(lines[k]):
                body.append(lines[k].strip())
                k += 1
            kind = KIND_LABELS.get(label.upper(), "dialogue")
            speaker, tags, flags = (_split_label(label) if kind == "dialogue" else (None, [], []))
            text, inline = _clean_text(" ".join(body), kind)
            if not text:
                ep.warnings.append(f"{where}: empty text for '{label}', kept as empty beat")
            idx += 1
            ep.beats.append(Beat(
                id="", ep=0, idx=idx, t0=t0, t1=t1, kind=kind, text=text, speaker=speaker,
                tags=tags + inline, flags=flags, scene=scene.num if scene else 0, src_line=i + 1,
            ))
            i = k
            continue
        elif in_script and line.strip() and not RX_SKIP.match(line):
            ep.warnings.append(f"{where}: ignored unrecognised line '{line.strip()[:50]}'")
        if RX_START.match(line):
            in_script = True
        i += 1

    if header_num is not None:
        if m and header_num != ep.num:
            ep.warnings.append(f"{path.name}: header says episode {header_num}, filename says {ep.num}; using header")
        ep.num = header_num
    if ep.num == 0:
        raise ParseError(f"{path.name}: no episode number in header or filename")
    if not ep.beats:
        raise ParseError(f"{path.name}: no timestamped blocks found (is this a Kuku screenplay?)")
    for b in ep.beats:
        b.ep = ep.num
        b.id = f"E{ep.num:02d}-B{b.idx:03d}"
    dlg = [b for b in ep.beats if b.kind == "dialogue"]
    if len(dlg) < 3:
        ep.warnings.append(f"{path.name}: only {len(dlg)} dialogue lines")
    return ep


def parse_show(folder: str | Path) -> Show:
    from .ingest import SUFFIXES, load_episode  # ingest builds on this module; it sniffs each file's format

    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUFFIXES and p.is_file()
                   and not p.name.startswith("."))
    if not files:
        raise ParseError(f"{folder}: no episode files found (.txt screenplay or transcript, .srt, .vtt)")
    episodes = [load_episode(p) for p in files]
    episodes.sort(key=lambda e: e.num)
    nums = [e.num for e in episodes]
    if len(set(nums)) != len(nums):
        dup = sorted({x for x in nums if nums.count(x) > 1})
        raise ParseError(f"{folder}: duplicate episode numbers {dup}")
    name = files[0].parent.name if files[0].parent != folder or folder.name else folder.name
    return Show(name=name, episodes=episodes)


def ingest_report(show: Show) -> str:
    rows = []
    tot = {"dialogue": 0, "action": 0, "camera": 0, "sfx": 0}
    for e in show.episodes:
        for b in e.beats:
            tot[b.kind] += 1
    rows.append(f"Show: {show.name} | episodes: {len(show.episodes)} "
                f"({show.episodes[0].num}-{show.episodes[-1].num}) | runtime: "
                f"{sum(e.duration for e in show.episodes) / 60:.1f} min")
    rows.append("Beats: " + ", ".join(f"{k} {v}" for k, v in tot.items()))
    with_notes = sum(1 for e in show.episodes if e.continuity)
    rows.append(f"Continuity notes present: {with_notes}/{len(show.episodes)}"
                + ("" if with_notes else "  (WARNING: no writer notes; anchors rely on annotation only)"))
    warns = [w for e in show.episodes for w in e.warnings]
    rows.append(f"Warnings: {len(warns)}")
    rows.extend(f"  - {w}" for w in warns[:20])
    return "\n".join(rows)
