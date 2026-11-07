"""Sparse per-episode annotation: the AI ranks the few beats that matter (cliffhangers to cut after,
hook beats to open on). Code verifies every ID. A rules-only annotator gives an offline fallback."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from .llm import LLM, LLMError
from .models import Beat, Episode, Show

PROMPT_VERSION = "annotate-v1"

HookType = Literal["humiliation", "physical_aggression", "identity_reveal", "threat_ultimatum", "betrayal_caught",
                   "accusation_frameup", "proposal_or_contract", "power_reversal", "rescue", "shocking_confession",
                   "danger", "other"]
CliffType = Literal["unanswered_question", "threat_delivered", "reveal_about_to_land", "interrupted_action",
                    "pending_choice", "arrival_or_discovery", "held_reaction", "other"]


class CliffPick(BaseModel):
    beat_id: str = Field(description="ID of the beat to cut AFTER (e.g. E07-B031)")
    strength: int = Field(description="1 = mild, 2 = strong, 3 = must-know-what-happens-next")
    type: CliffType
    open_question: str = Field(description="The question a viewer is left with, in under 15 words")


class HookPick(BaseModel):
    beat_id: str = Field(description="ID of the beat a promo could OPEN on")
    strength: int = Field(description="1 = mild, 2 = strong, 3 = instant scroll-stopper")
    type: HookType
    needs_context: int = Field(description="0 = a stranger gets it instantly ... 3 = meaningless without earlier episodes")
    why: str = Field(description="Under 15 words")


class RoleCard(BaseModel):
    label: str = Field(description="Speaker label exactly as written")
    role: str = Field(description="Spoiler-safe archetype a stranger would use, e.g. 'the jilted bride'")
    gender: Literal["F", "M", "?"]


class EpisodeAnnotation(BaseModel):
    cliffhangers: list[CliffPick] = Field(description="Best first, at most 5")
    hooks: list[HookPick] = Field(description="Best first, at most 8")
    roles: list[RoleCard]
    ending_is_cliffhanger: bool = Field(description="Does the episode's final beat leave a real open question?")


SYSTEM = """You are the promo editor for a vertical microdrama app. Promos are 20-60 second clips cut from an episode and posted on TikTok/Reels/Shorts to win NEW viewers who have never seen the show.

You will read ONE episode as a list of beats. Each beat has an ID, a time span, and either dialogue or an ACTION. Lines starting with "~" are sound cues for context only (no ID).

Pick, from THIS episode only:
1. CLIFFHANGER beats (up to 5, best first): beats where cutting the clip right after the beat leaves a specific unresolved question (a threat just landed, a reveal about to happen, an interrupted action, a pending choice, someone arriving). Choose the LAST beat before the answer comes - never a beat whose question is answered by that same beat. Soft endings (a hug, a vow with nothing pending, a calm goodbye) are NOT cliffhangers.
2. HOOK beats (up to 8, best first): beats a promo could OPEN on so that a scrolling stranger stops within 3 seconds - a slap or shove, a public humiliation, a shocking line, a reveal, a threat, an accusation, a proposal. Prefer beats that make sense cold. Rate needs_context honestly: a line that only works if you watched earlier episodes ("So it was you all along") is 2-3; a slap at a wedding is 0.
3. ROLE cards for every speaker label: a spoiler-safe archetype a stranger would use, and gender.

Use only beat IDs that appear in the episode text. Do not invent IDs. Prefer quality over quantity: fewer, sharper picks beat many weak ones."""


def render_episode(ep: Episode) -> str:
    out, scene = [f"EPISODE {ep.num}"], None
    sfx = sorted((b for b in ep.beats if b.kind == "sfx"), key=lambda b: b.t0)
    for b in sorted((b for b in ep.beats if b.kind in ("dialogue", "action", "sfx")), key=lambda b: (b.t0, b.idx)):
        if b.scene != scene:
            scene = b.scene
            head = next((s.heading for s in ep.scenes if s.num == scene), "")
            out.append(f"-- SCENE {scene}: {head}")
        if b.kind == "sfx":
            out.append(f"~ [{b.t0:.1f}s] sound: {b.text}")
        else:
            out.append(b.render())
    del sfx
    return "\n".join(out)


def _validate(ep: Episode, ann: EpisodeAnnotation) -> tuple[EpisodeAnnotation, list[str]]:
    ok_ids = {b.id for b in ep.beats if b.is_cut_unit}
    issues: list[str] = []
    def keep(items, kind):
        seen, kept = set(), []
        for it in items:
            if it.beat_id not in ok_ids:
                issues.append(f"{kind} {it.beat_id}: not a dialogue/action beat in E{ep.num}")
                continue
            if it.beat_id in seen:
                continue
            seen.add(it.beat_id)
            it.strength = min(3, max(1, it.strength))
            if hasattr(it, "needs_context"):
                it.needs_context = min(3, max(0, it.needs_context))
            kept.append(it)
        return kept
    ann.cliffhangers = keep(ann.cliffhangers, "cliff")[:5]
    ann.hooks = keep(ann.hooks, "hook")[:8]
    return ann, issues


def annotate_episode(ep: Episode, llm: LLM) -> tuple[EpisodeAnnotation, list[str]]:
    if llm.p.provider == "rules":
        return rules_annotation(ep), []
    user = render_episode(ep)
    ann = llm.json(SYSTEM, user, EpisodeAnnotation, max_tokens=6000, prompt_version=PROMPT_VERSION,
                   tag=f"E{ep.num:02d}")
    ann, issues = _validate(ep, ann)
    if issues and (not ann.cliffhangers or not ann.hooks):
        fix = user + "\n\nNOTE: your previous answer used IDs that do not exist here: " + "; ".join(issues[:10]) + \
            ". Use only IDs shown above."
        ann2 = llm.json(SYSTEM, fix, EpisodeAnnotation, max_tokens=6000, prompt_version=PROMPT_VERSION + "-fix",
                        tag=f"E{ep.num:02d}-fix")
        ann, issues2 = _validate(ep, ann2)
        issues = issues + ["re-asked"] + issues2
    return ann, issues


def annotate_show(show: Show, llm: LLM, log=print) -> dict[int, EpisodeAnnotation]:
    results: dict[int, EpisodeAnnotation] = {}
    workers = 1 if llm.p.provider == "rules" else max(1, llm.p.max_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(annotate_episode, ep, llm): ep for ep in show.episodes}
        for fut in futs:
            ep = futs[fut]
            try:
                ann, issues = fut.result()
            except LLMError as e:
                log(f"  E{ep.num:02d}: annotation failed ({e}); falling back to rules")
                ann, issues = rules_annotation(ep), ["fallback:rules"]
            if issues:
                log(f"  E{ep.num:02d}: {len(issues)} annotation issue(s): {issues[:3]}")
            results[ep.num] = ann
    return results


# ---------------------------------------------------------------- rules-only annotator (offline / baseline)
HOT_TAGS = {"shouting", "screaming", "furious", "outraged", "accusing", "accusatory", "cruel", "gloating", "threatening",
            "venomous", "roaring", "shrieking", "enraged", "deadly", "menacing", "horrified", "desperate", "chilling",
            "commanding", "imperious", "snarl", "vicious", "mocking", "smug", "defiant", "shocked"}
SOFT_TAGS = {"gentle", "tender", "soft", "warm", "reassuring", "calm", "loving", "reverent", "measured"}
IMPACT = re.compile(r"\b(slap|strikes?|punch|shove|whip|crack|kneel|drops? to|collaps|bursts?|grab|drag|seiz|"
                    r"explo|fire|flames?|blood|knife|sword|gun|pistol|iron|chains?|arrest|poison|ring)\w*", re.I)
CLIFF_SFX = re.compile(r"cliffhanger|ominous|swell|sting|cut(s|ting)? to black|freez|holds? on", re.I)
HOOK_TYPES = [(re.compile(r"slap|strike|punch|shove|whip|grab|drag", re.I), "physical_aggression"),
              (re.compile(r"arrest|accus|stole|thief|witch|guilty", re.I), "accusation_frameup"),
              (re.compile(r"marry|wife|husband|ring|kneel|propos", re.I), "proposal_or_contract"),
              (re.compile(r"regret|kill|die|hang|execut|destroy|never", re.I), "threat_ultimatum"),
              (re.compile(r"poison|dead|alive|heir|secret|truth|who are you", re.I), "identity_reveal")]


def _heat(b: Beat) -> float:
    tags = {t.lower() for t in b.tags}
    s = 0.0
    s += 1.2 * sum(1 for t in tags if any(h in t for h in HOT_TAGS))
    s -= 0.8 * sum(1 for t in tags if any(h in t for h in SOFT_TAGS))
    s += 1.5 if IMPACT.search(b.text) else 0.0
    s += 0.6 if b.text.endswith("!") else 0.0
    s += 0.3 if len(b.text.split()) <= 8 else 0.0
    if "VO" in b.flags:
        s -= 1.0
    return s


# Offline mode has no AI, so it says so plainly instead of pretending to explain the pick.
RULES_WHY = "A charged opening line or action, found by simple keyword rules (offline mode, no AI)."
RULES_QUESTION = "What happens next?"


def rules_annotation(ep: Episode) -> EpisodeAnnotation:
    units = ep.cut_units
    sfx = [b for b in ep.beats if b.kind == "sfx"]
    dur = ep.duration or 1.0
    hooks, cliffs = [], []
    for i, b in enumerate(units):
        h = _heat(b)
        early_cont = b.t0 < 10 and "pick" in ep.continuity.lower()
        needs = 2 if early_cont else (1 if re.match(r"^(and|but|so|then|that|this|it)\b", b.text, re.I) else 0)
        htype = next((t for rx, t in HOOK_TYPES if rx.search(b.text)), "other")
        if h >= 1.5:
            hooks.append((h - 0.7 * needs, HookPick(beat_id=b.id, strength=min(3, 1 + int(h // 2)), type=htype,
                                                     needs_context=needs, why=RULES_WHY)))
        c = 0.0
        c += 1.5 if b.text.rstrip().endswith("?") else 0.0
        c += 1.0 if re.search(r"\b(will|before|unless|or else|never|tonight|tomorrow|who)\b", b.text, re.I) else 0.0
        c += 1.2 * any(CLIFF_SFX.search(s.text) and b.t1 - 1 <= s.t0 <= b.t1 + 4 for s in sfx)
        c += 1.0 * (b.t1 / dur > 0.9)
        c += 0.5 * _heat(b) / 3
        nxt = units[i + 1] if i + 1 < len(units) else None
        if c >= 1.5 and (nxt is None or nxt.t0 - b.t1 >= -0.2):
            cliffs.append((c, CliffPick(beat_id=b.id, strength=min(3, 1 + int(c // 1.5)), type="other",
                                         open_question=RULES_QUESTION)))
    hooks.sort(key=lambda x: -x[0])
    cliffs.sort(key=lambda x: -x[0])
    if not cliffs and units:
        last = units[-1]
        cliffs = [(0, CliffPick(beat_id=last.id, strength=1, type="other", open_question=RULES_QUESTION))]
    roles = []
    for lab in sorted({b.speaker for b in ep.beats if b.speaker}):
        g = "F" if re.search(r"\b(LADY|WOMAN|MAID|DUCHESS|MOTHER|SISTER|QUEEN|GIRL|WIFE|MATRIARCH|BRIDE|AUNT|"
                             r"DAUGHTER|MISS|MRS|NURSE|PRINCESS|GRANDMOTHER)\b", lab) else ("M" if re.search(
                                 r"\b(LORD|MAN|DUKE|FATHER|PRIEST|GUARD|KING|BOY|HUSBAND|NOBLEMAN|GROOM|THUG|"
                                 r"CONSTABLE|JUDGE)\b", lab) else "?")
        roles.append(RoleCard(label=lab, role=lab.title(), gender=g))
    end_cliff = bool(cliffs) and cliffs[0][1].beat_id == units[-1].id if units else False
    return EpisodeAnnotation(cliffhangers=[c for _, c in cliffs[:5]], hooks=[h for _, h in hooks[:8]],
                             roles=roles, ending_is_cliffhanger=end_cliff)
