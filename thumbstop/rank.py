"""Ranking (code only): quality score, diversity, posting order. Ad-policy is a separate axis that never
changes the quality score - it only decides what a human must check before posting."""
from __future__ import annotations

import re

from .models import Show
from .search import Promo

# HOLD = do not post without human sign-off. Evidence must be a verbatim line from the clip.
POLICY_RULES = [
    ("HOLD", "nudity_sexual", re.compile(r"\b(naked|nude|undress\w*|topless|stripp\w*|lingerie|bath(ing)?\b|"
                                         r"bare (chest|back|skin)|in bed|bedchamber|seduc\w*|moan\w*|kiss\w* (her|his) neck|"
                                         r"passion\w*|intimacy)\b", re.I)),
    ("HOLD", "gore_torture", re.compile(r"\b(torture\w*|branding iron|red-hot|glowing iron|burn\w* (skin|flesh)|"
                                        r"bloodied|blood (pours|spurts|pools)|mutilat\w*|sever\w*|gore|stab\w*|"
                                        r"slit\w* (her|his)? ?throat|impal\w*)\b", re.I)),
    ("HOLD", "weapon_at_camera", re.compile(r"\b(points?|aims?) (a |the )?(gun|pistol|musket|sword|dagger|blade|knife)\b"
                                            r".{0,40}\b(camera|lens|at us)\b", re.I)),
    ("CHECK", "violence", re.compile(r"\b(slap\w*|punch\w*|shove\w*|whip\w*|beat(s|ing)?|strikes?|kick\w*|choke\w*|"
                                     r"blood|wound\w*|gallows|hang\w*|execut\w*|poison\w*|explo\w*|fire|flames|club|cudgel|sword|dagger|knife|"
                                     r"blade|gun|pistol|musket|weapon\w*|attack\w*|fight\w*|crush\w*|brawl\w*)\b", re.I)),
    ("CHECK", "minor_present", re.compile(r"\b(child|children|little (boy|girl|one)|kid|toddler|baby|infant)\b", re.I)),
]


def policy_status(show: Show, p: Promo) -> dict:
    hits = []
    for s in p.segments:
        ep = show.ep(s.ep)
        for b in ep.beats:
            if b.kind in ("dialogue", "action", "camera") and s.start - 0.01 <= b.t0 < s.end:
                for level, rule, rx in POLICY_RULES:
                    if rx.search(b.text):
                        hits.append({"level": level, "rule": rule, "beat": b.id, "evidence": b.text[:140]})
    levels = {h["level"] for h in hits}
    status = "HOLD" if "HOLD" in levels else ("CHECK" if "CHECK" in levels else "CLEAR")
    minor_and_violence = {"violence", "minor_present"} <= {h["rule"] for h in hits}
    if minor_and_violence:
        status = "HOLD"
    seen, ev = set(), []
    for h in hits:
        if (h["rule"], h["beat"]) not in seen:
            seen.add((h["rule"], h["beat"]))
            ev.append(h)
    ev.sort(key=lambda h: h["level"] != "HOLD")  # stable: HOLD reasons survive the cap below
    return {"status": status, "evidence": ev[:6],
            "note": "Text-only check; final frames decide platform approval. Applies to Meta, TikTok and YouTube ads."}


def spoiler_note(show: Show, p: Promo) -> str:
    # the whole show's length, not just the episodes loaded for this run (--episodes 8-10 is not "3 episodes")
    n = show.total_episodes or max((e.num for e in show.episodes), default=0)
    if n < 5:                      # a 1 to 4 episode show has no "late season" to spoil
        return ""
    ep = max(p.episodes)
    if ep > 0.6 * n:
        return f"late-season clip (ep {ep} of {n}): editor should check it does not give away a later twist"
    return ""


def final_score(p: Promo) -> float:
    """Search score, adjusted by the cold viewer's verdict (a gate + tie-break, never the main driver)."""
    g = p.gate or {}
    s = p.score
    if g.get("passed") is False or g.get("unchecked"):
        s -= 25
    if g.get("passed"):
        s += 2 * (g.get("standalone_score", 0) + g.get("hook_score", 0) + g.get("cliff_score", 0)) / 3
    if "END_EXTENDED_TO" in " ".join(p.flags):
        s -= 2
    return round(s, 2)


def _overlap(a: Promo, b: Promo) -> float:
    inter = 0.0
    for x in a.segments:
        for y in b.segments:
            if x.ep == y.ep:
                inter += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def select(promos: list[Promo], top: int = 20, per_episode: int = 2,
           lead: list[Promo] | None = None) -> tuple[list[Promo], dict[str, list[Promo]]]:
    """Greedy diverse selection by final score. Promos in `lead` (already in order, from the head-to-head judge)
    come first and the rest follow by final score. Returns (ranked list, variants keyed by primary id)."""
    first = {id(p) for p in lead or []}
    pool = list(lead or []) + sorted((p for p in promos if id(p) not in first), key=lambda p: -final_score(p))
    for p in pool:                 # only promos chosen in THIS selection carry a rank
        p.rank, p.rank_reason = 0, ""
    chosen: list[Promo] = []
    variants: dict[str, list[Promo]] = {}
    by_moment: dict[str, Promo] = {}
    for p in pool:
        if p.moment in by_moment:
            variants.setdefault(by_moment[p.moment].id, []).append(p)
            continue
        if len(chosen) >= top:
            continue
        if sum(1 for c in chosen if c.episodes[-1] == p.episodes[-1]) >= per_episode:   # where the cliffhanger is
            continue
        if any(_overlap(p, c) > 0.5 for c in chosen):
            continue
        chosen.append(p)
        by_moment[p.moment] = p
    for i, p in enumerate(chosen, 1):
        p.rank = i
    for a, b in zip(chosen, chosen[1:]):
        a.rank_reason = _why_above(a, b)
    if chosen:
        chosen[-1].rank_reason = chosen[-1].rank_reason or "last of the shortlist"
    return chosen, variants


def _why_above(a: Promo, b: Promo) -> str:
    ja = a.judge or {}
    if ja.get("of") and (b.judge or {}).get("of"):
        return f"ranked above #{b.rank} by the side-by-side judge (won {ja['won']} of {ja['of']} comparisons)"
    diffs = []
    if a.hook_strength > b.hook_strength:
        diffs.append("stronger opening")
    if a.cliff_strength > b.cliff_strength:
        diffs.append("sharper cliffhanger")
    if a.needs_context < b.needs_context:
        diffs.append("needs less backstory")
    ga, gb = a.gate or {}, b.gate or {}
    if ga.get("standalone_score", 0) > gb.get("standalone_score", 0):
        diffs.append("cold viewer followed it better")
    if a.note_confirmed and not b.note_confirmed:
        diffs.append("ends on the writer's own episode cliffhanger")
    return ("ranked above #%d: " % b.rank + ", ".join(diffs[:2])) if diffs else f"ranked above #{b.rank}: higher overall score"


HOOK_PHRASE = {"humiliation": "a public humiliation", "physical_aggression": "a physical attack",
               "identity_reveal": "a reveal", "threat_ultimatum": "a threat", "betrayal_caught": "a betrayal caught in the act",
               "accusation_frameup": "an accusation", "proposal_or_contract": "a proposal", "power_reversal": "a power reversal",
               "rescue": "a rescue", "shocking_confession": "a shocking line", "danger": "a moment of danger",
               "other": "a strong moment"}


def post_first(chosen: list[Promo], n: int = 3, early_ep: int = 15) -> list[tuple[Promo, str]]:
    """Which to post first: spoiler-safe, early-funnel, not HOLD, distinct hook types."""
    picks, used_types = [], set()
    def ok(p, strict):
        # strict 2: early episode + new hook type; 1: new hook type only; 0: anything safe
        if p.policy.get("status") == "HOLD" or p.spoiler:
            return False
        if strict >= 2 and p.episodes[0] > early_ep:
            return False
        if strict >= 1 and p.hook_type in used_types:
            return False
        return (p.gate or {}).get("passed") is not False
    for strict in (2, 1, 0):
        for p in chosen:
            if len(picks) >= n:
                break
            if p in [x for x, _ in picks] or not ok(p, strict):
                continue
            used_types.add(p.hook_type)
            reasons = []
            if p.episodes[0] <= early_ep:
                reasons.append(f"from episode {p.episodes[0]}, so a new viewer reaches the payoff within the free early episodes")
            reasons.append(f"opens on {HOOK_PHRASE.get(p.hook_type, 'a strong moment')}"
                           + (f" and leaves the question \"{p.open_question}\" open" if p.open_question else " and ends unresolved"))
            if p.policy.get("status") == "CLEAR":
                reasons.append("no ad-policy flags")
            picks.append((p, "; ".join(reasons)))
    return picks
