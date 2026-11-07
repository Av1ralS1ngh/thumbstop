"""Cold-viewer gate (a different model family, clip text only) and the setup-repair agent loop."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from .annotate import EpisodeAnnotation
from .llm import LLM, LLMError
from .models import Beat, Show, fmt_ts
from .search import MAX_LEN, Promo, check_cut, make_segment, validate

GATE_VERSION = "gate-v2"
REPAIR_VERSION = "repair-v1"
NAME_STOP = {"LADY", "LORD", "THE", "SIR", "MISS", "MR", "MRS", "OF", "AND", "YOUNG", "OLD", "ELDER", "HIGH"}


class GateVerdict(BaseModel):
    premise: str = Field(description="One sentence: what is going on, as a stranger understands it")
    open_question: str = Field(description="The question the clip leaves you wanting answered; empty if none")
    unexplained: list[str] = Field(description="Short quotes of names/references/events you could not follow")
    hook_in_3s: bool = Field(description="Would the first ~3 seconds stop you scrolling?")
    ends_unresolved: bool = Field(description="Does it cut at an unresolved moment?")
    stands_alone: bool = Field(description="Can you follow it without having seen anything else?")
    would_keep_watching: bool
    hook_score: int = Field(description="0-3")
    cliff_score: int = Field(description="0-3")
    standalone_score: int = Field(description="0-3")
    gives_away_payoff: bool = Field(description="Does the clip already answer its own open question?")


GATE_SYSTEM = """You are a viewer scrolling TikTok who has NEVER seen this show. You are shown the transcript of a short clip (dialogue plus what is visibly happening, with times in seconds from the start). You know nothing else - judge only what is in the clip. You are a tough critic: most clips are ordinary, and a 3 is rare.

hook_score - the first 3 seconds only:
 3 = a shock you cannot scroll past (a slap, a public betrayal line, a threat, a reveal) that reads instantly
 2 = clear conflict starts immediately but is not shocking
 1 = it takes a few lines to get going, or it opens on setup/explanation
 0 = opens on calm talk, logistics, or something meaningless without context
cliff_score - the last moment:
 3 = cuts right before an answer you badly want (a decision, a reveal, an attack about to land)
 2 = tension is left open but the question is vague
 1 = mostly resolved, or ends on a reaction with nothing pending
 0 = ends on a resolution, a goodbye, or the answer itself
standalone_score - could you tell a friend what is going on?
 3 = who these people are to each other and what is at stake are clear from the clip alone (archetypes are enough)
 2 = the gist is clear; one or two details are fuzzy
 1 = you follow the emotion but not the situation
 0 = you are lost
unexplained: list ONLY things that block understanding (an unknown person or past event the clip relies on, an object whose importance is never shown). Do not list lines that are clear in context. Empty list if nothing blocks you.
gives_away_payoff: true only if the clip itself answers the question it raises."""


def clip_text(show: Show, anns: dict[int, EpisodeAnnotation], p: Promo, anonymize: bool = True) -> str:
    gender_map, counters, words, label_alias, name_alias = {}, {"F": 0, "M": 0, "?": 0}, \
        {"F": "WOMAN", "M": "MAN", "?": "PERSON"}, {}, {}
    for s in p.segments:
        for r in (anns[s.ep].roles if s.ep in anns else []):
            gender_map.setdefault(r.label, r.gender)
    beats: list[tuple[int, Beat]] = []
    for si, s in enumerate(p.segments):
        ep = show.ep(s.ep)
        for b in sorted(ep.beats, key=lambda b: (b.t0, b.idx)):
            if b.kind in ("dialogue", "action") and s.start - 0.01 <= b.t0 < s.end:
                beats.append((si, b))
    spoken = " ".join(b.text for _, b in beats if b.kind == "dialogue").lower()

    def alias(label: str) -> str:
        if not anonymize:
            return label
        if label not in label_alias:
            g = gender_map.get(label, "?")
            counters[g] += 1
            label_alias[label] = f"{words[g]} {counters[g]}"
            for tok in re.findall(r"[A-Z][A-Z'\-]+", label):
                if tok not in NAME_STOP and len(tok) > 2 and tok.lower() not in spoken:
                    name_alias[tok.title()] = label_alias[label]
        return label_alias[label]

    lines, cur_seg, t_off = [], -1, 0.0
    for si, b in beats:
        if si != cur_seg:
            if cur_seg != -1:
                t_off += p.segments[cur_seg].dur
                lines.append("[CUT TO]")
            cur_seg = si
        t = t_off + (b.t0 - p.segments[si].start)
        if b.kind == "dialogue":
            lines.append(f"[{t:4.1f}s] {alias(b.speaker or '?')}: \"{b.text}\"")
        else:
            lines.append(f"[{t:4.1f}s] (visible) {b.text}")
    text = "\n".join(lines)
    if anonymize:
        for name, al in name_alias.items():
            text = re.sub(rf"\b{re.escape(name)}('s)?\b", lambda m: al.lower() + (m.group(1) or ""), text)
    return text


INTENT_VERSION = "intent-v1"
DRIFT_BELOW = 2   # on the blind-test controls this flagged 17/24 clips with a cut-off ending and 8/25 real clips


class IntentMatch(BaseModel):
    reason: str = Field(description="One short sentence")
    match: int = Field(description="3 same central question; 2 same situation, different emphasis; "
                                   "1 related but a different question; 0 unrelated")


INTENT_SYSTEM = ("You compare two questions about the same short drama clip. Question A is what the writer intends a "
                 "viewer to be wondering when the clip ends. Question B is what a first-time viewer actually wondered "
                 "after watching it. Unnamed characters may be written as WOMAN 1, MAN 2 and so on, and may be the same "
                 "people as named ones in A. Rate how closely B is the same open question as A. Return JSON only.")


def intent_match(llm: LLM, intended: str, stranger: str, tag: str = "") -> dict | None:
    """Does a stranger leave with the question the writer meant? The cold viewer cannot tell when a cliffhanger
    was cut off, but its question drifts when that happens, so this is an editor warning (never a filter)."""
    if llm.p.provider == "rules" or not (intended or "").strip() or not (stranger or "").strip():
        return None
    m = llm.json(INTENT_SYSTEM, f"A: {intended}\nB: {stranger}", IntentMatch, max_tokens=600,
                 prompt_version=INTENT_VERSION, tag=tag + ":intent")
    return {"intent_match": max(0, min(3, m.match)), "intent_note": m.reason}


def drift_flag(gate: dict | None) -> str | None:
    m = (gate or {}).get("intent_match")
    if m is not None and m < DRIFT_BELOW:
        return (f"QUESTION_DRIFT: a stranger leaves asking something other than the intended question ({m}/3), "
                "so check the ending lands and the setup is clear")
    return None


def gate_one(show: Show, anns: dict[int, EpisodeAnnotation], p: Promo, llm: LLM) -> dict:
    if llm.p.provider == "rules":
        ok = p.needs_context <= 1
        return {"premise": "", "open_question": p.open_question, "unexplained": [], "hook_in_3s": p.hook_strength >= 2,
                "ends_unresolved": True, "stands_alone": ok, "would_keep_watching": ok, "hook_score": p.hook_strength,
                "cliff_score": p.cliff_strength, "standalone_score": 3 - p.needs_context, "gives_away_payoff": False,
                "passed": ok, "judge": "rules"}
    user = f"CLIP ({p.duration:.0f} seconds):\n" + clip_text(show, anns, p)
    v = llm.json(GATE_SYSTEM, user, GateVerdict, max_tokens=3000, prompt_version=GATE_VERSION, tag=p.id)
    d = v.model_dump()
    for k in ("hook_score", "cliff_score", "standalone_score"):
        d[k] = min(3, max(0, d[k]))
    d["passed"] = bool(d["hook_score"] >= 2 and d["cliff_score"] >= 2 and d["standalone_score"] >= 2
                       and not v.gives_away_payoff and v.open_question.strip())
    d["judge"] = llm.p.model
    try:
        d.update(intent_match(llm, p.open_question, d.get("open_question", ""), tag=p.id) or {})
    except LLMError:
        pass   # a missing warning is fine; never fail the gate over it
    return d


def gate_all(show: Show, anns, promos: list[Promo], llm: LLM, log=print) -> None:
    workers = 1 if llm.p.provider == "rules" else max(1, llm.p.max_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(gate_one, show, anns, p, llm): p for p in promos}
        for fut, p in futs.items():
            try:
                p.gate = fut.result()
            except LLMError as e:
                log(f"  gate failed for {p.id}: {e}")
                p.gate = {"passed": None, "error": str(e)[:200]}


# ---------------------------------------------------------------- setup-repair agent loop
class RepairChoice(BaseModel):
    choice: int = Field(description="Index of the setup option that best explains the unexplained references, "
                                    "or -1 if none of them helps")
    reason: str = Field(description="Under 20 words")


REPAIR_SYSTEM = """You fix promo clips for a microdrama. A cold viewer watched a clip and could not follow some references. You are given the clip, what confused the viewer, and a few short earlier moments from the show that could be shown BEFORE the clip as a 2-8 second setup.

Pick the ONE option that best makes the clip understandable to a stranger without giving away the clip's ending. Answer -1 if no option clearly helps - an irrelevant setup makes the promo worse."""


def _setup_options(show: Show, p: Promo, refs: list[str], k: int = 5) -> list[tuple[Beat, float, float]]:
    first = p.segments[0]
    ep = show.ep(first.ep)
    pool: list[Beat] = [b for b in ep.cut_units if b.t1 <= first.start - 0.5]
    prev = show.ep(first.ep - 1)
    if prev:
        pool += [b for b in prev.cut_units if b.t0 >= prev.duration - 60]
    query = " ".join(refs)
    scored = []
    for b in pool:
        sc = fuzz.token_set_ratio(query.lower(), b.text.lower())
        if b.kind == "dialogue" and len(b.text.split()) >= 3:
            scored.append((sc, b))
    scored.sort(key=lambda x: -x[0])
    opts = []
    for sc, b in scored[: k * 2]:
        e = show.ep(b.ep)
        s0, s1, issues = check_cut(e, b.t0, b.t1)
        if any(i.startswith("MID_LINE") for i in issues) or not (1.5 <= s1 - s0 <= 8.0):
            continue
        opts.append((b, s0, s1))
        if len(opts) >= k:
            break
    return opts


def repair_one(show: Show, anns, p: Promo, repair_llm: LLM, gate_llm: LLM, max_rounds: int = 2) -> Promo | None:
    """Agent loop: gate says 'lost' -> code finds candidate setups -> model picks one -> code builds + validates
    -> gate re-checks. Stops when the gate passes, the model declines, or rounds run out."""
    cur, log = p, []
    for rnd in range(max_rounds):
        refs = (cur.gate or {}).get("unexplained") or []
        if not refs or (cur.gate or {}).get("passed"):
            break
        opts = _setup_options(show, cur, refs)
        if not opts:
            log.append({"round": rnd, "action": "no_options"})
            break
        menu = "\n".join(f"[{i}] (E{b.ep} {fmt_ts(s0)}) {b.speaker or 'ACTION'}: \"{b.text}\""
                         for i, (b, s0, s1) in enumerate(opts))
        user = (f"CLIP:\n{clip_text(show, anns, cur, anonymize=False)}\n\nTHE VIEWER COULD NOT FOLLOW: {refs}\n\n"
                f"SETUP OPTIONS:\n{menu}")
        choice = repair_llm.json(REPAIR_SYSTEM, user, RepairChoice, max_tokens=1500, prompt_version=REPAIR_VERSION,
                                 tag=f"{p.id}-r{rnd}")
        if choice.choice < 0 or choice.choice >= len(opts):
            log.append({"round": rnd, "action": "declined", "reason": choice.reason})
            break
        b, s0, s1 = opts[choice.choice]
        setup = make_segment(show.ep(b.ep), s0, s1)
        main = cur.segments[-1]
        earlier = cur.segments[:-1]            # keep setups added in earlier rounds
        if len(earlier) + 2 > 3:
            log.append({"round": rnd, "action": "segment_limit"})
            break
        budget = MAX_LEN - setup.dur - sum(s.dur for s in earlier)
        if main.dur > budget:  # tighten the main segment from the front, keeping the cliffhanger end
            ep = show.ep(main.ep)
            later = [u for u in ep.cut_units if u.t0 >= main.end - budget and u.t0 < main.end - 15]
            if not later:
                log.append({"round": rnd, "action": "no_room"})
                break
            main = make_segment(ep, later[0].t0, main.end)
        story = sorted(earlier + [setup], key=lambda s: (s.ep, s.start))
        if not all(s.ep < main.ep or s.end <= main.start for s in story):
            log.append({"round": rnd, "action": "setup_after_main"})
            break
        segs = story + [main]
        # A setup may open the promo only if it is itself a strong hook beat; otherwise the original
        # hook must still land within 3 s (validator enforces), so only very short setups survive.
        opener = show.beat(segs[0].first_beat)
        opener_is_hook = bool(opener) and opener.ep in anns and any(
            h.beat_id == opener.id and h.strength >= 2 for h in anns[opener.ep].hooks)
        cand = replace(cur, id=p.id + "R", source="repaired", segments=segs,
                       hook_beat=opener.id if opener_is_hook else cur.hook_beat)
        errs = validate(show, cand)
        if errs:
            log.append({"round": rnd, "action": "invalid", "errors": errs})
            break
        cand.gate = gate_one(show, anns, cand, gate_llm)
        log.append({"round": rnd, "action": "added_setup", "beat": b.id, "reason": choice.reason,
                    "passed": cand.gate.get("passed")})
        cur = cand
    if cur is p:
        p.repair = {"attempted": True, "log": log, "result": "unchanged"}
        return None
    cur.repair = {"attempted": True, "log": log, "from": p.id,
                  "result": "fixed" if (cur.gate or {}).get("passed") else "still_failing"}
    return cur
