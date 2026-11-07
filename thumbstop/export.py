"""Editor-facing outputs: promos.json / promos.csv (the brief's fields), report.md, review.html (cut sheets
with accept / nudge / reject + timer), selects.edl (CMX3600, top promos back to back)."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

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
    return f'{b.speaker}: "{b.text}"' if b.kind == "dialogue" else f"[{b.text}]"


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
        "cliff_type": p.cliff_type, "open_question": g.get("open_question") or p.open_question,
        "why_it_works": p.why, "rank_reason": p.rank_reason, "variant": p.variant, "score": p.score,
        "cold_viewer": {k: g.get(k) for k in ("passed", "premise", "unexplained", "hook_score", "cliff_score",
                                              "standalone_score", "judge")} if g else None,
        "policy": p.policy, "spoiler_note": p.spoiler, "repair": p.repair, "flags": p.flags, "cut_sheet": cs,
    }


def write_all(show: Show, out: Path, ranked: list[Promo], variants: dict, picks: list, meta: dict,
              extra: dict | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    recs = [promo_record(show, p) for p in ranked]
    var = {k: [promo_record(show, v) for v in vs] for k, vs in variants.items()}
    doc = {"show": show.name, "meta": meta,
           "post_first": [{"id": p.id, "rank": p.rank, "reason": r} for p, r in picks],
           "promos": recs, "variants": var, **(extra or {})}
    (out / "promos.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    with open(out / "promos.csv", "w", newline="") as f:
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
    (out / "report.md").write_text(report_md(show, ranked, recs, picks, meta))
    (out / "review.html").write_text(review_html(show, recs, picks, meta))


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
    path.write_text("\n".join(lines))


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


def review_html(show: Show, recs: list[dict], picks: list, meta: dict) -> str:
    first_ids = [p.id for p, _ in picks]
    cards = []
    for r in recs:
        cs = r["cut_sheet"]
        rows = "".join(
            f"<tr><td>{s['seg']}</td><td>EP{s['episode']:03d}</td><td>{s['in_tc']}<br><small>{s['in_ts']}</small></td>"
            f"<td>{s['out_tc']}<br><small>{s['out_ts']}</small></td><td>{s['dur_s']:.1f}s</td>"
            f"<td><b>IN ON</b> {html.escape(s['in_on'])}<br><small>{html.escape(s['in_visual'])}</small><br>"
            f"<b>OUT ON</b> {html.escape(s['out_on'])}"
            + (f"<br><b>STOP BEFORE</b> {html.escape(s['stop_before'])}" if s["stop_before"] else "")
            + f"</td><td class='c-{s['cut_confidence']}'>{s['cut_confidence']}<br><small>gap in {s['gap_in_s']}s / "
            f"out {s['gap_out_s']}s<br>handles -{s['handle_pre_s']} / +{s['handle_post_s']}s</small></td></tr>"
            for s in cs["segments"])
        cv = r["cold_viewer"] or {}
        pol = r["policy"]
        badge = "FIRST" if r["id"] in first_ids else ""
        cards.append(f"""
<article class="card" data-id="{r['id']}">
 <header><span class="rank">#{r['rank']}</span> <b>{r['id']}</b> <span class="pill">{r['duration_s']:.0f}s</span>
  <span class="pill">{html.escape(r['hook_type'])} &rarr; {html.escape(r['cliff_type'])}</span>
  <span class="pill pol-{pol.get('status','')}">{pol.get('status','')}</span> {f'<span class="pill first">{badge}</span>' if badge else ''}</header>
 <p><b>Hook:</b> {html.escape(r['hook'])}<br><b>Cliffhanger:</b> {html.escape(r['cliffhanger'])}<br>
 <b>Open question:</b> {html.escape(r['open_question'] or '')}<br><b>Why it works:</b> {html.escape(r['why_it_works'] or '')}<br>
 <small>{html.escape(r['rank_reason'] or '')}</small></p>
 <table><thead><tr><th>#</th><th>Reel</th><th>In</th><th>Out</th><th>Dur</th><th>Cut on</th><th>Cut confidence</th></tr></thead><tbody>{rows}</tbody></table>
 <details><summary>Paper edit</summary><pre>{html.escape(chr(10).join(cs['paper_edit']))}</pre></details>
 <details><summary>Cold viewer ({'pass' if cv.get('passed') else 'fail' if cv.get('passed') is False else 'n/a'})</summary>
  <p>{html.escape(str(cv.get('premise') or ''))}<br><b>Lost on:</b> {html.escape(', '.join(cv.get('unexplained') or []) or 'nothing')}</p></details>
 {('<details><summary>Policy evidence</summary><ul>' + ''.join(f"<li>{e['level']} {e['rule']}: {html.escape(e['evidence'])}</li>" for e in pol.get('evidence', [])) + '</ul></details>') if pol.get('evidence') else ''}
 <div class="decide"><button data-v="accept">Accept as is</button><button data-v="nudge">Accept with nudge</button>
  <label>in &plusmn;s <input type="number" step="0.1" class="nin"></label><label>out &plusmn;s <input type="number" step="0.1" class="nout"></label>
  <button data-v="reject">Reject</button><select class="why"><option value="">reason…</option><option>weak hook</option>
  <option>not a cliffhanger</option><option>needs context</option><option>spoiler</option><option>bad cut</option><option>policy</option><option>duplicate</option></select>
  <span class="state"></span></div>
</article>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Promo Review</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e6e3dc;--acc:#b4441c;--ok:#2f7d32;--warn:#a86a00;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#151513;--fg:#ecebe6;--mut:#a3a29c;--card:#1f1f1c;--line:#34332f;--acc:#f08a5d}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;max-width:1100px;margin:auto}}
h1{{font-size:20px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
.rank{{color:var(--acc);font-weight:700;font-size:18px}} .pill{{border:1px solid var(--line);border-radius:99px;padding:1px 8px;font-size:12px;color:var(--mut)}}
.first{{background:var(--acc);color:#fff;border:none}} .pol-HOLD{{color:var(--bad)}} .pol-CHECK{{color:var(--warn)}} .pol-CLEAR{{color:var(--ok)}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}} td,th{{border-top:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}}
.c-high{{color:var(--ok)}} .c-medium{{color:var(--warn)}} .c-low{{color:var(--bad)}} pre{{white-space:pre-wrap;font-size:12px}}
.decide{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}} button{{padding:5px 10px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}}
input{{width:60px}} .done{{outline:2px solid var(--ok)}} .bar{{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
</style></head><body>
<h1>Promo review - {html.escape(show.name)}</h1>
<div class="bar"><span>Reviewed <b id="n">0</b>/{len(recs)}</span><span>Timer <b id="t">0:00</b></span>
<button id="exp">Download decisions (CSV)</button><small>Timecodes at {FPS} fps from script timestamps; confirm on picture.</small></div>
{''.join(cards)}
<script>
const KEY='promo-review-'+location.pathname; let st={{}}; try{{st=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(e){{}}
const t0=st.__t0||Date.now(); st.__t0=t0; const save=()=>{{try{{localStorage.setItem(KEY,JSON.stringify(st))}}catch(e){{}}}};
function paint(){{let n=0;document.querySelectorAll('.card').forEach(c=>{{const d=st[c.dataset.id];c.classList.toggle('done',!!d);c.querySelector('.state').textContent=d?d.v+(d.why?' ('+d.why+')':''):'';if(d)n++}});document.getElementById('n').textContent=n}}
document.querySelectorAll('.card').forEach(c=>c.querySelectorAll('button').forEach(b=>b.onclick=()=>{{st[c.dataset.id]={{v:b.dataset.v,nin:c.querySelector('.nin').value,nout:c.querySelector('.nout').value,why:c.querySelector('.why').value,at:Math.round((Date.now()-t0)/1000)}};save();paint()}}));
setInterval(()=>{{const s=Math.round((Date.now()-t0)/1000);document.getElementById('t').textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}},1000);
document.getElementById('exp').onclick=()=>{{const rows=[['promo_id','decision','nudge_in_s','nudge_out_s','reason','seconds_since_start']];for(const [k,d] of Object.entries(st)){{if(k==='__t0')continue;rows.push([k,d.v,d.nin,d.nout,d.why,d.at])}}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.map(r=>r.join(',')).join('\\n')],{{type:'text/csv'}}));a.download='review_decisions.csv';a.click()}};
paint();
</script></body></html>"""
