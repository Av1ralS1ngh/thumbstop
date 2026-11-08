"""Editor-facing outputs: promos.json / promos.csv (the brief's fields), report.md, review.html (cut sheets
with accept / nudge / reject + timer), selects.edl (CMX3600, top promos back to back)."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from .annotate import RULES_QUESTION
from .models import Beat, Show, fmt_tc, fmt_ts
from .search import Promo, cut_confidence

FPS = 30


def _units_in(show: Show, s) -> list[Beat]:
    ep = show.ep(s.ep)
    return [b for b in sorted(ep.beats, key=lambda b: (b.t0, b.idx))
            if b.kind in ("dialogue", "action") and s.start - 0.01 <= b.t0 < s.end]


def _camera_at(show: Show, ep_num: int, t: float) -> str:
    ep = show.ep(ep_num)
    cams = [b for b in ep.beats if b.kind == "camera" and b.t0 <= t + 0.05 < b.t1 + 0.05]
    return cams[0].text if cams else ""


def _next_unit(show: Show, ep_num: int, t: float) -> Beat | None:
    ep = show.ep(ep_num)
    nxt = [u for u in ep.cut_units if u.t0 >= t - 0.05]
    return nxt[0] if nxt else None


def _say(b: Beat | None) -> str:
    if not b:
        return "(end of episode)"
    if b.kind != "dialogue":
        return f"[{b.text}]"
    return f'{b.speaker}: "{b.text}"' if b.speaker else f'"{b.text}"'



def cut_sheet(show: Show, p: Promo) -> dict:
    segs = []
    running = 0.0
    for i, s in enumerate(p.segments, 1):
        units = _units_in(show, s)
        first = units[0] if units else None
        last = max(units, key=lambda u: u.t1) if units else None
        conf = cut_confidence(show.ep(s.ep), s.start, s.end)
        running += s.dur
        segs.append({
            "seg": i, "episode": s.ep, "reel": f"EP{s.ep:03d}", "in_ts": fmt_ts(s.start), "out_ts": fmt_ts(s.end),
            "in_tc": fmt_tc(s.start, FPS), "out_tc": fmt_tc(s.end, FPS), "dur_s": round(s.dur, 2),
            "running_s": round(running, 2), "in_on": _say(first), "in_visual": _camera_at(show, s.ep, s.start),
            "out_on": _say(last), "stop_before": _say(_next_unit(show, s.ep, s.end)) if i == len(p.segments) else "",
            "cut_confidence": conf["level"], "gap_in_s": conf["gap_before_in_s"], "gap_out_s": conf["gap_after_out_s"],
            "handle_pre_s": conf["handles"]["pre_s"], "handle_post_s": conf["handles"]["post_s"],
        })
    paper = []
    off = 0.0
    for s in p.segments:
        for b in _units_in(show, s):
            paper.append(f"{off + b.t0 - s.start:5.1f}s  (E{s.ep} {fmt_ts(b.t0)})  {_say(b)}")
        off += s.dur
    hb, cb = show.beat(p.hook_beat), show.beat(p.cliff_beat)
    return {"segments": segs, "paper_edit": paper, "hook_quote": _say(hb), "cliff_quote": _say(cb)}


def promo_record(show: Show, p: Promo) -> dict:
    cs = cut_sheet(show, p)
    g = p.gate or {}
    return {
        "rank": p.rank, "id": p.id, "source": p.source, "episodes": p.episodes, "duration_s": p.duration,
        "segments": [{k: s[k] for k in ("seg", "episode", "in_ts", "out_ts", "dur_s")} for s in cs["segments"]],
        "hook": cs["hook_quote"], "hook_type": p.hook_type, "cliffhanger": cs["cliff_quote"],
        # the annotator's question names the characters; the cold viewer's uses anonymous labels (WOMAN 2),
        # so it stays inside cold_viewer as "what a stranger asks" and never becomes the editor-facing line
        "cliff_type": p.cliff_type, "open_question": p.open_question,
        "why_it_works": p.why, "rank_reason": p.rank_reason, "variant": p.variant, "score": p.score,
        "cold_viewer": {k: g.get(k) for k in ("passed", "premise", "open_question", "intent_match", "intent_note",
                                              "unexplained", "hook_score", "cliff_score", "standalone_score",
                                              "judge")} if g else None,
        "judge": p.judge, "policy": p.policy, "spoiler_note": p.spoiler, "repair": p.repair, "flags": p.flags,
        "cut_sheet": cs,
    }


def write_all(show: Show, out: Path, ranked: list[Promo], variants: dict, picks: list, meta: dict,
              extra: dict | None = None, genders: dict[int, dict[str, str]] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    recs = [promo_record(show, p) for p in ranked]
    var = {k: [promo_record(show, v) for v in vs] for k, vs in variants.items()}
    doc = {"show": show.name, "meta": meta,
           "post_first": [{"id": p.id, "rank": p.rank, "reason": r} for p, r in picks],
           "promos": recs, "variants": var, **(extra or {})}
    (out / "promos.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(out / "promos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "promo_id", "segment", "episode", "start", "end", "start_tc", "end_tc", "seg_dur_s",
                    "promo_dur_s", "hook", "cliffhanger", "why_it_works", "hook_type", "cliff_type", "open_question",
                    "cut_confidence", "policy", "cold_viewer_passed"])
        for p, r in zip(ranked, recs):
            for s in r["cut_sheet"]["segments"]:
                w.writerow([p.rank, p.id, s["seg"], s["episode"], s["in_ts"], s["out_ts"], s["in_tc"], s["out_tc"],
                            s["dur_s"], p.duration, r["hook"], r["cliffhanger"], p.why, p.hook_type, p.cliff_type,
                            r["open_question"], s["cut_confidence"], p.policy.get("status", ""),
                            (p.gate or {}).get("passed")])
    write_edl(out / "selects.edl", show, ranked)
    (out / "report.md").write_text(report_md(show, ranked, recs, picks, meta), encoding="utf-8")
    view = review_data(show, recs, picks, genders)
    (out / "review.json").write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
    (out / "review.html").write_text(review_html(view), encoding="utf-8")


def write_edl(path: Path, show: Show, ranked: list[Promo]) -> None:
    """CMX3600: every segment of every ranked promo, back to back, with a comment per promo.
    Record times are counted in whole frames so each event's record length equals its source length."""
    lines = [f"TITLE: {show.name[:60]} - promo selects", "FCM: NON-DROP FRAME", ""]
    def tc(frames: int) -> str:
        return fmt_tc(frames / FPS, FPS)

    ev, rec = 1, 3600 * FPS
    for p in ranked:
        for i, s in enumerate(p.segments):
            f_in, f_out = round(s.start * FPS), round(s.end * FPS)
            src_in, src_out, r_in, r_out = tc(f_in), tc(f_out), tc(rec), tc(rec + f_out - f_in)
            lines.append(f"{ev:03d}  EP{s.ep:03d}   V     C        {src_in} {src_out} {r_in} {r_out}")
            lines.append(f"{ev:03d}  EP{s.ep:03d}   A     C        {src_in} {src_out} {r_in} {r_out}")
            if i == 0:
                lines.append(f"* PROMO #{p.rank} {p.id} ({p.duration:.1f}s) {p.hook_type} -> {p.cliff_type}")
            lines.append(f"* FROM CLIP NAME: EP{s.ep:03d}")
            lines.append("")
            ev += 1
            rec += f_out - f_in
        rec += 2 * FPS  # 2 s gap between promos
    path.write_text("\n".join(lines), encoding="utf-8")


def report_md(show: Show, ranked: list[Promo], recs: list[dict], picks: list, meta: dict) -> str:
    u = meta.get("usage", {})
    L = [f"# Promo picks - {show.name}", "",
         f"Episodes: {len(show.episodes)} | Models: {meta.get('models')} | "
         f"Calls: {u.get('calls', 0)} (+{u.get('cache_hits', 0)} cached) | Est. cost: ${u.get('usd', 0):.2f} | "
         f"Run time: {meta.get('seconds', 0):.0f}s", "", "## Post these first", ""]
    for p, reason in picks:
        r = next(x for x in recs if x["id"] == p.id)
        segs = "; ".join(f"E{s['episode']} {s['in_ts']}-{s['out_ts']}" for s in r["segments"])
        L += [f"**#{p.rank} {p.id}** - {segs} ({p.duration:.0f}s)", f"- Hook: {r['hook']}",
              f"- Cliffhanger: {r['cliffhanger']}", f"- Why first: {reason}", ""]
    L += ["## Ranked promos", "", "| # | Segments | Len | Hook | Cliffhanger | Why it works | Cold viewer | Policy |",
          "|---|---|---|---|---|---|---|---|"]
    for r in recs:
        segs = "<br>".join(f"E{s['episode']} {s['in_ts']}-{s['out_ts']}" for s in r["segments"])
        cv = r["cold_viewer"] or {}
        cvs = "pass" if cv.get("passed") else ("fail" if cv.get("passed") is False else "-")
        esc = lambda x: str(x).replace("|", "/")
        L.append(f"| {r['rank']} | {segs} | {r['duration_s']:.0f}s | {esc(r['hook'])[:90]} | "
                 f"{esc(r['cliffhanger'])[:90]} | {esc(r['why_it_works'])[:80]} | {cvs} | {r['policy'].get('status', '')} |")
    return "\n".join(L) + "\n"


def run_fingerprint(recs: list[dict]) -> str:
    """Short id of exactly which clips this page shows, so saved decisions never carry over to a different run."""
    sig = json.dumps([(r["id"], [(s["episode"], s["in_ts"], s["out_ts"]) for s in r["segments"]]) for r in recs])
    return hashlib.sha1(sig.encode()).hexdigest()[:10]


def _secs(ts: str) -> float:
    """'MM:SS.ss' (or 'H:MM:SS.ss') -> seconds."""
    total = 0.0
    for part in ts.split(":"):
        total = total * 60 + float(part)
    return total


def _short(ts: str) -> str:
    """'01:19.00' -> '1:19' for people; editors get the frame timecodes elsewhere."""
    s = _secs(ts)
    return f"{int(s // 60)}:{int(s % 60):02d}"


def clip_lines(show: Show, rec: dict, genders: dict[int, dict[str, str]] | None = None) -> list[dict]:
    """Everything a viewer sees and hears in the promo, in order, timed from the promo's own 0:00.
    Used by the script preview player and the line-by-line view."""
    out: list[dict] = []
    off = 0.0
    hook_done = False
    for i, s in enumerate(rec["segments"]):
        ep = show.ep(s["episode"])
        a, b = _secs(s["in_ts"]), _secs(s["out_ts"])
        if ep is None:
            continue
        if i:
            out.append({"t": round(off, 2), "e": round(off, 2), "k": "cut", "text": f"Cut to episode {s['episode']}"})
        beats = sorted(ep.beats, key=lambda x: (x.t0, x.idx))
        shot = next((x for x in reversed(beats) if x.kind == "camera" and x.t0 <= a + 0.05 < x.t1 + 0.05), None)
        for x in beats:
            inside = a - 0.05 <= x.t0 < b - 0.05
            if not (inside or x is shot):
                continue
            t0 = max(x.t0, a)
            item = {"t": round(off + t0 - a, 2), "e": round(off + min(x.t1, b) - a, 2), "k": x.kind,
                    "who": x.speaker or "", "how": ", ".join(x.tags[:2]).lower(), "text": x.text}
            if x.kind == "dialogue" and genders:
                item["g"] = (genders.get(s["episode"]) or {}).get(x.speaker or "", "?")
            said = _say(x) if x.kind in ("dialogue", "action") else None
            if said == rec["hook"] and not hook_done:
                item["hook"], hook_done = True, True
            out.append(item)
        off += b - a
    cliffs = [it for it in out if it["k"] in ("dialogue", "action") and _say_item(it) == rec["cliffhanger"]]
    if cliffs:
        cliffs[-1]["cliff"] = True
    return out


def _say_item(it: dict) -> str:
    if it["k"] != "dialogue":
        return f"[{it['text']}]"
    return f'{it["who"]}: "{it["text"]}"' if it["who"] else f'"{it["text"]}"'


_ANON = re.compile(r"\b(WOMAN|MAN|PERSON)\s?\d", re.I)


def _title(r: dict) -> str:
    """The question the promo leaves open: the writer's, else the stranger's when it names no anonymous label,
    else the cliffhanger line itself."""
    q = (r.get("open_question") or "").strip()
    if q and q != RULES_QUESTION and not q.startswith("(rules)"):
        return q
    sq = ((r.get("cold_viewer") or {}).get("open_question") or "").strip()
    if sq and not _ANON.search(sq):
        return sq[:1].upper() + sq[1:]
    return r["cliffhanger"]


def _where(r: dict) -> str:
    return " + ".join(f"Episode {s['episode']} · {_short(s['in_ts'])} → {_short(s['out_ts'])}" for s in r["segments"])


_FLAG_TEXT = {
    "QUESTION_DRIFT": "The ending may not land. A stranger came away asking something other than the intended "
                      "question, so check that the last line really leaves it open and the setup is clear.",
}


def _flag(f: str) -> str:
    code, _, rest = f.partition(":")
    return _FLAG_TEXT.get(code, rest.strip() or f)


# ---------------------------------------------------------------- review page (data + a thin HTML shell)
# The page itself is one JS component (web/review.js + review.css) that renders from this JSON, so the hosted
# app and the standalone review.html are the same code.
WEB = Path(__file__).resolve().parent / "web"
FONTS = ("https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..500"
         "&family=Instrument+Serif:ital@0;1&display=swap")
_POLICY_NAME = {"violence": "violence", "minor_present": "a child on screen", "nudity_sexual": "nudity",
                "gore_torture": "gore", "weapon_at_camera": "a weapon at the camera"}


def _plain_rule(rule: str) -> str:
    return _POLICY_NAME.get(rule, rule.replace("_", " "))


def review_data(show: Show, recs: list[dict], picks: list, genders: dict[int, dict[str, str]] | None = None) -> dict:
    """Everything the review page shows, already in plain words, so the page only lays it out."""
    reasons = {p.id: r for p, r in picks}
    total_eps = max((e.num for e in show.episodes), default=0)
    promos = []
    for r in recs:
        cs, cv, pol = r["cut_sheet"], r["cold_viewer"] or {}, r["policy"] or {}
        levels = [s["cut_confidence"] for s in cs["segments"]]
        rules = sorted({e["rule"] for e in pol.get("evidence") or []})
        segs = []
        for s in cs["segments"]:
            ep = show.ep(s["episode"])
            a, b = _secs(s["in_ts"]), _secs(s["out_ts"])
            segs.append({"seg": s["seg"], "ep": s["episode"], "in_ts": s["in_ts"], "out_ts": s["out_ts"],
                         "in_tc": s["in_tc"], "out_tc": s["out_tc"], "dur": s["dur_s"], "a": round(a, 2), "b": round(b, 2),
                         "ep_dur": round(max(ep.duration if ep else 0.0, b, 1.0), 2), "in_on": s["in_on"],
                         "in_visual": s["in_visual"], "out_on": s["out_on"], "stop_before": s["stop_before"],
                         "conf": s["cut_confidence"], "gap_in": s["gap_in_s"], "gap_out": s["gap_out_s"],
                         "h_pre": s["handle_pre_s"], "h_post": s["handle_post_s"]})
        last_ep = cs["segments"][-1]["episode"] if cs["segments"] else 0
        rr = r.get("rank_reason") or ""
        promos.append({
            "id": r["id"], "rank": r["rank"], "title": _title(r), "where": _where(r), "dur": round(r["duration_s"], 2),
            "first": r["id"] in reasons, "reason": reasons.get(r["id"], ""),
            "hook": r["hook"], "cliff": r["cliffhanger"], "why": r["why_it_works"] or "",
            "hook_type": (r["hook_type"] or "").replace("_", " "), "cliff_type": (r["cliff_type"] or "").replace("_", " "),
            "rank_reason": "" if "higher overall score" in rr else rr,
            "stranger": {"passed": cv.get("passed"), "premise": cv.get("premise") or "",
                         "question": cv.get("open_question") or "", "intent": cv.get("intent_match"),
                         "lost": cv.get("unexplained") or []} if cv else None,
            "policy": {"status": pol.get("status", ""), "rules": [_plain_rule(x) for x in rules],
                       "evidence": [{"level": e["level"], "rule": _plain_rule(e["rule"]), "text": e["evidence"]}
                                    for e in pol.get("evidence") or []]},
            "cut": "low" if "low" in levels else "medium" if "medium" in levels else "high",
            "flags": [_flag(f) for f in r["flags"]], "segments": segs,
            "next_ep": last_ep + 1 if last_ep and last_ep < total_eps else None,
            "lines": clip_lines(show, r, genders),
        })
    return {"v": 2, "show": show.name, "episodes": len(show.episodes), "fps": FPS, "fp": run_fingerprint(recs),
            "promos": promos}


def review_html(view: dict) -> str:
    """A single-file review page: the shared component, inlined, with this run's data."""
    css = (WEB / "review.css").read_text(encoding="utf-8")
    js = (WEB / "review.js").read_text(encoding="utf-8")
    data = json.dumps(view, ensure_ascii=False).replace("<", "\\u003c")
    title = view["show"].replace("&", "&amp;").replace("<", "&lt;")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Promo review · {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{FONTS}" rel="stylesheet"><style>{css}</style></head>
<body class="tsr-page"><main class="tsr-wrap" id="review"></main>
<script type="application/json" id="review-data">{data}</script>
<script>{js}</script>
<script>BangerzReview.mount(document.getElementById("review"), JSON.parse(document.getElementById("review-data").textContent), {{standalone: true}});</script>
</body></html>
"""
