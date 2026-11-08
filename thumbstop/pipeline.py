"""The orchestrator. Code fixes the order of stages and owns every limit; models are called only at
annotate, gate and repair. Re-runs are free: every model call is cached by content."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .annotate import EpisodeAnnotation, annotate_show
from .export import write_all
from .gate import clip_text, drift_flag, gate_all, repair_one
from .judge import tournament
from .llm import BudgetExceeded, LLMError, Router
from .parse import ingest_report, parse_show
from .rank import final_score, policy_status, post_first, select, spoiler_note
from .baselines import proposed
from .search import CONTEXT_MIN_LEN, CONTEXT_TIGHT_PENALTY, MAX_LEN, MIN_LEN, Promo, anchored_candidates, assign_ids, best_per_moment, validate


@dataclass
class Options:
    top: int = 20
    gate_n: int = 40              # how many shortlisted promos the cold viewer checks
    repair: bool = True
    gate: bool = True
    episodes: tuple[int, int] | None = None
    min_len: float = MIN_LEN
    max_len: float = MAX_LEN
    exclude_policy: list[str] = field(default_factory=list)   # e.g. ["violence"] for a "no violence" what-if
    hook_types: list[str] = field(default_factory=list)
    early_ep: int = 15
    no_notes: bool = False         # ablation: ignore the writers' continuity notes
    seeds: bool = True             # also let the model propose whole promos (validated + judged like the rest)
    judge: bool = False            # experiment, off: order the shortlist by side-by-side comparisons (judge.py).
                                   # On my 33 blind picks it agreed with me less often than the score order.
    judge_extra: int = 10          # the judge compares the top `top + judge_extra` diverse promos
    judge_rounds: int = 4
    judge_size: int = 4


def run(folder: str | Path, out: Path, router: Router, opt: Options, log=print) -> dict:
    t_start = time.time()
    out.mkdir(parents=True, exist_ok=True)
    show = parse_show(folder)
    show.total_episodes = max((e.num for e in show.episodes), default=0)
    if opt.episodes:
        a, b = opt.episodes
        show.episodes = [e for e in show.episodes if a <= e.num <= b]
        if not show.episodes:
            raise SystemExit(f"no episodes in range {a}-{b}")
    if opt.no_notes:
        for e in show.episodes:
            e.continuity = ""
    rep = ingest_report(show)
    log(rep)
    log(f"Models: {router.describe()}")
    funnel: dict[str, int] = {}

    # 1. annotate (AI): sparse ranked hook / cliffhanger beats per episode
    log("[1/5] annotating episodes ...")
    anns: dict[int, EpisodeAnnotation] = annotate_show(show, router["annotate"], log=log)
    (out / "annotations.json").write_text(json.dumps({k: v.model_dump() for k, v in anns.items()}, indent=1), encoding="utf-8")
    funnel["anchors"] = sum(len(a.cliffhangers) for a in anns.values())
    funnel["hook_beats"] = sum(len(a.hooks) for a in anns.values())

    # 2. search (code): writer's ending fixed, standalone hook-first opening searched
    log("[2/5] anchored search ...")
    cands = anchored_candidates(show, anns, opt.min_len, opt.max_len)
    funnel["raw_candidates"] = len(cands)
    if opt.seeds and router["propose"].p.provider != "rules":
        log("  + model-proposed whole promos (seeds) ...")
        seeds = seed_candidates(show, anns, router["propose"], opt, log)
        funnel["seed_candidates"] = len(seeds)
        # A proposal that opens or ends on a moment the annotator did not rank used to score as if both were weak,
        # so the model's own whole-promo ideas could never compete (the ep 34 courtroom opening in the blind test).
        # Now the cold viewer, a different model family, rates those moments instead; it is a tough critic, so
        # only proposals it rates 3/3 reach the top of the list.
        unranked = [p for p in seeds if any(getattr(p, "_unranked", (False, False)))]
        if opt.gate and unranked and router["gate"].p.provider != "rules":
            log(f"  second opinion from the cold viewer on {len(unranked)} proposals the annotator did not rank ...")
            gate_all(show, anns, unranked, router["gate"], log=log)
            funnel["second_opinion"] = len(unranked)
            funnel["second_opinion_strong"] = rescore_from_second_opinion(unranked)
        cands += seeds
    pool = best_per_moment(cands)
    assign_ids(pool)
    for p in pool:
        p.policy = policy_status(show, p)
        p.spoiler = spoiler_note(show, p)
    if opt.exclude_policy:
        pool = [p for p in pool if not any(e["rule"] in opt.exclude_policy for e in p.policy.get("evidence", []))
                and p.policy.get("status") != "HOLD"]
    if opt.hook_types:
        pool = [p for p in pool if p.hook_type in opt.hook_types]
    bad = [(p.id, validate(show, p, opt.min_len, opt.max_len)) for p in pool]
    bad = [b for b in bad if b[1]]
    funnel["valid_candidates"] = len(pool) - len(bad)
    if bad:
        log(f"  {len(bad)} candidates failed validation and were dropped: {bad[:3]}")
        drop = {b[0] for b in bad}
        pool = [p for p in pool if p.id not in drop]

    # 3. cold-viewer gate (AI, different family) on a diverse shortlist
    short, _ = select(pool, top=opt.gate_n, per_episode=3)
    funnel["shortlisted"] = len(short)
    if opt.gate and short:
        log(f"[3/5] cold-viewer check on {len(short)} promos ({router['gate'].p.model}) ...")
        gate_all(show, anns, short, router["gate"], log=log)
        funnel["gate_passed"] = sum(1 for p in short if (p.gate or {}).get("passed"))

    # 4. setup-repair agent loop for promos the cold viewer could not follow
    repaired: list[Promo] = []
    if opt.gate and opt.repair:
        fails = [p for p in short if (p.gate or {}).get("passed") is False and (p.gate or {}).get("unexplained")]
        log(f"[4/5] setup-repair on {len(fails)} promos the cold viewer could not follow ...")
        def _rep(p):
            try:
                return repair_one(show, anns, p, router["repair"], router["gate"], lo=opt.min_len, hi=opt.max_len)
            except (LLMError, BudgetExceeded) as e:
                p.repair = {"attempted": True, "error": str(e)[:200]}
                return None
        with ThreadPoolExecutor(max_workers=4) as ex:
            for r in ex.map(_rep, fails):
                if r is not None:
                    r.policy = policy_status(show, r)
                    r.spoiler = spoiler_note(show, r)
                    repaired.append(r)
        funnel["repair_attempted"] = len(fails)
        funnel["repair_fixed"] = sum(1 for r in repaired if (r.repair or {}).get("result") == "fixed")

    # 5. rank (code) + post-first picks
    log("[5/5] ranking ...")
    final_pool = short + [r for r in repaired if (r.gate or {}).get("passed")]
    for p in final_pool:   # editor warning only; ranking ignores it
        f = drift_flag(p.gate)
        if f and f not in p.flags:
            p.flags.append(f)
    funnel["question_drift"] = sum(1 for p in final_pool if drift_flag(p.gate))
    rank_pool = final_pool + [p for p in pool if p not in short]
    ranked, variants = select(rank_pool, top=opt.top, per_episode=2)
    late = 0
    for _ in range(4 if opt.gate else 0):   # every promo the editor sees gets a cold-viewer verdict
        unchecked = [p for p in ranked if not p.gate]
        if not unchecked:
            break
        log(f"  cold-viewer check on {len(unchecked)} promos that moved into the top {opt.top} ...")
        gate_all(show, anns, unchecked, router["gate"], log=log)
        late += len(unchecked)
        for p in unchecked:
            f = drift_flag(p.gate)
            if f and f not in p.flags:
                p.flags.append(f)
        ranked, variants = select(rank_pool, top=opt.top, per_episode=2)
    if opt.gate and any(not p.gate for p in ranked):
        # still unseen after the late checks: from here on it ranks like a failed check (never above a checked one)
        for p in rank_pool:
            if not p.gate:
                p.gate = {"passed": None, "unchecked": True}
        ranked, variants = select(rank_pool, top=opt.top, per_episode=2)
    if late:
        funnel["late_checked"] = late
    if opt.judge and opt.gate and ranked and router["judge"].p.provider != "rules":
        ranked, variants = head_to_head(show, anns, rank_pool, router, opt, funnel, log)
    picks = post_first(ranked, early_ep=opt.early_ep)
    funnel["ranked"] = len(ranked)
    u = router.budget.usage
    meta = {"models": router.describe(), "usage": u.__dict__,
            "by_role": {k: v.__dict__ for k, v in router.budget.by_role.items()},
            "seconds": round(time.time() - t_start, 1), "funnel": funnel,
            "options": {k: v for k, v in opt.__dict__.items()}, "ingest": rep}
    genders = {n: {r.label: r.gender for r in a.roles} for n, a in anns.items()}
    write_all(show, out, ranked, variants, picks, meta, genders=genders,
              extra={"repairs": [{"id": r.id, "from": (r.repair or {}).get("from"), "result": (r.repair or {}).get("result"),
                                  "log": (r.repair or {}).get("log")} for r in repaired]})
    (out / "candidates.json").write_text(json.dumps(
        [dict(p.to_dict(), final_score=final_score(p)) for p in pool + repaired], indent=1, default=str), encoding="utf-8")
    log(f"Done in {meta['seconds']}s | calls {u.calls} (+{u.cache_hits} cached) | est. ${u.usd:.2f} | funnel {funnel}")
    return {"show": show, "anns": anns, "pool": pool, "short": short, "ranked": ranked, "picks": picks, "meta": meta}


def head_to_head(show, anns, rank_pool: list[Promo], router: Router, opt: Options, funnel: dict, log=print):
    """The 0-3 scores tie at the top, so the final order comes from side-by-side comparisons: the judge ranks
    small groups of the best promos, every promo meets several rivals in different positions, and the wins are
    pooled into one strength each. Only promos the cold viewer followed take part; the rest keep the score order."""
    contenders, _ = select(rank_pool, top=opt.top + opt.judge_extra, per_episode=2)
    unchecked = [p for p in contenders if not p.gate or p.gate.get("unchecked")]
    if unchecked:
        gate_all(show, anns, unchecked, router["gate"], log=log)
    contenders = [p for p in contenders if (p.gate or {}).get("passed")]
    if len(contenders) < 3:
        return select(rank_pool, top=opt.top, per_episode=2)
    judge = router["judge"]
    log(f"  head-to-head: {judge.p.model} compares the top {len(contenders)} promos side by side ...")
    items = {p.id: (p.duration, clip_text(show, anns, p, anonymize=False)) for p in contenders}
    try:
        res = tournament(judge, items, rounds=opt.judge_rounds, size=opt.judge_size, log=log)
    except BudgetExceeded as e:
        log(f"  head-to-head skipped ({e}); keeping the score order")
        return select(rank_pool, top=opt.top, per_episode=2)
    if res["failed"] * 2 > res["calls"]:
        log(f"  head-to-head: {res['failed']} of {res['calls']} groups failed; keeping the score order")
        return select(rank_pool, top=opt.top, per_episode=2)
    for p in contenders:
        p.judge = {"strength": res["strength"][p.id], "won": res["wins"][p.id], "of": res["games"][p.id]}
    lead = sorted(contenders, key=lambda p: (-p.judge["strength"], -final_score(p)))
    funnel["head_to_head"] = len(contenders)
    funnel["judge_groups"] = res["calls"] - res["failed"]
    ranked, variants = select(rank_pool, top=opt.top, per_episode=2, lead=lead)
    late = [p for p in ranked if not p.gate or p.gate.get("unchecked")]
    if late:            # only possible when fewer promos were judged than are shown
        gate_all(show, anns, late, router["gate"], log=log)
    return ranked, variants


def seed_candidates(show, anns, llm, opt: Options, log=print) -> list[Promo]:
    """Model-proposed whole promos, kept only if they pass the same validator. Scored from the annotation:
    credit if the opening is a ranked hook beat and the ending a ranked cliffhanger beat."""
    out = []
    for p in proposed(show, llm, log=log):
        if validate(show, p, opt.min_len, opt.max_len):
            continue
        ann = anns.get(p.episodes[-1])
        hooks = {h.beat_id: h for h in (ann.hooks if ann else [])}
        cliffs = {c.beat_id: c for c in (ann.cliffhangers if ann else [])}
        h, c = hooks.get(p.hook_beat), cliffs.get(p.cliff_beat)
        p.hook_strength, p.cliff_strength = (h.strength if h else 1), (c.strength if c else 1)
        p.needs_context = h.needs_context if h else 1
        p.hook_type, p.cliff_type = (h.type if h else "other"), (c.type if c else "other")
        p.open_question = c.open_question if c else ""
        p._unranked = (h is None, c is None)   # transient: which ends still need a second opinion
        p.score = seed_score(p)
        p.variant = "full"
        out.append(p)
    return out


def seed_score(p: Promo) -> float:
    """The anchored search's formula, for a proposal's single segment (no within-episode rank or note bonus)."""
    score = 100 * (0.37 * p.hook_strength / 3 + 0.37 * p.cliff_strength / 3 - 0.2 * p.needs_context / 3 + 0.08)
    if p.needs_context >= 1 and p.duration < CONTEXT_MIN_LEN:   # same rule as anchored search
        score -= CONTEXT_TIGHT_PENALTY * p.needs_context
    return round(score, 2)


def rescore_from_second_opinion(promos: list[Promo]) -> int:
    """Proposals whose opening or ending the annotator never ranked take that strength from the cold viewer's
    scores (0-3 on the same scale). Returns how many came out strong on both ends."""
    strong = 0
    for p in promos:
        g = p.gate or {}
        hook_missing, cliff_missing = getattr(p, "_unranked", (False, False))
        if g.get("passed") is None:      # the check itself failed: keep the cautious default
            continue
        if hook_missing:
            p.hook_strength = max(1, int(g.get("hook_score") or 1))
            p.needs_context = max(0, 3 - int(g.get("standalone_score") or 0))
        if cliff_missing:
            p.cliff_strength = max(1, int(g.get("cliff_score") or 1))
        p.score = seed_score(p)
        strong += p.hook_strength >= 3 and p.cliff_strength >= 3
    return strong
