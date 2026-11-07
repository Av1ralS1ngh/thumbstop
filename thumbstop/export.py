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


def _short(ts: str) -> str:
    """'01:19.00' -> '1:19' for people; editors get the frame timecodes elsewhere."""
    s = _secs(ts)
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _nice(kind: str | None) -> str:
    return html.escape((kind or "").replace("_", " "))


def clip_lines(show: Show, rec: dict) -> list[dict]:
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


def _title(r: dict) -> str:
    q = (r.get("open_question") or "").strip()
    if q and not q.startswith("(rules)"):
        return q
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


_REVIEW_CSS = """
:root{--bg:#0c0a08;--ink:#f6f1e7;--mut:#a39c91;--dim:#756e64;--gold:#c9a45a;--gold-2:#e6c47e;
--ok:#7cc488;--warn:#e3b04f;--bad:#f0735f;--line:rgba(255,240,220,.10);--glass:rgba(255,248,235,.05);
--glass-2:rgba(255,248,235,.085);--r:24px;--ease:cubic-bezier(.2,.8,.2,1);
--sans:"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
color-scheme:dark}
*{box-sizing:border-box}
html{background:var(--bg);scroll-behavior:smooth;scroll-padding-top:90px}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased;min-height:100vh}
body::before{content:"";position:fixed;inset:-10vmax;z-index:-1;pointer-events:none;
background:radial-gradient(40vmax 32vmax at 10% 6%,rgba(214,170,88,.40),transparent 70%),
radial-gradient(36vmax 30vmax at 95% 28%,rgba(228,104,56,.30),transparent 70%),
radial-gradient(42vmax 32vmax at 25% 95%,rgba(170,98,38,.30),transparent 70%),
radial-gradient(30vmax 24vmax at 80% 90%,rgba(214,170,88,.20),transparent 70%),var(--bg)}
.wrap{max-width:1140px;margin:0 auto;padding:0 16px 80px}
.glass{position:relative;background:linear-gradient(180deg,var(--glass-2),var(--glass));
-webkit-backdrop-filter:blur(24px) saturate(170%);backdrop-filter:blur(24px) saturate(170%);
border-radius:var(--r);box-shadow:inset 0 1px 0 rgba(255,255,255,.13),inset 0 -1px 0 rgba(0,0,0,.25),0 30px 60px -30px rgba(0,0,0,.85)}
.glass::before{content:"";position:absolute;inset:0;border-radius:inherit;padding:1px;pointer-events:none;
background:linear-gradient(140deg,rgba(255,255,255,.38),rgba(255,255,255,.06) 32%,rgba(255,255,255,0) 55%,rgba(201,164,90,.30));
-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
.mono{font-family:var(--mono);font-feature-settings:"tnum"}
.lbl{display:block;font:500 10.5px var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--mut);margin-bottom:5px}
a{color:inherit}
/* top */
.top{padding:44px 0 8px}
.brand{display:flex;align-items:center;gap:10px;font-weight:500;color:var(--mut);font-size:14px}
.brand b{color:var(--ink);font-weight:600;letter-spacing:-.01em}
.logo{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;
background:radial-gradient(circle at 30% 25%,var(--gold-2),var(--gold) 55%,#8a6a2c);box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 6px 18px -6px rgba(201,164,90,.7)}
.logo i{width:9px;height:11px;margin-left:2px;background:#1a1206;clip-path:polygon(0 0,100% 50%,0 100%)}
h1{font-size:clamp(30px,5vw,54px);line-height:1.04;letter-spacing:-.04em;font-weight:500;margin:18px 0 12px;max-width:20ch}
.sub{color:var(--mut);max-width:66ch;margin:0 0 6px;font-size:16px}
.sub b{color:var(--ink);font-weight:500}
.hint{color:var(--dim);font-size:13px;margin:0}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:26px}
.stat{padding:16px 18px;border-radius:18px}
.stat b{display:block;font-size:28px;font-weight:500;letter-spacing:-.03em;line-height:1.1}
.stat span{font-size:12px;color:var(--mut)}
.stat .split{font-size:13px;display:flex;flex-wrap:wrap;gap:2px 10px;margin-top:6px;font-family:var(--mono);white-space:nowrap}
h2.sec{font-size:24px;font-weight:500;letter-spacing:-.03em;margin:42px 0 14px}
h2.sec small{display:block;font:500 11px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--gold);margin-bottom:6px}
.firsts{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.fcard{display:flex;flex-direction:column;gap:10px;padding:20px 22px;text-decoration:none;transition:transform 180ms var(--ease)}
@media (hover:hover){.fcard:hover{transform:translateY(-2px)}}
.fcard .r{font-size:34px;line-height:1;font-weight:500;letter-spacing:-.05em;color:var(--gold-2)}
.fcard .q{font-size:17px;line-height:1.35;letter-spacing:-.01em;font-weight:500}
.fcard .m{font:12px var(--mono);color:var(--mut)}
.fcard .w{font-size:13px;color:var(--mut);line-height:1.5}
.fcard .go{font-size:13px;color:var(--gold-2);margin-top:auto}
/* floating bar */
.bar{position:sticky;top:12px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;padding:9px 10px 9px 18px;border-radius:26px;margin:0 0 22px}
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
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.primary{background:linear-gradient(180deg,var(--gold-2),var(--gold));color:#1a1206;border-color:transparent;box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 8px 22px -10px rgba(201,164,90,.9)}
@media (hover:hover){.primary:hover:not(:disabled){background:linear-gradient(180deg,#f0d28f,#d4b066)}}
/* promo card */
.card{display:grid;grid-template-columns:262px minmax(0,1fr);gap:30px;padding:26px;margin:0 0 20px;transition:box-shadow .3s var(--ease),opacity .3s var(--ease)}
.card[hidden]{display:none}
.card[data-d=accept]{box-shadow:inset 0 0 0 1.5px rgba(124,196,136,.55),0 30px 60px -30px rgba(0,0,0,.85)}
.card[data-d=nudge]{box-shadow:inset 0 0 0 1.5px rgba(201,164,90,.65),0 30px 60px -30px rgba(0,0,0,.85)}
.card[data-d=reject]{box-shadow:inset 0 0 0 1.5px rgba(240,115,95,.5),0 30px 60px -30px rgba(0,0,0,.85);opacity:.7}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font:12.5px var(--mono);color:var(--mut)}
.rank{font:500 15px var(--sans);color:#1a1206;background:linear-gradient(180deg,var(--gold-2),var(--gold));padding:4px 10px;border-radius:999px;box-shadow:inset 0 1px 0 rgba(255,255,255,.5)}
.chip{display:inline-flex;align-items:center;gap:6px;font:12px var(--sans);padding:4px 10px;border-radius:999px;background:rgba(255,248,235,.07);border:1px solid var(--line);color:var(--ink);white-space:nowrap}
.chip.first{border-color:rgba(201,164,90,.5);color:var(--gold-2)}
h3{font-size:clamp(20px,2.3vw,26px);line-height:1.22;letter-spacing:-.025em;font-weight:500;margin:12px 0 18px;text-wrap:balance}
.quotes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 14px}
.quote{padding:14px 16px;border-radius:16px;background:rgba(0,0,0,.22);border:1px solid var(--line)}
.quote p{margin:0;font-size:15.5px;line-height:1.45}
.quote.cliff{background:linear-gradient(180deg,rgba(201,164,90,.12),rgba(201,164,90,.04));border-color:rgba(201,164,90,.25)}
.why{margin:0 0 14px;font-size:15px;color:var(--ink)} .why .lbl{display:inline;margin-right:8px}
.checks{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 4px}
.ck{display:inline-flex;align-items:center;gap:8px;font-size:13px;padding:7px 12px;border-radius:12px;background:rgba(0,0,0,.22);border:1px solid var(--line)}
.ck i{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor;flex:none}
.ck span{color:var(--ink)}
.ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)} .na{color:var(--dim)}
.tags{font-size:12.5px;color:var(--dim);margin:10px 0 0}
.flag{display:flex;gap:10px;align-items:flex-start;margin:12px 0 0;padding:10px 14px;border-radius:12px;font-size:13.5px;line-height:1.5;
color:#f3d9a0;background:rgba(227,176,79,.10);border:1px solid rgba(227,176,79,.28)}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--mut);font-size:13.5px;list-style:none;display:flex;align-items:center;gap:8px;padding:4px 0}
summary::-webkit-details-marker{display:none}
summary::before{content:"";width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform 160ms var(--ease)}
details[open] summary::before{transform:rotate(45deg)}
@media (hover:hover){summary:hover{color:var(--ink)}}
details p,details ul{font-size:14px;color:var(--ink)} details li{margin:4px 0}
/* line by line */
.script{list-style:none;margin:8px 0 4px;padding:0;display:grid;gap:2px}
.script li{display:grid;grid-template-columns:44px 1fr auto;gap:12px;padding:7px 10px;border-radius:10px;cursor:pointer;transition:background 140ms var(--ease)}
@media (hover:hover){.script li:hover{background:rgba(255,248,235,.05)}}
.script time{font:12px var(--mono);color:var(--dim);padding-top:2px}
.script p{margin:0;line-height:1.45}
.script .who{font:600 11px var(--mono);letter-spacing:.06em;color:var(--gold-2);margin-right:6px}
.script .how{font-size:12px;color:var(--dim);font-style:italic}
.script .camera p{color:var(--dim);font-style:italic;font-size:13.5px}
.script .action p{color:#d9cfbf}
.script .sfx p{color:var(--mut);font-size:13.5px}
.script .kind{font:500 10px var(--mono);letter-spacing:.08em;color:var(--dim);margin-right:6px}
.script .mark{font:600 10px var(--mono);letter-spacing:.08em;color:#1a1206;background:var(--gold);padding:3px 7px;border-radius:999px;align-self:start}
.script .hook,.script .cliff{background:rgba(201,164,90,.10);box-shadow:inset 2px 0 0 var(--gold)}
.script li.now{background:rgba(255,248,235,.10)}
.script .cutto{display:block;text-align:center;font:500 11px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--gold);padding:8px;cursor:default}
/* edit details */
.track{display:grid;grid-template-columns:62px 1fr auto;gap:12px;align-items:center;font-size:12px;margin:8px 0}
.track .reel{color:var(--mut)}
.lane{position:relative;height:18px;border-radius:6px;background:repeating-linear-gradient(90deg,rgba(255,255,255,.07) 0 1px,transparent 1px 10%),rgba(0,0,0,.3);border:1px solid var(--line);overflow:hidden}
.win{position:absolute;top:2px;bottom:2px;border-radius:4px;min-width:4px;background:linear-gradient(180deg,var(--gold-2),var(--gold));box-shadow:0 0 14px rgba(201,164,90,.5)}
.track .tc{color:var(--mut)}
.tbl{overflow-x:auto;margin:8px 0 4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);text-align:left;padding:8px}
td{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}
td small{color:var(--mut)} td.tcs{font-family:var(--mono);white-space:nowrap}
.cut b{font-size:10.5px;letter-spacing:.08em;color:var(--gold);font-weight:600;margin-right:4px}
pre{white-space:pre-wrap;font:12px/1.6 var(--mono);color:#d9d1c3;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
/* decision */
.decide{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.decide .lbl{display:inline;margin:0 4px 0 0}
.decide [data-v=accept]{background:rgba(124,196,136,.14);border-color:rgba(124,196,136,.35)}
.decide [data-v=nudge]{background:rgba(201,164,90,.15);border-color:rgba(201,164,90,.38)}
.decide [data-v=reject]{background:rgba(240,115,95,.12);border-color:rgba(240,115,95,.32)}
.decide label{font-size:12px;color:var(--mut);display:inline-flex;align-items:center;gap:6px}
input,select{font:13px var(--mono);color:var(--ink);background:rgba(0,0,0,.32);border:1px solid var(--line);border-radius:10px;padding:7px 9px}
input{width:72px} select{font-family:var(--sans)}
.state{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-left:auto}
.card[data-d=accept] .state{color:var(--ok)} .card[data-d=nudge] .state{color:var(--gold-2)} .card[data-d=reject] .state{color:var(--bad)}
/* phone preview */
.side{position:relative}
.phone{position:sticky;top:96px;width:262px;aspect-ratio:9/16;border-radius:30px;overflow:hidden;cursor:pointer;user-select:none;
background:radial-gradient(120% 70% at 50% 30%,#2b2119,#100c09 70%);
box-shadow:inset 0 0 0 1px rgba(255,240,220,.14),inset 0 1px 0 rgba(255,255,255,.18),0 40px 70px -30px rgba(0,0,0,.95),0 0 0 7px rgba(255,248,235,.04)}
.phone::after{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(140% 90% at 50% 50%,transparent 55%,rgba(0,0,0,.55))}
.ph-bars{position:absolute;top:12px;left:12px;right:12px;display:flex;gap:3px;z-index:2}
.ph-bars span{position:relative;height:3px;border-radius:3px;background:rgba(255,255,255,.22);overflow:hidden}
.ph-bars span b{position:absolute;inset:0;width:0;background:#fff;border-radius:inherit}
.ph-hookzone{position:absolute;top:17px;left:12px;height:2px;background:var(--gold);border-radius:2px;opacity:.9;z-index:2}
.ph-top{position:absolute;top:24px;left:14px;right:14px;display:flex;justify-content:space-between;align-items:center;font:500 10.5px var(--mono);letter-spacing:.06em;color:rgba(255,255,255,.75);z-index:2}
.ph-tag{position:absolute;top:50px;left:14px;font:700 10px var(--mono);letter-spacing:.1em;color:#1a1206;background:var(--gold);padding:4px 8px;border-radius:999px;opacity:0;transform:translateY(-4px);transition:opacity .2s,transform .2s;z-index:2}
.ph-tag.on{opacity:1;transform:none}
.ph-sfx{position:absolute;top:50px;right:14px;font:500 10.5px var(--sans);color:rgba(255,255,255,.7);background:rgba(0,0,0,.35);padding:4px 8px;border-radius:999px;opacity:0;transition:opacity .2s;max-width:60%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;z-index:2}
.ph-sfx.on{opacity:1}
.ph-pic{position:absolute;top:84px;left:18px;right:18px;z-index:1}
.ph-shot{font-size:12.5px;line-height:1.45;font-style:italic;color:rgba(246,241,231,.55);transition:opacity .25s}
.ph-act{margin-top:10px;font-size:14px;line-height:1.4;color:#f6f1e7;font-weight:500;transition:opacity .25s}
.ph-sub{position:absolute;left:14px;right:14px;bottom:58px;text-align:center;z-index:2;transition:opacity .15s}
.ph-who{display:block;font:600 10.5px var(--mono);letter-spacing:.08em;color:var(--gold-2);margin-bottom:5px;text-shadow:0 1px 6px rgba(0,0,0,.8)}
.ph-line{display:inline;font-size:17px;line-height:1.38;font-weight:650;color:#fff;text-shadow:0 2px 10px rgba(0,0,0,.9);
background:rgba(0,0,0,.38);-webkit-box-decoration-break:clone;box-decoration-break:clone;padding:2px 6px;border-radius:6px}
.ph-ctl{position:absolute;bottom:14px;left:14px;right:14px;display:flex;justify-content:space-between;align-items:center;z-index:3}
.ph-ctl button{padding:6px 10px;font-size:11.5px;background:rgba(0,0,0,.4);border-color:rgba(255,255,255,.14)}
.ph-note{font:10px var(--mono);letter-spacing:.06em;color:rgba(255,255,255,.4);text-transform:uppercase}
.ph-play{position:absolute;inset:0;display:grid;place-items:center;align-content:center;gap:10px;background:rgba(8,6,4,.42);z-index:4;transition:opacity .2s}
.ph-play .disc{width:66px;height:66px;border-radius:50%;display:grid;place-items:center;background:rgba(255,248,235,.16);
-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 10px 30px -8px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.18)}
.ph-play .disc i{width:20px;height:24px;margin-left:5px;background:#fff;clip-path:polygon(0 0,100% 50%,0 100%)}
.ph-play span{font-size:13px;font-weight:500;color:#fff;text-shadow:0 1px 8px rgba(0,0,0,.8)}
.ph-play small{font:10.5px var(--mono);color:rgba(255,255,255,.65);letter-spacing:.04em}
.phone.playing .ph-play{opacity:0;pointer-events:none}
.ph-end{position:absolute;inset:0;z-index:5;display:none;flex-direction:column;justify-content:center;gap:14px;padding:28px 22px;text-align:center;
background:linear-gradient(180deg,rgba(12,10,8,.55),rgba(12,10,8,.92))}
.phone.ended .ph-end{display:flex}
.ph-end .tbc{font:600 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--gold-2)}
.ph-end p{margin:0;font-size:17px;line-height:1.35;font-weight:500;color:#fff}
.ph-end button{align-self:center}
@media (hover:hover){.phone:hover .ph-play .disc{background:rgba(255,248,235,.24)}}
@media (max-width:720px){.firsts{grid-template-columns:1fr}}
@media (max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}.stats .stat:last-child{grid-column:span 2}.quotes{grid-template-columns:1fr}
.card{grid-template-columns:1fr;gap:20px}.phone{position:relative;top:0;margin:0 auto}}
@media (max-width:520px){.bar{position:static}.card{padding:18px 16px}.phone{width:min(262px,100%)}.state{margin-left:0}.script li{grid-template-columns:38px 1fr}.script .mark{grid-column:2;justify-self:start}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}html{scroll-behavior:auto}}
@media (prefers-reduced-transparency:reduce){.glass{background:#1a1612;-webkit-backdrop-filter:none;backdrop-filter:none}}
@media print{body::before,.bar,.decide,.side{display:none}body{background:#fff;color:#000}.glass{background:none;box-shadow:none;border:1px solid #bbb;backdrop-filter:none}
.glass::before{display:none}.card{display:block;break-inside:avoid}.quote{background:none}}
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
cards.forEach(c=>c.querySelectorAll('.decide button').forEach(b=>b.onclick=()=>{start();st[c.dataset.id]={v:b.dataset.v,nin:c.querySelector('.nin').value,nout:c.querySelector('.nout').value,why:c.querySelector('.why').value,at:Math.round((Date.now()-t0)/1000)};save();paint()}));
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{F=b.dataset.f;paint()});
setInterval(()=>{const e=$('t');if(!t0){e.textContent='not started';return}const s=Math.round((Date.now()-t0)/1000);e.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0')},1000);
$('go').onclick=()=>{start();paint()};
$('rst').onclick=()=>{if(confirm('Clear all decisions and the timer?')){st={};t0=null;save();document.querySelectorAll('.nin,.nout,.why').forEach(x=>x.value='');paint()}};
$('exp').onclick=()=>{const rows=[['promo_id','decision','nudge_in_s','nudge_out_s','reason','seconds_since_start']];for(const [k,d] of Object.entries(st)){if(k==='__t0')continue;rows.push([k,d.v,d.nin,d.nout,d.why,d.at])}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.map(r=>r.map(q).join(',')).join('\\n')],{type:'text/csv'}));a.download='review_decisions.csv';a.click()};
paint();
/* script preview: plays the clip from the script's own timings, like the promo would run */
const mm=s=>{s=Math.max(0,s);return Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0')};
let current=null;
cards.forEach(card=>{
 const ph=card.querySelector('.phone'), data=card.querySelector('.clip'); if(!ph||!data) return;
 const D=JSON.parse(data.textContent), dur=+ph.dataset.dur, g=s=>ph.querySelector(s);
 const shot=g('.ph-shot'), act=g('.ph-act'), sub=g('.ph-sub'), who=g('.ph-who'), line=g('.ph-line'), tag=g('.ph-tag'), sfx=g('.ph-sfx'), clock=g('.ph-clock');
 const bars=[...ph.querySelectorAll('.ph-bars b')], spans=JSON.parse(ph.dataset.segs), rows=[...card.querySelectorAll('.script li[data-t]')];
 let t=0, on=false, last=0, rate=1;
 function draw(){
  let cam=null, a=null, said=null, snd=null, cliff=false;
  for(const x of D){ if(x.t>t+0.001) break;
   if(x.k==='camera'||x.k==='cut') cam=x;
   if(x.k==='action'&&t<x.e+1.2) a=x;
   if(x.k==='dialogue'&&t<x.e+0.5) said=x;
   if(x.k==='sfx'&&t<x.e+0.3) snd=x;
   if(x.cliff&&t>=x.t) cliff=true }
  shot.textContent=cam?cam.text:''; act.textContent=a?a.text:'';
  if(said){who.textContent=(said.who||'')+(said.how?' · '+said.how:'');line.textContent=said.text;sub.style.opacity=1}else{sub.style.opacity=0}
  sfx.textContent=snd?'♪ '+snd.text:''; sfx.classList.toggle('on',!!snd);
  const hook=t<3; tag.textContent=cliff?'CLIFFHANGER':'HOOK · FIRST 3 S'; tag.classList.toggle('on',hook||cliff);
  clock.textContent=mm(t)+' / '+mm(dur);
  let acc=0; spans.forEach((d,i)=>{bars[i].style.width=Math.min(100,Math.max(0,(t-acc)/d*100))+'%';acc+=d});
  let k=-1; rows.forEach((r,i)=>{if(+r.dataset.t<=t+0.001)k=i}); rows.forEach((r,i)=>r.classList.toggle('now',on&&i===k));
 }
 function frame(now){ if(!on) return; t+=(now-last)/1000*rate; last=now;
  if(t>=dur){t=dur;draw();stop();ph.classList.add('ended');return} draw(); requestAnimationFrame(frame) }
 function play(){ if(current&&current!==api) current.stop(); current=api; if(t>=dur) t=0; ph.classList.remove('ended');
  on=true; ph.classList.add('playing'); last=performance.now(); requestAnimationFrame(frame) }
 function stop(){ on=false; ph.classList.remove('playing'); rows.forEach(r=>r.classList.remove('now')) }
 const api={stop};
 ph.addEventListener('click',e=>{ if(e.target.closest('.ph-rate')) return; if(e.target.closest('.ph-again')){t=0;play();return}
  on?stop():play() });
 g('.ph-rate').onclick=e=>{rate=rate===1?2:1;e.target.textContent=rate+'×'};
 ph.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();on?stop():play()}});
 rows.forEach(r=>r.addEventListener('click',()=>{t=+r.dataset.t;ph.classList.remove('ended');draw();if(!on)play()}));
 draw();
});
"""

_LOGO = "<i></i>"


def _card(show: Show, r: dict, first: bool) -> str:
    cs, cv, pol = r["cut_sheet"], r["cold_viewer"] or {}, r["policy"] or {}
    status = pol.get("status", "")
    rules = sorted({e["rule"] for e in pol.get("evidence") or []})
    policy = {"CLEAR": ("ok", "Clear to run as an ad"),
              "CHECK": ("warn", "Ad check needed" + (f": {', '.join(rules)}" if rules else "")),
              "HOLD": ("bad", "Hold as an ad" + (f": {', '.join(rules)}" if rules else ""))}.get(status, ("na", "Ad policy not checked"))
    passed = cv.get("passed")
    stranger = (("ok", "A stranger followed it") if passed else ("bad", "A stranger got lost") if passed is False
                else ("na", "Not shown to a stranger"))
    levels = [s["cut_confidence"] for s in cs["segments"]]
    worst = "low" if "low" in levels else "medium" if "medium" in levels else "high"
    cut = {"high": ("ok", "Clean cuts"), "medium": ("warn", "Tight cut, trim with care"), "low": ("bad", "Risky cut")}[worst]
    checks = "".join(f'<span class="ck {c}"><i></i><span>{html.escape(t)}</span></span>' for c, t in (stranger, policy, cut))

    lines = clip_lines(show, r)
    rows = []
    for it in lines:
        if it["k"] == "cut":
            rows.append(f'<li class="cutto">{html.escape(it["text"])}</li>')
            continue
        mark = "Hook" if it.get("hook") else "Cliffhanger" if it.get("cliff") else ""
        cls = it["k"] + (" hook" if it.get("hook") else "") + (" cliff" if it.get("cliff") else "")
        if it["k"] == "dialogue":
            body = (f'<span class="who">{html.escape(it["who"])}</span>' if it["who"] else "") + \
                   (f'<span class="how">{html.escape(it["how"])}</span>' if it["how"] else "") + f'<p>{html.escape(it["text"])}</p>'
        else:
            kind = {"camera": "SHOT", "action": "ACTION", "sfx": "SOUND"}[it["k"]]
            body = f'<p><span class="kind">{kind}</span>{html.escape(it["text"])}</p>'
        rows.append(f'<li class="{cls}" data-t="{it["t"]}"><time>{int(it["t"] // 60)}:{int(it["t"] % 60):02d}</time>'
                    f'<div>{body}</div>{f"<span class=mark>{mark}</span>" if mark else ""}</li>')
    clip_json = json.dumps([{k: v for k, v in it.items()} for it in lines], ensure_ascii=False).replace("<", "\\u003c")

    tracks, trows = [], []
    for s in cs["segments"]:
        ep = show.ep(s["episode"])
        a, b = _secs(s["in_ts"]), _secs(s["out_ts"])
        total = max(ep.duration if ep else 0.0, b, 1.0)
        tracks.append(
            f'<div class="track"><span class="reel mono">EP{s["episode"]:03d}</span>'
            f'<div class="lane" title="Where this cut sits in episode {s["episode"]}"><span class="win" '
            f'style="left:{100 * a / total:.2f}%;width:{100 * (b - a) / total:.2f}%"></span></div>'
            f'<span class="tc mono">{s["in_ts"]} → {s["out_ts"]}</span></div>')
        trows.append(
            f"<tr><td class='tcs'>{s['seg']}</td><td class='tcs'>EP{s['episode']:03d}</td>"
            f"<td class='tcs'>{s['in_tc']}<br><small>{s['in_ts']}</small></td>"
            f"<td class='tcs'>{s['out_tc']}<br><small>{s['out_ts']}</small></td><td class='tcs'>{s['dur_s']:.1f}s</td>"
            f"<td class='cut'><b>IN ON</b> {html.escape(s['in_on'])}<br><small>{html.escape(s['in_visual'])}</small><br>"
            f"<b>OUT ON</b> {html.escape(s['out_on'])}"
            + (f"<br><b>STOP BEFORE</b> {html.escape(s['stop_before'])}" if s["stop_before"] else "")
            + f"</td><td><span class='{ {'high': 'ok', 'medium': 'warn', 'low': 'bad'}[s['cut_confidence']] }'>{s['cut_confidence']}</span><br>"
            f"<small>{s['gap_in_s']}s quiet before the in point, {s['gap_out_s']}s after the out point<br>"
            f"handles -{s['handle_pre_s']} / +{s['handle_post_s']}s</small></td></tr>")
    match = (f" <small>(matches the intended question {cv['intent_match']}/3)</small>"
             if cv.get("intent_match") is not None else "")
    evidence = pol.get("evidence") or []
    segs = [round(s["dur_s"], 2) for s in cs["segments"]]
    total = sum(segs) or 1.0
    bars = "".join(f'<span style="flex:{d}"><b></b></span>' for d in segs)
    title = _title(r)
    return f"""
<article class="card glass" id="{r['id']}" data-id="{r['id']}"{' data-first="1"' if first else ''}{' data-flag="1"' if r['flags'] else ''}>
 <div class="side"><div class="phone" data-dur="{total:.2f}" data-segs='{json.dumps(segs)}' role="button" tabindex="0" aria-label="Play promo #{r['rank']} from the script">
  <div class="ph-bars">{bars}</div><span class="ph-hookzone" style="width:calc((100% - 24px) * {min(3.0, total) / total:.4f})"></span>
  <div class="ph-top"><span>EP {' + '.join(str(s['episode']) for s in cs['segments'])}</span><span class="ph-clock">0:00 / {int(total // 60)}:{int(total % 60):02d}</span></div>
  <span class="ph-tag"></span><span class="ph-sfx"></span>
  <div class="ph-pic"><div class="ph-shot"></div><div class="ph-act"></div></div>
  <div class="ph-sub"><span class="ph-who"></span><span class="ph-line"></span></div>
  <div class="ph-ctl"><span class="ph-note">Script preview</span><button class="ph-rate" type="button">1×</button></div>
  <div class="ph-play"><span class="disc"><i></i></span><span>Play the promo</span><small>{total:.0f} s · timed from the script</small></div>
  <div class="ph-end"><span class="tbc">To be continued</span><p>{html.escape(title)}</p><button class="ph-again" type="button">Play again</button></div>
 </div><script type="application/json" class="clip">{clip_json}</script></div>
 <div class="main">
  <div class="meta"><span class="rank">#{r['rank']}</span>{'<span class="chip first">Post first</span>' if first else ''}<span>{html.escape(_where(r))}</span><span>{r['duration_s']:.0f} s</span></div>
  <h3>{html.escape(title)}</h3>
  <div class="quotes"><div class="quote"><span class="lbl">The hook · first 3 s</span><p>{html.escape(r['hook'])}</p></div>
   <div class="quote cliff"><span class="lbl">The cliffhanger · last moment</span><p>{html.escape(r['cliffhanger'])}</p></div></div>
  <p class="why"><span class="lbl">Why it works</span>{html.escape(r['why_it_works'] or '')}</p>
  <div class="checks">{checks}</div>
  {''.join(f'<p class="flag"><span>&#9888;</span><span>{html.escape(_flag(f))}</span></p>' for f in r['flags'])}
  <p class="tags">Hook type: {_nice(r['hook_type'])} · Ending: {_nice(r['cliff_type'])}{f" · {html.escape(r['rank_reason'])}" if r.get('rank_reason') and 'higher overall score' not in r['rank_reason'] else ''}</p>
  <details><summary>Read the whole clip, line by line</summary><ol class="script">{''.join(rows)}</ol></details>
  <details><summary>Edit details: timecodes and cut points</summary>{''.join(tracks)}
   <div class="tbl"><table><thead><tr><th>#</th><th>Reel</th><th>In</th><th>Out</th><th>Dur</th><th>Cut on</th><th>Cut safety</th></tr></thead>
   <tbody>{''.join(trows)}</tbody></table></div></details>
  <details><summary>What the stranger understood</summary>
   <p>{html.escape(str(cv.get('premise') or 'Not checked.'))}<br><b>They're left asking:</b> {html.escape(str(cv.get('open_question') or '-'))}{match}<br>
   <b>Lost on:</b> {html.escape(', '.join(cv.get('unexplained') or []) or 'nothing')}</p></details>
  {('<details><summary>Why the ad check</summary><ul>' + ''.join(f"<li>{e['level']} {e['rule']}: {html.escape(e['evidence'])}</li>" for e in evidence) + '</ul></details>') if evidence else ''}
  <div class="decide"><span class="lbl">Your call</span><button data-v="accept">Accept as is</button><button data-v="nudge">Accept with nudge</button>
   <label>in &plusmn;s <input type="number" step="0.1" class="nin"></label><label>out &plusmn;s <input type="number" step="0.1" class="nout"></label>
   <button data-v="reject">Reject</button><select class="why" aria-label="Reason"><option value="">reason…</option><option>weak hook</option>
   <option>not a cliffhanger</option><option>needs context</option><option>spoiler</option><option>bad cut</option><option>policy</option><option>duplicate</option></select>
   <span class="state"></span></div>
 </div>
</article>"""


def _first_card(r: dict, reason: str) -> str:
    return (f'<a class="fcard glass" href="#{r["id"]}"><span class="r">#{r["rank"]}</span>'
            f'<span class="q">{html.escape(_title(r))}</span><span class="m">{html.escape(_where(r))} · {r["duration_s"]:.0f} s</span>'
            f'<span class="w">{html.escape(reason[:1].upper() + reason[1:])}.</span><span class="go">Watch it ↓</span></a>')


def review_html(show: Show, recs: list[dict], picks: list, meta: dict) -> str:
    first = [(p.id, reason) for p, reason in picks]
    first_ids = {i for i, _ in first}
    by_id = {r["id"]: r for r in recs}
    n = len(recs)
    pol = {k: sum((r["policy"] or {}).get("status") == k for r in recs) for k in ("CLEAR", "CHECK", "HOLD")}
    passed = sum(bool((r["cold_viewer"] or {}).get("passed")) for r in recs)
    flagged = sum(bool(r["flags"]) for r in recs)
    cards = "".join(_card(show, r, r["id"] in first_ids) for r in recs)
    firsts = "".join(_first_card(by_id[i], reason) for i, reason in first if i in by_id)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Promo Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..500&display=swap" rel="stylesheet">
<style>{_REVIEW_CSS}</style></head><body><div class="wrap">
<header class="top"><div class="brand"><span class="logo">{_LOGO}</span><b>Thumbstop</b><span>/ promo review</span></div>
<h1>{html.escape(show.name)}</h1>
<p class="sub">Thumbstop read all {len(show.episodes)} episodes and found these <b>{n} promos</b>, best first. Each one opens on a
hook in the first 3 seconds, ends on a cliffhanger, and was shown to a model that had never seen the show to check that a
stranger can follow it. <b>Press play on any promo</b> to watch it run from the script.</p>
<p class="hint">Timecodes are at {FPS} fps from the script timestamps, so confirm them on picture.</p>
<div class="stats"><div class="stat glass"><b>{n}</b><span>Promos ranked</span></div>
<div class="stat glass"><b>{len(first_ids)}</b><span>To post first</span></div>
<div class="stat glass"><b>{passed}/{n}</b><span>A stranger followed</span></div>
<div class="stat glass"><b>{flagged}</b><span>With a warning to check</span></div>
<div class="stat glass"><span>As an ad</span><div class="split"><span class="ok">{pol['CLEAR']} clear</span>
<span class="warn">{pol['CHECK']} check</span><span class="bad">{pol['HOLD']} hold</span></div></div></div></header>
{f'<h2 class="sec"><small>Start here</small>Post these first</h2><div class="firsts">{firsts}</div>' if firsts else ''}
<h2 class="sec"><small>All {n}, best first</small>Every promo</h2>
<div class="bar glass"><div class="prog"><span>Reviewed <b id="n">0</b>/{n}</span><div class="meter"><i id="pbar"></i></div></div>
<span class="timer">Timer <b id="t">not started</b></span>
<div class="seg" role="group" aria-label="Show"><button data-f="all" aria-pressed="true">All</button><button data-f="first">Post first</button>
<button data-f="todo">To review</button><button data-f="flag">Warnings</button></div><span class="grow"></span>
<button id="go" class="primary">Start timer</button><button id="exp">Download decisions (CSV)</button><button id="rst">Reset</button></div>
{cards}
</div><script>{_REVIEW_JS.replace('__FP__', run_fingerprint(recs))}</script></body></html>"""
