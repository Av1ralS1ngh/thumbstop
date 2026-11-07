"""Editor-facing outputs: promos.json / promos.csv (the brief's fields), report.md, review.html (cut sheets
with accept / nudge / reject + timer), selects.edl (CMX3600, top promos back to back)."""
from __future__ import annotations

import csv
import hashlib
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
        "cliff_type": p.cliff_type, "open_question": p.open_question or g.get("open_question"),
        "why_it_works": p.why, "rank_reason": p.rank_reason, "variant": p.variant, "score": p.score,
        "cold_viewer": {k: g.get(k) for k in ("passed", "premise", "open_question", "intent_match", "intent_note",
                                              "unexplained", "hook_score", "cliff_score", "standalone_score",
                                              "judge")} if g else None,
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
    (out / "review.html").write_text(review_html(show, recs, picks, meta), encoding="utf-8")


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


def _nice(kind: str | None) -> str:
    return html.escape((kind or "").replace("_", " "))


_REVIEW_CSS = """
:root{--bg:#0c0a08;--ink:#f6f1e7;--mut:#a39c91;--dim:#6f695f;--gold:#c9a45a;--gold-2:#e6c47e;--ember:#e0673a;
--ok:#7cc488;--warn:#e3b04f;--bad:#f0735f;--line:rgba(255,240,220,.10);--glass:rgba(255,248,235,.055);
--glass-2:rgba(255,248,235,.09);--r:22px;--r-s:12px;--ease:cubic-bezier(.2,.8,.2,1);
--sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
color-scheme:dark}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased;min-height:100vh}
body::before{content:"";position:fixed;inset:-10vmax;z-index:-1;pointer-events:none;
background:radial-gradient(40vmax 32vmax at 10% 6%,rgba(214,170,88,.42),transparent 70%),
radial-gradient(36vmax 30vmax at 95% 28%,rgba(228,104,56,.34),transparent 70%),
radial-gradient(42vmax 32vmax at 25% 95%,rgba(170,98,38,.32),transparent 70%),
radial-gradient(30vmax 24vmax at 80% 90%,rgba(214,170,88,.22),transparent 70%),var(--bg)}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px 80px}
.glass{position:relative;background:linear-gradient(180deg,var(--glass-2),var(--glass));
-webkit-backdrop-filter:blur(24px) saturate(170%);backdrop-filter:blur(24px) saturate(170%);
border-radius:var(--r);box-shadow:inset 0 1px 0 rgba(255,255,255,.13),inset 0 -1px 0 rgba(0,0,0,.25),0 30px 60px -30px rgba(0,0,0,.85)}
.glass::before{content:"";position:absolute;inset:0;border-radius:inherit;padding:1px;pointer-events:none;
background:linear-gradient(140deg,rgba(255,255,255,.38),rgba(255,255,255,.06) 32%,rgba(255,255,255,0) 55%,rgba(201,164,90,.30));
-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
.mono{font-family:var(--mono);font-feature-settings:"tnum"}
.lbl{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);font-weight:500;margin-bottom:4px}
/* top */
.top{padding:44px 0 26px}
.brand{display:flex;align-items:center;gap:10px;font-weight:500;color:var(--mut);font-size:14px}
.brand b{color:var(--ink);font-weight:600;letter-spacing:-.01em}
.logo{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;
background:radial-gradient(circle at 30% 25%,var(--gold-2),var(--gold) 55%,#8a6a2c);box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 6px 18px -6px rgba(201,164,90,.7)}
.logo svg{width:14px;height:14px}
h1{font-size:clamp(30px,5vw,52px);line-height:1.04;letter-spacing:-.04em;font-weight:500;margin:18px 0 10px;max-width:22ch}
.sub{color:var(--mut);max-width:62ch;margin:0}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:26px}
.stat{padding:16px 18px;border-radius:18px}
.stat b{display:block;font-size:28px;font-weight:500;letter-spacing:-.03em;line-height:1.1}
.stat span{font-size:12px;color:var(--mut)}
.stat .split{font-size:13px;color:var(--mut);display:flex;flex-wrap:wrap;gap:2px 10px;margin-top:6px;font-family:var(--mono);white-space:nowrap}
/* floating bar */
.bar{position:sticky;top:12px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;padding:9px 10px 9px 18px;border-radius:26px;margin:6px 0 22px}
.prog{display:flex;align-items:center;gap:10px;font-size:14px}
.meter{width:80px;height:6px;border-radius:9px;background:rgba(255,255,255,.08);overflow:hidden}
.meter i{display:block;height:100%;width:0;border-radius:inherit;background:linear-gradient(90deg,var(--gold),var(--gold-2));transition:width .4s var(--ease)}
.timer{font-size:14px;color:var(--mut)} .timer b{color:var(--ink);font-family:var(--mono);font-weight:500}
.seg{display:inline-flex;padding:3px;border-radius:999px;background:rgba(0,0,0,.28);box-shadow:inset 0 1px 2px rgba(0,0,0,.4)}
.seg button{border:0;background:transparent;box-shadow:none;padding:6px 10px;color:var(--mut)}
.seg button[aria-pressed=true]{background:rgba(255,248,235,.14);color:var(--ink);box-shadow:inset 0 1px 0 rgba(255,255,255,.14)}
.grow{flex:1}
/* buttons */
button{font:500 13px/1 var(--sans);color:var(--ink);padding:9px 13px;border-radius:999px;cursor:pointer;
border:1px solid var(--line);background:rgba(255,248,235,.07);box-shadow:inset 0 1px 0 rgba(255,255,255,.10);
transition:transform 140ms var(--ease),background 140ms var(--ease),border-color 140ms var(--ease)}
@media (hover:hover){button:hover:not(:disabled){transform:translateY(-1px);background:rgba(255,248,235,.13);border-color:rgba(255,240,220,.22)}}
button:active:not(:disabled){transform:translateY(1px) scale(.985);transition-duration:90ms}
button:disabled{opacity:.45;cursor:default}
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.primary{background:linear-gradient(180deg,var(--gold-2),var(--gold));color:#1a1206;border-color:transparent;box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 8px 22px -10px rgba(201,164,90,.9)}
@media (hover:hover){.primary:hover:not(:disabled){background:linear-gradient(180deg,#f0d28f,#d4b066)}}
/* cards */
.card{padding:24px 26px;margin:0 0 18px;transition:box-shadow .3s var(--ease)}
.card[hidden]{display:none}
.card[data-d=accept]{box-shadow:inset 0 0 0 1.5px rgba(124,196,136,.55),0 30px 60px -30px rgba(0,0,0,.85)}
.card[data-d=nudge]{box-shadow:inset 0 0 0 1.5px rgba(201,164,90,.65),0 30px 60px -30px rgba(0,0,0,.85)}
.card[data-d=reject]{box-shadow:inset 0 0 0 1.5px rgba(240,115,95,.5),0 30px 60px -30px rgba(0,0,0,.85);opacity:.72}
.head{display:flex;gap:18px;align-items:flex-start}
.rank{font-size:46px;line-height:.9;font-weight:500;letter-spacing:-.05em;color:var(--gold-2);min-width:64px;font-feature-settings:"tnum"}
.rank small{font-size:22px;color:var(--gold);opacity:.7;margin-right:1px}
.pid{font-family:var(--mono);font-size:13px;color:var(--mut);margin-right:6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 10px;border-radius:999px;background:rgba(255,248,235,.07);
border:1px solid var(--line);color:var(--ink);white-space:nowrap}
.chip i{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor}
.chip.first{background:linear-gradient(180deg,var(--gold-2),var(--gold));color:#1a1206;border-color:transparent;font-weight:600}
.pol-CLEAR,.c-high,.ok{color:var(--ok)} .pol-CHECK,.c-medium{color:var(--warn)} .pol-HOLD,.c-low,.bad{color:var(--bad)}
.quotes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:20px 0 14px}
.quote{padding:14px 16px;border-radius:16px;background:rgba(0,0,0,.22);border:1px solid var(--line)}
.quote p{margin:0;font-size:16px;line-height:1.45;letter-spacing:-.005em}
.quote.cliff{background:linear-gradient(180deg,rgba(201,164,90,.12),rgba(201,164,90,.04));border-color:rgba(201,164,90,.25)}
.ask{margin:0 0 6px;font-size:15px} .ask .lbl{display:inline;margin-right:8px}
.why{color:var(--mut);margin:0 0 4px;font-size:14px} .reason{color:var(--dim);font-size:12px;margin:0}
.flag{display:flex;gap:10px;align-items:flex-start;margin:12px 0 0;padding:10px 14px;border-radius:12px;font-size:13px;
color:#f3d9a0;background:rgba(227,176,79,.10);border:1px solid rgba(227,176,79,.28)}
.tracks{margin:18px 0 6px;display:grid;gap:10px}
.track{display:grid;grid-template-columns:62px 1fr auto;gap:12px;align-items:center;font-size:12px}
.track .reel{color:var(--mut)}
.lane{position:relative;height:22px;border-radius:7px;background:repeating-linear-gradient(90deg,rgba(255,255,255,.07) 0 1px,transparent 1px 10%),rgba(0,0,0,.3);
border:1px solid var(--line);overflow:hidden}
.win{position:absolute;top:2px;bottom:2px;border-radius:5px;min-width:4px;background:linear-gradient(180deg,var(--gold-2),var(--gold));box-shadow:0 0 18px rgba(201,164,90,.55)}
.track .tc{color:var(--mut)}
.tbl{overflow-x:auto;margin:10px 0 4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);text-align:left;padding:8px}
td{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}
td small{color:var(--mut)} td.tcs{font-family:var(--mono);white-space:nowrap}
.cut b{font-size:10.5px;letter-spacing:.08em;color:var(--gold);font-weight:600;margin-right:4px}
details{margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--mut);font-size:13px;list-style:none;display:flex;align-items:center;gap:8px;padding:4px 0}
summary::-webkit-details-marker{display:none}
summary::before{content:"";width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform 160ms var(--ease)}
details[open] summary::before{transform:rotate(45deg)}
@media (hover:hover){summary:hover{color:var(--ink)}}
details p,details ul{font-size:14px;color:var(--ink)} details li{margin:4px 0}
pre{white-space:pre-wrap;font:12px/1.6 var(--mono);color:#d9d1c3;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.decide{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.decide [data-v=accept]{background:rgba(124,196,136,.14);border-color:rgba(124,196,136,.35)}
.decide [data-v=nudge]{background:rgba(201,164,90,.15);border-color:rgba(201,164,90,.38)}
.decide [data-v=reject]{background:rgba(240,115,95,.12);border-color:rgba(240,115,95,.32)}
.decide label{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px}
input,select{font:13px var(--mono);color:var(--ink);background:rgba(0,0,0,.32);border:1px solid var(--line);border-radius:10px;padding:7px 9px}
input{width:72px} select{font-family:var(--sans)}
.state{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-left:auto}
.card[data-d=accept] .state{color:var(--ok)} .card[data-d=nudge] .state{color:var(--gold-2)} .card[data-d=reject] .state{color:var(--bad)}
@media (max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}.stats .stat:last-child{grid-column:span 2}.quotes{grid-template-columns:1fr}.bar{border-radius:20px}.meter{width:70px}}
@media (max-width:520px){.bar{position:static}.head{gap:12px}.rank{font-size:36px;min-width:46px}.card{padding:18px 16px}.track{grid-template-columns:52px 1fr}.track .tc{grid-column:2}.state{margin-left:0}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media (prefers-reduced-transparency:reduce){.glass{background:#1a1612;-webkit-backdrop-filter:none;backdrop-filter:none}}
@media print{body::before,.bar,.decide{display:none}body{background:#fff;color:#000}.glass{background:none;box-shadow:none;border:1px solid #bbb;backdrop-filter:none}
.glass::before{display:none}.card{break-inside:avoid}.quote{background:none}details{display:block}}
"""

_REVIEW_JS = """
const KEY='promo-review-'+location.pathname+'-__FP__'; let st={}; try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
let t0=st.__t0||null, F='all'; const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}};
const cards=[...document.querySelectorAll('.card')], $=id=>document.getElementById(id);
const start=()=>{if(!t0){t0=Date.now();st.__t0=t0;save()}};
const q=v=>{v=String(v??'');return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
function paint(){let n=0;cards.forEach(c=>{const d=st[c.dataset.id];c.classList.toggle('done',!!d);c.dataset.d=d?d.v:'';
 c.querySelector('.state').textContent=d?d.v+(d.why?' · '+d.why:''):'';
 if(d){n++;c.querySelector('.nin').value=d.nin;c.querySelector('.nout').value=d.nout;c.querySelector('.why').value=d.why}
 c.hidden=!(F==='all'||(F==='first'&&c.dataset.first)||(F==='todo'&&!d)||(F==='flag'&&c.dataset.flag))});
 $('n').textContent=n;$('pbar').style.width=(cards.length?100*n/cards.length:0)+'%';$('go').disabled=!!t0;
 document.querySelectorAll('.seg button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.f===F))}
cards.forEach(c=>c.querySelectorAll('button').forEach(b=>b.onclick=()=>{start();st[c.dataset.id]={v:b.dataset.v,nin:c.querySelector('.nin').value,nout:c.querySelector('.nout').value,why:c.querySelector('.why').value,at:Math.round((Date.now()-t0)/1000)};save();paint()}));
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{F=b.dataset.f;paint()});
setInterval(()=>{const e=$('t');if(!t0){e.textContent='not started';return}const s=Math.round((Date.now()-t0)/1000);e.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0')},1000);
$('go').onclick=()=>{start();paint()};
$('rst').onclick=()=>{if(confirm('Clear all decisions and the timer?')){st={};t0=null;save();document.querySelectorAll('.nin,.nout,.why').forEach(x=>x.value='');paint()}};
$('exp').onclick=()=>{const rows=[['promo_id','decision','nudge_in_s','nudge_out_s','reason','seconds_since_start']];for(const [k,d] of Object.entries(st)){if(k==='__t0')continue;rows.push([k,d.v,d.nin,d.nout,d.why,d.at])}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.map(r=>r.map(q).join(',')).join('\\n')],{type:'text/csv'}));a.download='review_decisions.csv';a.click()};
paint();
"""

_LOGO = ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 2.5v11l10-5.5z" fill="#1a1206"/>'
         '<rect x="12" y="2.5" width="1.8" height="11" rx=".9" fill="#1a1206"/></svg>')


def _card(show: Show, r: dict, first: bool) -> str:
    cs, cv, pol = r["cut_sheet"], r["cold_viewer"] or {}, r["policy"]
    status = pol.get("status", "")
    verdict = "pass" if cv.get("passed") else "fail" if cv.get("passed") is False else "n/a"
    tracks, rows = [], []
    for s in cs["segments"]:
        ep = show.ep(s["episode"])
        a, b = _secs(s["in_ts"]), _secs(s["out_ts"])
        total = max(ep.duration if ep else 0.0, b, 1.0)
        tracks.append(
            f'<div class="track"><span class="reel mono">EP{s["episode"]:03d}</span>'
            f'<div class="lane" title="Where this cut sits in episode {s["episode"]}"><span class="win" '
            f'style="left:{100 * a / total:.2f}%;width:{100 * (b - a) / total:.2f}%"></span></div>'
            f'<span class="tc mono">{s["in_ts"]} → {s["out_ts"]} · {s["dur_s"]:.1f}s</span></div>')
        rows.append(
            f"<tr><td class='tcs'>{s['seg']}</td><td class='tcs'>EP{s['episode']:03d}</td>"
            f"<td class='tcs'>{s['in_tc']}<br><small>{s['in_ts']}</small></td>"
            f"<td class='tcs'>{s['out_tc']}<br><small>{s['out_ts']}</small></td><td class='tcs'>{s['dur_s']:.1f}s</td>"
            f"<td class='cut'><b>IN ON</b> {html.escape(s['in_on'])}<br><small>{html.escape(s['in_visual'])}</small><br>"
            f"<b>OUT ON</b> {html.escape(s['out_on'])}"
            + (f"<br><b>STOP BEFORE</b> {html.escape(s['stop_before'])}" if s["stop_before"] else "")
            + f"</td><td class='c-{s['cut_confidence']}'>{s['cut_confidence']}<br><small>gap in {s['gap_in_s']}s / "
            f"out {s['gap_out_s']}s<br>handles -{s['handle_pre_s']} / +{s['handle_post_s']}s</small></td></tr>")
    match = (f" <small>(matches the intended question {cv['intent_match']}/3)</small>"
             if cv.get("intent_match") is not None else "")
    evidence = pol.get("evidence") or []
    return f"""
<article class="card glass" data-id="{r['id']}"{' data-first="1"' if first else ''}{' data-flag="1"' if r['flags'] else ''}>
 <div class="head"><div class="rank"><small>#</small>{r['rank']}</div><div>
  <span class="pid">{r['id']}</span>{'<span class="chip first">Post first</span>' if first else ''}
  <div class="chips"><span class="chip mono">{r['duration_s']:.0f}s</span>
   <span class="chip">{_nice(r['hook_type'])} → {_nice(r['cliff_type'])}</span>
   <span class="chip pol-{status}"><i></i>{status}</span>
   <span class="chip {'ok' if verdict == 'pass' else 'bad' if verdict == 'fail' else ''}"><i></i>Cold viewer {verdict}</span></div>
 </div></div>
 <div class="quotes"><div class="quote"><span class="lbl">Hook</span><p>{html.escape(r['hook'])}</p></div>
  <div class="quote cliff"><span class="lbl">Cliffhanger</span><p>{html.escape(r['cliffhanger'])}</p></div></div>
 <p class="ask"><span class="lbl">Open question</span>{html.escape(r['open_question'] or '')}</p>
 <p class="why">{html.escape(r['why_it_works'] or '')}</p><p class="reason">{html.escape(r['rank_reason'] or '')}</p>
 {''.join(f'<p class="flag"><span>&#9888;</span><span>{html.escape(f)}</span></p>' for f in r['flags'])}
 <div class="tracks">{''.join(tracks)}</div>
 <div class="tbl"><table><thead><tr><th>#</th><th>Reel</th><th>In</th><th>Out</th><th>Dur</th><th>Cut on</th><th>Cut confidence</th></tr></thead>
 <tbody>{''.join(rows)}</tbody></table></div>
 <details><summary>Paper edit</summary><pre>{html.escape(chr(10).join(cs['paper_edit']))}</pre></details>
 <details><summary>Cold viewer ({verdict})</summary>
  <p>{html.escape(str(cv.get('premise') or ''))}<br><b>A stranger asks:</b> {html.escape(str(cv.get('open_question') or '-'))}{match}<br>
  <b>Lost on:</b> {html.escape(', '.join(cv.get('unexplained') or []) or 'nothing')}</p></details>
 {('<details><summary>Policy evidence</summary><ul>' + ''.join(f"<li>{e['level']} {e['rule']}: {html.escape(e['evidence'])}</li>" for e in evidence) + '</ul></details>') if evidence else ''}
 <div class="decide"><button data-v="accept">Accept as is</button><button data-v="nudge">Accept with nudge</button>
  <label>in &plusmn;s <input type="number" step="0.1" class="nin"></label><label>out &plusmn;s <input type="number" step="0.1" class="nout"></label>
  <button data-v="reject">Reject</button><select class="why" aria-label="Reason"><option value="">reason…</option><option>weak hook</option>
  <option>not a cliffhanger</option><option>needs context</option><option>spoiler</option><option>bad cut</option><option>policy</option><option>duplicate</option></select>
  <span class="state"></span></div>
</article>"""


def review_html(show: Show, recs: list[dict], picks: list, meta: dict) -> str:
    first_ids = {p.id for p, _ in picks}
    n = len(recs)
    pol = {k: sum((r["policy"] or {}).get("status") == k for r in recs) for k in ("CLEAR", "CHECK", "HOLD")}
    passed = sum(bool((r["cold_viewer"] or {}).get("passed")) for r in recs)
    flagged = sum(bool(r["flags"]) for r in recs)
    cards = "".join(_card(show, r, r["id"] in first_ids) for r in recs)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Promo Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..500&display=swap" rel="stylesheet">
<style>{_REVIEW_CSS}</style></head><body><div class="wrap">
<header class="top"><div class="brand"><span class="logo">{_LOGO}</span><b>Thumbstop</b><span>/ promo review</span></div>
<h1>{html.escape(show.name)}</h1>
<p class="sub">{n} promos from {len(show.episodes)} episodes, best first. Accept, nudge or reject each one, then download your
decisions. Timecodes are at {FPS} fps from the script timestamps, so confirm them on picture.</p>
<div class="stats"><div class="stat glass"><b>{n}</b><span>Promos ranked</span></div>
<div class="stat glass"><b>{len(first_ids)}</b><span>To post first</span></div>
<div class="stat glass"><b>{passed}/{n}</b><span>Cold viewer passed</span></div>
<div class="stat glass"><b>{flagged}</b><span>With a warning</span></div>
<div class="stat glass"><span>Ad policy</span><div class="split"><span class="pol-CLEAR">{pol['CLEAR']} clear</span>
<span class="pol-CHECK">{pol['CHECK']} check</span><span class="pol-HOLD">{pol['HOLD']} hold</span></div></div></div></header>
<div class="bar glass"><div class="prog"><span>Reviewed <b id="n">0</b>/{n}</span><div class="meter"><i id="pbar"></i></div></div>
<span class="timer">Timer <b id="t">not started</b></span>
<div class="seg" role="group" aria-label="Show"><button data-f="all" aria-pressed="true">All</button><button data-f="first">Post first</button>
<button data-f="todo">To review</button><button data-f="flag">Warnings</button></div><span class="grow"></span>
<button id="go" class="primary">Start timer</button><button id="exp">Download decisions (CSV)</button><button id="rst">Reset</button></div>
{cards}
</div><script>{_REVIEW_JS.replace('__FP__', run_fingerprint(recs))}</script></body></html>"""
