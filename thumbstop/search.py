"""Anchored search: keep a writer cliffhanger as the END, search for a standalone hook-first START.
All timestamps are computed here from parsed beats; nothing time-related comes from a model."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .annotate import EpisodeAnnotation
from .models import Beat, Episode, Segment, Show, fmt_ts

MIN_LEN, MAX_LEN = 20.0, 60.0
HOOK_WINDOW = 3.0          # hook beat must start within 3 s of promo start
OVERLAP_TOL = 0.4          # dialogue may overlap a cut point by this much without counting as mid-line
TIGHT_MAX = 32.0


@dataclass
class Promo:
    id: str
    source: str                         # anchored | proposed | last45 | first45 | random | repaired
    segments: list[Segment]
    hook_beat: str
    cliff_beat: str
    hook_type: str = "other"
    cliff_type: str = "other"
    hook_strength: int = 0
    cliff_strength: int = 0
    needs_context: int = 0
    open_question: str = ""
    why: str = ""
    moment: str = ""                    # cliff beat id; variants of one moment share it
    variant: str = "full"               # tight | full
    score: float = 0.0
    note_confirmed: bool = False
    flags: list[str] = field(default_factory=list)
    gate: dict | None = None
    repair: dict | None = None
    policy: dict = field(default_factory=dict)
    spoiler: str = ""
    rank: int = 0
    rank_reason: str = ""

    @property
    def duration(self) -> float:
        return round(sum(s.dur for s in self.segments), 2)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.ep for s in self.segments})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        for s in d["segments"]:
            s["start_ts"], s["end_ts"] = fmt_ts(s["start"]), fmt_ts(s["end"])
            s["dur"] = round(s["end"] - s["start"], 2)
        return d


# ---------------------------------------------------------------- cut-point checks (pure code)
def _dialogue(ep: Episode) -> list[Beat]:
    return sorted((b for b in ep.beats if b.kind == "dialogue"), key=lambda b: b.t0)


def check_cut(ep: Episode, start: float, end: float) -> tuple[float, float, list[str]]:
    """Return (start, end, issues). Snaps an end that lands inside a spoken line to the line's end
    when that costs <= 3 s; otherwise reports MID_LINE_* so the caller rejects or flags the cut."""
    issues = []
    for d in _dialogue(ep):
        if d.t0 < start - OVERLAP_TOL and d.t1 > start + OVERLAP_TOL:
            issues.append(f"MID_LINE_START:{d.id}")
        if d.t0 < end - OVERLAP_TOL and d.t1 > end + OVERLAP_TOL:
            if d.t1 - end <= 3.0:
                end = d.t1
                issues.append(f"END_EXTENDED_TO:{d.id}")
            else:
                issues.append(f"MID_LINE_END:{d.id}")
    return start, end, issues


def cut_confidence(ep: Episode, start: float, end: float) -> dict:
    """How safe the cut is on real audio, from the script's own silences."""
    dl = _dialogue(ep)
    before = [d.t1 for d in dl if d.t1 <= start + OVERLAP_TOL and d.t0 < start]
    after = [d.t0 for d in dl if d.t0 >= end - OVERLAP_TOL and d.t1 > end]
    gap_in = round(start - max(before), 2) if before else 9.9
    gap_out = round(min(after) - end, 2) if after else 9.9
    worst = min(gap_in, gap_out)
    level = "high" if worst >= 0.3 else ("medium" if worst >= 0.05 else "low")
    return {"gap_before_in_s": gap_in, "gap_after_out_s": gap_out, "level": level,
            "handles": {"pre_s": round(min(0.5, max(gap_in, 0)), 2), "post_s": round(min(1.0, max(gap_out, 0)), 2)}}


def validate(show: Show, p: Promo, lo: float = MIN_LEN, hi: float = MAX_LEN) -> list[str]:
    """Hard rules. Empty list = valid."""
    errs = []
    if not p.segments:
        return ["NO_SEGMENTS"]
    if len(p.segments) > 3:
        errs.append("TOO_MANY_SEGMENTS")
    dur = p.duration
    if dur < lo - 0.01:
        errs.append(f"TOO_SHORT:{dur}")
    if dur > hi + 0.01:
        errs.append(f"TOO_LONG:{dur}")
    for s in p.segments:
        ep = show.ep(s.ep)
        if ep is None:
            errs.append(f"NO_EPISODE:{s.ep}")
            continue
        if not (0 <= s.start < s.end <= ep.duration + 0.5):
            errs.append(f"OUT_OF_RANGE:E{s.ep}")
        _, _, issues = check_cut(ep, s.start, s.end)
        errs += [i for i in issues if i.startswith("MID_LINE")]
    for a, b in zip(p.segments, p.segments[1:]):
        if a.ep == b.ep and b.start < a.end - 0.01:
            errs.append("SEGMENTS_OVERLAP")
    hb = show.beat(p.hook_beat)
    if hb is None:
        errs.append("NO_HOOK_BEAT")
    else:
        off, found = 0.0, False
        for s in p.segments:
            if s.ep == hb.ep and s.start - 0.01 <= hb.t0 <= s.end:
                off += hb.t0 - s.start
                found = True
                break
            off += s.dur
        if not found:
            errs.append("HOOK_NOT_IN_CLIP")
        elif off > HOOK_WINDOW:
            errs.append(f"HOOK_TOO_LATE:{off:.1f}s")
    cb = show.beat(p.cliff_beat)
    last = p.segments[-1]
    if cb is None or cb.ep != last.ep or not (last.start <= cb.t0 <= last.end):
        errs.append("CLIFF_NOT_AT_END")
    return errs


# ---------------------------------------------------------------- candidate generation
def _note_confirms(show: Show, ep: Episode, b: Beat) -> bool:
    nxt = show.ep(ep.num + 1)
    if not nxt or not nxt.continuity:
        return False
    units = ep.cut_units
    near_end = units and (b.id == units[-1].id or units[-1].t1 - b.t1 <= 6.0)
    return bool(near_end and re.search(r"cliffhanger|picks up (directly|immediately)", nxt.continuity, re.I))


def make_segment(ep: Episode, start: float, end: float) -> Segment:
    units = [u for u in ep.cut_units if u.t0 >= start - 0.01 and u.t0 <= end]
    first = units[0].id if units else ""
    last = max(units, key=lambda u: u.t1).id if units else ""
    return Segment(ep=ep.num, start=round(start, 2), end=round(end, 2), first_beat=first, last_beat=last)


def anchored_candidates(show: Show, anns: dict[int, EpisodeAnnotation], lo: float = MIN_LEN,
                        hi: float = MAX_LEN) -> list[Promo]:
    out: list[Promo] = []
    for ep in show.episodes:
        ann = anns.get(ep.num)
        if not ann:
            continue
        for c_rank, cp in enumerate(ann.cliffhangers):
            a = ep.beat(cp.beat_id)
            if a is None:
                continue
            confirmed = _note_confirms(show, ep, a)
            for h_rank, hp in enumerate(ann.hooks):
                h = ep.beat(hp.beat_id)
                if h is None or h.t0 >= a.t0:
                    continue
                start, end, issues = check_cut(ep, h.t0, a.t1)
                if any(i.startswith("MID_LINE") for i in issues):
                    continue
                dur = end - start
                if not (lo <= dur <= hi):
                    continue
                hook_s = hp.strength / 3 * (1 - 0.05 * h_rank)
                cliff_s = cp.strength / 3 * (1 - 0.05 * c_rank)
                ctx = hp.needs_context / 3
                fit = 1.0 if 25 <= dur <= 45 else (0.8 if dur <= 52 else 0.6)
                fit = fit if (lo, hi) == (MIN_LEN, MAX_LEN) else 1.0
                score = 100 * (0.37 * hook_s + 0.37 * cliff_s - 0.2 * ctx + 0.08 * fit
                               + (0.06 if confirmed else 0) + (0.03 if ann.ending_is_cliffhanger and confirmed else 0))
                out.append(Promo(
                    id="", source="anchored", segments=[make_segment(ep, start, end)], hook_beat=h.id,
                    cliff_beat=a.id, hook_type=hp.type, cliff_type=cp.type, hook_strength=hp.strength,
                    cliff_strength=cp.strength, needs_context=hp.needs_context, open_question=cp.open_question,
                    why=hp.why, moment=a.id, variant="tight" if dur <= TIGHT_MAX else "full",
                    score=round(score, 2), note_confirmed=confirmed,
                    flags=[i for i in issues if i.startswith("END_EXTENDED")],
                ))
    return out


def best_per_moment(cands: list[Promo]) -> list[Promo]:
    """Keep the best 'full' and the best 'tight' cut for each cliffhanger moment."""
    best: dict[tuple[str, str], Promo] = {}
    for p in cands:
        k = (p.moment, p.variant)
        if k not in best or p.score > best[k].score:
            best[k] = p
    return list(best.values())


def assign_ids(promos: list[Promo], prefix: str = "P") -> None:
    for i, p in enumerate(sorted(promos, key=lambda p: (-p.score, p.moment)), 1):
        p.id = f"{prefix}{i:03d}"


def promo_from_dict(d: dict) -> Promo:
    segs = [Segment(ep=s["ep"], start=s["start"], end=s["end"], first_beat=s.get("first_beat", ""),
                    last_beat=s.get("last_beat", "")) for s in d["segments"]]
    keep = {k: v for k, v in d.items() if k in Promo.__dataclass_fields__ and k != "segments"}
    return Promo(segments=segs, **keep)
