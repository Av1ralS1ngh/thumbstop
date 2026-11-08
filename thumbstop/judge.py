"""Head-to-head judge: orders the best promos by reading a few of them side by side, the way an editor chooses
which one to post.

Why: the 0-3 scores saturate at the top (almost every promo near the top is rated 3/3 by the annotator), so the
order of the best 20 was decided by small tie-breakers. Comparing a few promos is an easier judgement than scoring
one, so the judge ranks small groups, every promo meets several others in different positions (to cancel position
bias), and the wins are pooled into one strength per promo (a Bradley-Terry fit).

Status: an experiment, off by default (--judge turns it on). On my 33 blind tasks it agreed with my own pick less
often than the tool's score order did (Opus 4.6 19.5, Sonnet 4.6 18, GPT-OSS 19, against 21), so it did not earn
the job. `python -m thumbstop.evaluate judge labels/blind_answers.csv <profile>` reruns that check.
"""
from __future__ import annotations

import math
import random
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from .llm import LLM, LLMError

JUDGE_VERSION = "h2h-v1"

JUDGE_SYSTEM = """You pick promos for a microdrama app. A promo is a 20 to 60 second clip cut straight from an episode and posted on TikTok, Reels and Shorts to people who have never seen the show. A promo works when:
1. The first 3 seconds stop a stranger who is scrolling: a shock, a threat, a slap, an accusation or a reveal that reads instantly, not calm talk or setup.
2. A stranger can follow it: who these people are to each other and what is at stake are clear from the clip alone. Archetypes are enough (a cruel stepmother, a disguised heir). Names and past events the clip depends on but never explains hurt it.
3. It ends on a cliffhanger: it cuts right before an answer the viewer badly wants (a decision, a reveal, a blow about to land), so they tap to watch the episode. It must not give the payoff away.
4. It keeps moving: no slow stretch where a viewer would swipe away.

You will read a few candidate promos as transcripts: dialogue and visible action, with times in seconds from the start of the clip. Judge only what is on the page. Imagine each one scrolling past on a phone, compare them against each other, and rank them from the one you would post first to the last. Be decisive: no ties. Return JSON only."""


class ClipNote(BaseModel):
    promo: int = Field(description="The promo's number")
    first_3s: str = Field(description="What a scrolling stranger gets in the first 3 seconds, in a few words")
    follow: str = Field(description="Can a stranger follow who is who and what is at stake? A few words")
    ending: str = Field(description="What the viewer is left wanting at the cut, or what spoils it. A few words")


class GroupRanking(BaseModel):
    notes: list[ClipNote]
    ranking: list[int] = Field(description="Every promo number exactly once, the one you would post first leading")
    reason: str = Field(description="One sentence: why your first choice beats your second")


def rank_group(llm: LLM, items: list[tuple[str, float, str]], tag: str = "") -> tuple[list[str], str]:
    """items: (id, seconds, transcript) in the order shown. Returns the ids best first, and the judge's reason."""
    user = "\n\n".join(f"Promo {i + 1} ({dur:.0f} s)\n{text}" for i, (_, dur, text) in enumerate(items))
    user += f"\n\nRank these {len(items)} promos, best first."
    r = llm.json(JUDGE_SYSTEM, user, GroupRanking, max_tokens=2000, prompt_version=JUDGE_VERSION, tag=tag)
    order: list[str] = []
    for n in r.ranking:
        if 1 <= n <= len(items) and items[n - 1][0] not in order:
            order.append(items[n - 1][0])
    for it in items:                       # anything the judge left out goes last, in the order shown
        if it[0] not in order:
            order.append(it[0])
    return order, r.reason.strip()


def groups_for(ids: list[str], rounds: int = 3, size: int = 5, seed: int = 7) -> list[list[str]]:
    """Each round shuffles everyone into groups of about `size`, so a promo meets different rivals in different
    positions each round. Deterministic for a given list, so a re-run hits the cache."""
    rng = random.Random(seed)
    out = []
    n_groups = max(1, round(len(ids) / size))
    for _ in range(rounds):
        xs = ids[:]
        rng.shuffle(xs)
        out += [g for g in (xs[i::n_groups] for i in range(n_groups)) if len(g) >= 2]
    return out


def bradley_terry(ids: list[str], wins: dict[tuple[str, str], float], iters: int = 200) -> dict[str, float]:
    """Strength per promo from pairwise wins (a beats b counted in wins[(a, b)]), with a small prior so a promo
    that won every comparison still gets a finite strength. Returned on a log scale, centred on 0."""
    s = {i: 1.0 for i in ids}
    games: dict[str, dict[str, float]] = {i: {} for i in ids}
    for (a, b), w in wins.items():
        games[a][b] = games[a].get(b, 0.0) + w
        games[b][a] = games[b].get(a, 0.0)          # make sure both directions exist
    prior = 0.5                                       # half a win and half a loss against an average rival
    for _ in range(iters):
        new = {}
        for i in ids:
            w_i = sum(games[i].values()) + prior
            denom = sum((games[i].get(j, 0.0) + games[j].get(i, 0.0)) / (s[i] + s[j]) for j in games[i]) + 2 * prior / (s[i] + 1.0)
            new[i] = w_i / denom if denom > 0 else s[i]
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        s = {i: v / g for i, v in new.items()}
    return {i: round(math.log(v), 3) for i, v in s.items()}


def tournament(llm: LLM, items: dict[str, tuple[float, str]], rounds: int = 3, size: int = 5, log=print) -> dict:
    """items: id -> (seconds, transcript). Returns {"strength", "wins", "games", "reasons", "calls", "failed"}."""
    ids = sorted(items)
    gs = groups_for(ids, rounds, size)
    wins: dict[tuple[str, str], float] = {}
    won = {i: 0 for i in ids}
    played = {i: 0 for i in ids}
    reasons: dict[str, str] = {}
    failed = 0

    def one(k_g):
        k, g = k_g
        try:
            return g, rank_group(llm, [(i, *items[i]) for i in g], tag=f"h2h:{k}")
        except LLMError as e:
            return g, e

    with ThreadPoolExecutor(max_workers=max(1, min(6, llm.p.max_concurrency))) as ex:
        for g, res in ex.map(one, enumerate(gs)):
            if isinstance(res, Exception):
                failed += 1
                log(f"  head-to-head group failed: {str(res)[:120]}")
                continue
            order, reason = res
            if order and order[0] not in reasons:
                reasons[order[0]] = reason
            for x in range(len(order)):
                for y in range(x + 1, len(order)):
                    a, b = order[x], order[y]
                    wins[(a, b)] = wins.get((a, b), 0.0) + 1
                    won[a] += 1
                    played[a] += 1
                    played[b] += 1
    return {"strength": bradley_terry(ids, wins), "wins": won, "games": played, "reasons": reasons,
            "calls": len(gs), "failed": failed}
