"""Evaluation. Everything here is honest about n and about who the rater is.

  python -m thumbstop.evaluate build  <pack> <run_dir>   # blind labelling page + AI-judge ratings + controls
  python -m thumbstop.evaluate score  <pack> <run_dir> <decisions.csv>   # unblind and report

Session A (opening test): tool clip vs last-45s clip ending at the SAME point (only the opening differs).
Session B (design test): tool's best vs field-standard 'model proposes whole promos' best vs last-45s of the episode.
Controls: random windows, truncated endings, hook-stripped openings -> does the AI judge notice?
"""
from __future__ import annotations

import csv
import html
import json
import math
import random
import sys
from dataclasses import replace
from pathlib import Path

from .annotate import EpisodeAnnotation
from .baselines import last45, last45_at, proposed, random_windows
from .gate import clip_text, gate_one
from .llm import ROOT, Router, load_config
from .models import Show
from .parse import parse_show
from .search import Promo, make_segment, promo_from_dict, validate

TEST_EPS = (26, 50)          # never used for tuning prompts
DESIGN_EPS = (26, 40)        # session B (kept shorter so labelling stays ~45 min)
SEED = 20261107


def _load(pack: str, run_dir: Path):
    show = parse_show(pack)
    anns = {int(k): EpisodeAnnotation.model_validate(v) for k, v in
            json.loads((run_dir / "annotations.json").read_text(encoding="utf-8")).items()}
    cands = [promo_from_dict(d) for d in json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))]
    scores = {d["id"]: d.get("final_score", d.get("score", 0)) for d in json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))}
    return show, anns, cands, scores


def best_tool_per_episode(cands: list[Promo], scores: dict) -> dict[int, Promo]:
    best: dict[int, Promo] = {}
    for p in cands:
        if p.source not in ("anchored", "repaired"):
            continue
        e = p.episodes[-1]
        if e not in best or scores.get(p.id, 0) > scores.get(best[e].id, 0):
            best[e] = p
    return best


def _overlap_frac(a: Promo, b: Promo) -> float:
    inter = sum(max(0.0, min(x.end, y.end) - max(x.start, y.start)) for x in a.segments for y in b.segments if x.ep == y.ep)
    return inter / max(1e-6, max(a.duration, b.duration))


def _human_clip(show: Show, anns, p: Promo) -> str:
    return clip_text(show, anns, p, anonymize=False)


def build(pack: str, run_dir: Path, router: Router) -> None:
    import subprocess
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    show, anns, cands, scores = _load(pack, run_dir)
    rng = random.Random(SEED)
    out = ROOT / "labels"
    out.mkdir(exist_ok=True)
    best = best_tool_per_episode(cands, scores)
    tasks, key = [], {}

    # Session A: same ending, different opening
    for e in range(TEST_EPS[0], TEST_EPS[1] + 1):
        tool = best.get(e)
        if not tool:
            continue
        base = last45_at(show, tool)
        if not base or _overlap_frac(tool, base) > 0.85:
            continue
        clips = [("tool", tool), ("last45_same_end", base)]
        rng.shuffle(clips)
        tid = f"A-E{e:02d}"
        tasks.append({"id": tid, "session": "A", "episode": e,
                      "clips": [{"label": "AB"[i], "dur": round(p.duration, 1), "text": _human_clip(show, anns, p)}
                                for i, (_, p) in enumerate(clips)]})
        key[tid] = {"AB"[i]: {"design": d, "promo": p.to_dict()} for i, (d, p) in enumerate(clips)}

    # Session B: three designs, one episode each
    print("Running the field-standard baseline (model proposes whole promos) on episodes "
          f"{DESIGN_EPS[0]}-{DESIGN_EPS[1]} ...")
    sub = Show(name=show.name, episodes=[x for x in show.episodes if DESIGN_EPS[0] <= x.num <= DESIGN_EPS[1]])
    props = proposed(sub, router["propose"])
    for e in range(DESIGN_EPS[0], DESIGN_EPS[1] + 1):
        tool = best.get(e)
        fs = [p for p in props if p.episodes == [e] and not p.flags]
        fs_any = fs[0] if fs else next((p for p in props if p.episodes == [e]), None)
        l45 = last45(show.ep(e))
        if not (tool and fs_any and l45):
            continue
        clips = [("tool", tool), ("field_standard", fs_any), ("last45", l45)]
        rng.shuffle(clips)
        tid = f"B-E{e:02d}"
        tasks.append({"id": tid, "session": "B", "episode": e,
                      "clips": [{"label": "ABC"[i], "dur": round(p.duration, 1), "text": _human_clip(show, anns, p)}
                                for i, (_, p) in enumerate(clips)]})
        key[tid] = {"ABC"[i]: {"design": d, "promo": p.to_dict(), "valid": not validate(show, p)}
                    for i, (d, p) in enumerate(clips)}
    rng.shuffle(tasks)
    key["__meta"] = {"frozen_commit": commit, "seed": SEED, "test_episodes": TEST_EPS, "design_episodes": DESIGN_EPS}
    (out / "blind_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    (out / "blind_tasks.json").write_text(json.dumps(tasks, indent=1), encoding="utf-8")
    (out / "blind.html").write_text(blind_html(tasks), encoding="utf-8")
    print(f"Blind page: {out / 'blind.html'}  ({sum(t['session']=='A' for t in tasks)} pairs, "
          f"{sum(t['session']=='B' for t in tasks)} triads)")

    # AI judge on every clip in the blind set (second rater) + controls (judge calibration)
    print("AI judge (cold viewer) on blind clips and controls ...")
    judge = router["gate"]
    from concurrent.futures import ThreadPoolExecutor
    jobs = []
    for tid, m in key.items():
        if tid.startswith('__'):
            continue
        for lab, info in m.items():
            p = promo_from_dict(info["promo"])
            p.id = f"{tid}-{lab}"
            jobs.append(("blind", info["design"], p))
    for kind, p in build_controls(show, cands, scores, rng):
        jobs.append(("control", kind, p))
    def _j(job):
        try:
            return job, gate_one(show, anns, job[2], judge)
        except Exception as e:  # keep going; report the gap
            return job, {"error": str(e)[:200]}
    judged, ctrl = {}, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for (where, label, p), v in ex.map(_j, jobs):
            if where == "blind":
                judged[p.id] = {"design": label, **v}
            else:
                ctrl.append({"kind": label, "id": p.id, **{k: v.get(k) for k in ("passed", "hook_score", "cliff_score",
                                                                                "standalone_score")}})
    (out / "ai_judge.json").write_text(json.dumps({"blind": judged, "controls": ctrl}, indent=1), encoding="utf-8")
    print(control_report(ctrl))


def build_controls(show: Show, cands: list[Promo], scores: dict, rng: random.Random, n: int = 25):
    """Real top picks plus three broken versions each, so we can see if the judge notices the breakage."""
    top = sorted([p for p in cands if p.source == "anchored" and p.duration >= 26], key=lambda p: -scores.get(p.id, 0))[:n]
    out = []
    for p in top:
        out.append(("real", p))
        s = p.segments[-1]
        ep = show.ep(s.ep)
        # truncated ending: stop ~35% earlier, on a line boundary, so the cliffhanger never arrives
        units = [u for u in ep.cut_units if s.start <= u.t1 <= s.end - 0.35 * s.dur]
        if units:
            end = max(units, key=lambda u: u.t1).t1
            if end - s.start >= 15:
                out.append(("truncated_end", replace(p, id=p.id + "-T", segments=[make_segment(ep, s.start, end)])))
        # hook stripped: start ~8 s later on the next line boundary
        later = [u for u in ep.cut_units if u.t0 >= s.start + 8 and u.t0 <= s.end - 18]
        if later:
            out.append(("hook_stripped", replace(p, id=p.id + "-H", segments=[make_segment(ep, later[0].t0, s.end)])))
    for i, r in enumerate(random_windows(show, n, seed=rng.randint(0, 10_000))):
        r.id = f"RND{i:02d}"
        out.append(("random", r))
    return out


def control_report(ctrl: list[dict]) -> str:
    rows = ["Judge calibration (mean scores; a useful judge rates 'real' clearly above the broken versions):",
            f"{'kind':15s} {'n':>3s} {'pass%':>6s} {'hook':>5s} {'cliff':>5s} {'alone':>5s}"]
    for kind in ("real", "truncated_end", "hook_stripped", "random"):
        xs = [c for c in ctrl if c["kind"] == kind and c.get("hook_score") is not None]
        if not xs:
            continue
        m = lambda k: sum(c[k] for c in xs) / len(xs)
        rows.append(f"{kind:15s} {len(xs):3d} {100*sum(bool(c['passed']) for c in xs)/len(xs):5.0f}% "
                    f"{m('hook_score'):5.2f} {m('cliff_score'):5.2f} {m('standalone_score'):5.2f}")
    real = [c for c in ctrl if c["kind"] == "real"]
    broken = [c for c in ctrl if c["kind"] != "real"]
    tot = lambda c: (c.get("hook_score") or 0) + (c.get("cliff_score") or 0) + (c.get("standalone_score") or 0)
    if real and broken:
        wins = sum((tot(r) > tot(b)) + 0.5 * (tot(r) == tot(b)) for r in real for b in broken)
        rows.append(f"AUC real vs broken (total score): {wins / (len(real) * len(broken)):.2f}  (0.5 = cannot tell)")
    return "\n".join(rows)


def blind_html(tasks: list[dict]) -> str:
    items = []
    for t in tasks:
        q = ("Same ending, different opening. Which would you post?" if t["session"] == "A"
             else "Three different promos from one episode. Which would you post first?")
        clips = "".join(f"""<div class="clip"><h3>Clip {c['label']}</h3><pre>{html.escape(c['text'])}</pre>
<div class="yn">Hook in first 3s? <label><input type="radio" name="{t['id']}-{c['label']}-hook" value="1">yes</label><label><input type="radio" name="{t['id']}-{c['label']}-hook" value="0">no</label>
 &nbsp; Ends unresolved? <label><input type="radio" name="{t['id']}-{c['label']}-cliff" value="1">yes</label><label><input type="radio" name="{t['id']}-{c['label']}-cliff" value="0">no</label>
 &nbsp; Makes sense alone? <label><input type="radio" name="{t['id']}-{c['label']}-alone" value="1">yes</label><label><input type="radio" name="{t['id']}-{c['label']}-alone" value="0">no</label></div></div>"""
                        for c in t["clips"])
        opts = "".join(f'<label><input type="radio" name="{t["id"]}-pick" value="{c["label"]}">{c["label"]}</label>'
                       for c in t["clips"])
        items.append(f"""<section class="task" data-id="{t['id']}" data-labels="{''.join(c['label'] for c in t['clips'])}"><h2>{t['id'][:1]}{len(items)+1:02d}</h2><p class="q">{q}</p>
<div class="clips">{clips}</div><p class="pick"><b>Post:</b> {opts}<label><input type="radio" name="{t['id']}-pick" value="tie">can't choose</label><label><input type="radio" name="{t['id']}-pick" value="none">none</label></p></section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind Promo Test</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e6e3dc;--acc:#b4441c}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#151513;--fg:#ecebe6;--mut:#a3a29c;--card:#1f1f1c;--line:#34332f;--acc:#f08a5d}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1200px}}
.task{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:16px 0}}
.clips{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}} .clip{{border:1px solid var(--line);border-radius:8px;padding:8px}}
pre{{white-space:pre-wrap;font-size:13px;max-height:340px;overflow:auto}} h2{{margin:0;color:var(--acc);font-size:16px}} h3{{margin:0 0 4px;font-size:14px}}
.yn,.pick{{font-size:13px}} label{{margin-right:8px;white-space:nowrap}} .done{{border-color:var(--acc)}}
.bar{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center}}
</style></head><body><h1>Blind promo test</h1>
<p>Read each clip as if it scrolled past on TikTok and you have never seen the show. Clips are in random order; you are not told which tool made which. Judge only what is on the page. Answer the three yes/no questions per clip, then pick the one you would post.</p>
<div class="bar"><span>Done <b id="n">0</b>/{len(tasks)}</span><span>Timer <b id="t">0:00</b></span><button id="exp">Download answers (CSV)</button><small>Answers are kept in this browser until you download them.</small></div>
{''.join(items)}
<script>
const KEY='blind-promo-test'; let st={{}}; try{{st=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(e){{}}
const t0=st.__t0||Date.now(); st.__t0=t0;
function save(){{try{{localStorage.setItem(KEY,JSON.stringify(st))}}catch(e){{}}}}
document.querySelectorAll('input[type=radio]').forEach(i=>{{ if(st[i.name]===i.value) i.checked=true; i.onchange=()=>{{st[i.name]=i.value; st[i.name.split('-').slice(0,2).join('-')+'-at']=Math.round((Date.now()-t0)/1000); save(); paint()}} }});
function paint(){{let n=0; document.querySelectorAll('.task').forEach(t=>{{const d=!!st[t.dataset.id+'-pick']; t.classList.toggle('done',d); if(d)n++}}); document.getElementById('n').textContent=n}}
setInterval(()=>{{const s=Math.round((Date.now()-t0)/1000);document.getElementById('t').textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}},1000);
document.getElementById('exp').onclick=()=>{{const rows=[['task','clip','hook','cliff','alone','pick']];
 document.querySelectorAll('.task').forEach(t=>{{for(const L of t.dataset.labels){{rows.push([t.dataset.id,L,st[t.dataset.id+'-'+L+'-hook']??'',st[t.dataset.id+'-'+L+'-cliff']??'',st[t.dataset.id+'-'+L+'-alone']??'',st[t.dataset.id+'-pick']??''])}}}});
 rows.push(['__seconds','','','','',Math.round((Date.now()-t0)/1000)]);
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.map(r=>r.join(',')).join('\\n')],{{type:'text/csv'}}));a.download='blind_answers.csv';a.click()}};
paint();
</script></body></html>"""


# ---------------------------------------------------------------- scoring after labels
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - h), 2), round(min(1.0, c + h), 2))


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def score(run_dir: Path, answers_csv: Path) -> str:
    key = json.loads((ROOT / "labels" / "blind_key.json").read_text(encoding="utf-8"))
    ai = json.loads((ROOT / "labels" / "ai_judge.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader(open(answers_csv, encoding="utf-8")))
    by = {}
    for r in rows:
        if r["task"].startswith("__"):
            continue
        by.setdefault(r["task"], {})[r["clip"]] = r
    L = ["# Blind evaluation results", ""]
    # Session A
    w = l = t = 0
    for tid, clips in by.items():
        if not tid.startswith("A-"):
            continue
        pick = next(iter(clips.values()))["pick"]
        if pick in ("tie", "none", ""):
            t += 1
            continue
        d = key[tid][pick]["design"]
        w += d == "tool"
        l += d != "tool"
    L += ["## Session A - same ending, different opening (tool vs last-45s)",
          f"Tool preferred {w}, last-45s preferred {l}, tie/none {t}. Two-sided sign test p = {sign_test(w, l)}. "
          f"Tool win rate {w}/{w + l} (95% CI {wilson(w, w + l)}).", ""]
    # Session B
    picks = {"tool": 0, "field_standard": 0, "last45": 0, "none": 0}
    nb = 0
    for tid, clips in by.items():
        if not tid.startswith("B-"):
            continue
        nb += 1
        pick = next(iter(clips.values()))["pick"]
        if pick in ("tie", "none", ""):
            picks["none"] += 1
        else:
            picks[key[tid][pick]["design"]] += 1
    L += ["## Session B - which design's promo would you post (one episode each)",
          *[f"- {k}: {v}/{nb} (95% CI {wilson(v, nb)})" for k, v in picks.items()], ""]
    # Per-design yes rates
    rates: dict[str, dict[str, list[int]]] = {}
    for tid, clips in by.items():
        for lab, r in clips.items():
            d = key[tid][lab]["design"]
            for q in ("hook", "cliff", "alone"):
                if r[q] in ("0", "1"):
                    rates.setdefault(d, {}).setdefault(q, []).append(int(r[q]))
    L += ["## Your yes-rates per design (hook in 3s / ends unresolved / makes sense alone)"]
    for d, qs in rates.items():
        L.append(f"- {d}: " + ", ".join(f"{q} {sum(v)}/{len(v)}" for q, v in qs.items()))
    # AI judge vs human agreement on 'makes sense alone'
    agree = n = 0
    for tid, clips in by.items():
        for lab, r in clips.items():
            j = ai["blind"].get(f"{tid}-{lab}")
            if j and r["alone"] in ("0", "1") and j.get("standalone_score") is not None:
                n += 1
                agree += int(r["alone"]) == int(j["standalone_score"] >= 2)
    L += ["", f"AI judge vs you on 'makes sense alone': {agree}/{n} agree ({wilson(agree, n)}). Single human rater, one show: "
          "treat as directional."]
    L += ["", "```", control_report(ai["controls"]), "```"]
    text = "\n".join(L)
    (run_dir / "eval_report.md").write_text(text, encoding="utf-8")
    return text


def main(argv=None) -> int:
    from .__main__ import _load_env
    _load_env()
    a = argv or sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == "build":
        cfg = load_config()
        router = Router(cfg, out_dir=Path(a[2]))
        build(a[1], Path(a[2]), router)
        u = router.budget.usage
        print(f"eval calls {u.calls} (+{u.cache_hits} cached), est ${u.usd:.2f}")
    elif a[0] == "score":
        print(score(Path(a[2]), Path(a[3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
