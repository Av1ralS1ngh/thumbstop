"""Baselines the tool must beat, built with the same validator so the comparison is fair:
- last45 / first45: naive windows; last45_at(): last-45s ending at a given cut (paired test)
- proposed: the field-standard design - the model proposes whole promos as beat-ID ranges."""
from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from .annotate import render_episode
from .llm import LLM, LLMError
from .models import Episode, Show
from .search import MAX_LEN, MIN_LEN, Promo, check_cut, make_segment, validate

PROPOSE_VERSION = "propose-v1"


def _window(ep: Episode, start_t: float, end_t: float, source: str) -> Promo | None:
    units = ep.cut_units
    starts = [u for u in units if u.t0 >= start_t - 0.01]
    if not starts:
        return None
    first = starts[0]
    ends = [u for u in units if u.t1 <= end_t + 0.01 and u.t0 >= first.t0]
    if not ends:
        return None
    last = max(ends, key=lambda u: u.t1)
    s0, s1, _ = check_cut(ep, first.t0, last.t1)
    if not (MIN_LEN <= s1 - s0 <= MAX_LEN):
        return None
    return Promo(id="", source=source, segments=[make_segment(ep, s0, s1)], hook_beat=first.id, cliff_beat=last.id,
                 moment=last.id)


def last45(ep: Episode) -> Promo | None:
    units = ep.cut_units
    if not units:
        return None
    end = max(u.t1 for u in units)
    return _window(ep, end - 45, end, "last45")


def first45(ep: Episode) -> Promo | None:
    units = ep.cut_units
    if not units:
        return None
    return _window(ep, units[0].t0, units[0].t0 + 45, "first45")


def last45_at(show: Show, p: Promo) -> Promo | None:
    """Same ending as promo p, but the start is simply 45 s earlier (the naive opening)."""
    last = p.segments[-1]
    ep = show.ep(last.ep)
    q = _window(ep, last.end - 45, last.end, "last45_same_end")
    if q:
        q.cliff_beat, q.moment = p.cliff_beat, p.moment
    return q


def random_windows(show: Show, n: int, seed: int = 7) -> list[Promo]:
    rng, out = random.Random(seed), []
    eps = [e for e in show.episodes if e.duration > 30]
    tries = 0
    while len(out) < n and tries < n * 20:
        tries += 1
        ep = rng.choice(eps)
        L = rng.uniform(25, 45)
        t = rng.uniform(0, max(0.1, ep.duration - L))
        p = _window(ep, t, t + L, "random")
        if p:
            out.append(p)
    return out


class Proposal(BaseModel):
    start_beat: str = Field(description="ID of the first beat of the promo")
    end_beat: str = Field(description="ID of the last beat of the promo (the cliffhanger)")
    why: str = Field(description="Under 20 words")


class Proposals(BaseModel):
    promos: list[Proposal] = Field(description="1 to 3 promos, best first")


PROPOSE_SYSTEM = """You are the promo editor for a vertical microdrama app. From this ONE episode, propose 1-3 promos: continuous 20-60 second clips posted on TikTok/Reels to win new viewers. A good promo opens on a hook in the first 3 seconds (a slap, a reveal, a shocking line), ends on a cliffhanger (cut at an unresolved moment), and makes sense to someone who has never seen the show. Give each promo as a start beat ID and an end beat ID from the episode text (the clip runs from the start of the first beat to the end of the last). Use only IDs shown."""


def proposed(show: Show, llm: LLM, log=print) -> list[Promo]:
    """Field-standard design, run through the same validator."""
    out: list[Promo] = []

    def one(ep: Episode) -> list[Promo]:
        res = llm.json(PROPOSE_SYSTEM, render_episode(ep), Proposals, max_tokens=3000,
                       prompt_version=PROPOSE_VERSION, tag=f"E{ep.num:02d}")
        ps = []
        for pr in res.promos[:3]:
            a, b = ep.beat(pr.start_beat), ep.beat(pr.end_beat)
            if not a or not b or b.t1 <= a.t0:
                continue
            s0, s1, _ = check_cut(ep, a.t0, b.t1)
            p = Promo(id="", source="proposed", segments=[make_segment(ep, s0, s1)], hook_beat=a.id,
                      cliff_beat=b.id, moment=b.id, why=pr.why)
            p.flags = validate(show, p)
            ps.append(p)
        return ps

    if llm.p.provider == "rules":
        return out
    with ThreadPoolExecutor(max_workers=max(1, llm.p.max_concurrency)) as pool:
        for ep, fut in [(ep, pool.submit(one, ep)) for ep in show.episodes]:
            try:
                out += fut.result()
            except LLMError as e:
                log(f"  propose E{ep.num:02d} failed: {e}")
    return out
