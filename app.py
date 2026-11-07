"""Thumbstop - hosted prototype (Streamlit).

    streamlit run app.py

Pick a model (offline rules by default, no key needed), upload a .zip of episode scripts or use the
built-in fictional demo show, and get ranked promos with editor cut sheets and downloads. Uploads and
outputs live in a temporary folder that is deleted when the run ends; results stay in session memory.
"""
from __future__ import annotations

import contextlib
import html
import io
import json
import os
import re
import tempfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import streamlit as st

from thumbstop.llm import Router, load_config
from thumbstop.models import fmt_ts
from thumbstop.pipeline import Options, run

OFFLINE = "Offline (rules only, no key)"
BEDROCK = "AWS Bedrock (your AWS credentials)"
CUSTOM = "Any OpenAI-compatible API"
DEMO = "Demo show (original fiction, 3 episodes)"
UPLOAD = "Upload a .zip of episode files"
OUTPUTS = {"promos.csv": "text/csv", "promos.json": "application/json", "selects.edl": "text/plain",
           "review.html": "text/html"}
MAX_FILES, MAX_UNZIPPED = 1000, 200 * 1024 * 1024
EPISODE_SUFFIXES = (".txt", ".srt", ".vtt")   # same formats the CLI reads

# ---------------------------------------------------------------- demo show (original fiction)
DEMO_NAME = "The Forty-Third Floor (demo)"
Block = tuple[str, float, float, str]          # (label, start s, end s, text)
DEMO_EPISODES: list[dict] = [
    {"num": 1, "logline": "A junior analyst is fired in front of the board, then handed a keycard to a floor that "
                          "should not exist.",
     "continuity": "Series opener.",
     "scenes": [
         ("INT. GLASS BOARDROOM - DAY", 0.0, 48.0, [
             ("CAMERA", 0.0, 3.0, "Slow push across a glass boardroom forty-two floors above a rain-soaked city."),
             ("ACTION", 0.4, 2.8, "SERENA hurls a bound report across the table. It slaps against MIRA's chest."),
             ("SERENA COLE (Mocking, Cruel)", 3.2, 5.8, "Who let the intern touch the board deck?"),
             ("MIRA VOSS (Steady)", 6.2, 9.4, "I built that forecast. Every number in it is mine."),
             ("SERENA COLE (Gloating)", 9.8, 13.0, "Then every mistake is yours. Security walks you out at five."),
             ("ACTION", 13.4, 15.6, "Two board members trade smirks. MIRA kneels to gather the scattered pages."),
             ("DECLAN HART (Measured)", 16.0, 19.0, "Enough. Her forecast was right last quarter."),
             ("SERENA COLE (Cold)", 19.4, 22.6, "Last quarter she was fetching your coffee, Declan."),
             ("MIRA VOSS (Quiet, Defiant)", 23.0, 26.4, "And this quarter I found the eleven million you buried."),
             ("SOUND FX", 26.4, 28.4, "A sharp musical sting. Dead silence in the room."),
             ("ACTION", 26.6, 29.0, "SERENA freezes, her pen hovering over the page."),
             ("SERENA COLE (Shocked, Venomous)", 29.4, 32.0, "Say that again. I dare you!"),
             ("DECLAN HART (Commanding)", 32.4, 35.2, "Everyone out. Except the two of you."),
             ("ACTION", 35.6, 38.0, "The board files out. The glass door hisses shut."),
             ("SERENA COLE (Menacing)", 38.4, 42.0, "Do you even know whose money that was?"),
             ("MIRA VOSS (Defiant)", 42.4, 45.6, "No. But the auditors will by tomorrow."),
         ]),
         ("INT. OPEN-PLAN OFFICE - NIGHT", 48.0, 84.0, [
             ("CAMERA", 48.0, 51.0, "Low angle on an empty office floor, one desk lamp still burning."),
             ("ACTION", 48.4, 51.0, "MIRA packs a cardboard box. A SECURITY GUARD looms over her desk."),
             ("SECURITY GUARD (Commanding)", 51.4, 54.0, "Badge. Now. You're done here!"),
             ("MIRA VOSS (Bitter)", 54.4, 57.4, "Of course I am. She works fast."),
             ("ACTION", 57.8, 60.4, "TOBY slides a sealed envelope across the desk. The wax seal shows a silver hawk."),
             ("TOBY REYES (Nervous)", 60.8, 64.4, "This came for you today. Same hawk as the founder's ring."),
             ("MIRA VOSS (Shaken)", 64.8, 67.6, "That was my father's ring. He's been gone ten years."),
             ("ACTION", 68.0, 71.0, "MIRA breaks the seal. A single keycard falls out, stamped FLOOR 43."),
             ("TOBY REYES (Horrified)", 71.4, 74.0, "There is no forty-third floor."),
             ("ACTION", 74.4, 77.0, "Behind them, the elevator doors slide open on their own."),
             ("MIRA VOSS (Desperate)", 77.4, 80.6, "Who is waiting for me up there?"),
             ("SOUND FX", 80.6, 82.6, "Ominous low swell."),
             ("ACTION", 81.0, 84.0, "MIRA steps inside. The doors close."),
         ]),
     ]},
    {"num": 2, "logline": "On the hidden floor, the fired analyst learns she inherits control of the company at "
                          "midnight - and her rival knew all along.",
     "continuity": "Continues the Episode 1 cliffhanger: Mira rides the elevator to floor 43.",
     "scenes": [
         ("INT. HIDDEN EXECUTIVE FLOOR - NIGHT", 0.0, 50.0, [
             ("CAMERA", 0.0, 3.0, "Doors open onto a dark executive suite under dust sheets."),
             ("ACTION", 0.4, 3.0, "MIRA steps out. Motion sensors snap the lights on, one row at a time."),
             ("AUGUSTINE PELL (Calm)", 3.4, 6.6, "Miss Voss. Your father said you would come eventually."),
             ("MIRA VOSS (Wary)", 7.0, 9.6, "Who are you, and how do you know my name?"),
             ("AUGUSTINE PELL (Measured)", 10.0, 14.0, "I was his lawyer. As of tonight, I am yours."),
             ("ACTION", 14.4, 17.0, "PELL pulls a dust sheet off a desk. A share certificate lies under glass."),
             ("AUGUSTINE PELL (Grave)", 17.4, 21.6, "Fifty-one percent of this company. Yours, from midnight."),
             ("MIRA VOSS (Stunned)", 22.0, 24.4, "Midnight. That's in four minutes."),
             ("SOUND FX", 24.4, 26.0, "A clock ticks loudly somewhere in the dark."),
             ("AUGUSTINE PELL (Urgent)", 26.4, 30.4, "Which is why someone downstairs wants you gone before it strikes."),
             ("ACTION", 30.8, 33.4, "A red light blinks on the security camera in the corner."),
             ("MIRA VOSS (Shocked)", 33.8, 36.0, "Serena knew. She fired me tonight on purpose!"),
             ("AUGUSTINE PELL (Grim)", 36.4, 40.0, "She has known for ten years. So did Declan."),
             ("SOUND FX", 40.0, 42.0, "Elevator chime. Heels on marble."),
             ("ACTION", 40.4, 43.0, "The elevator opens. SERENA steps out, flanked by two guards."),
             ("SERENA COLE (Gloating, Menacing)", 43.4, 47.0, "Three minutes to midnight. Shall we wait together?"),
         ]),
         ("INT. HIDDEN EXECUTIVE FLOOR - CONTINUOUS", 50.0, 92.0, [
             ("CAMERA", 50.0, 53.0, "Tight two-shot: MIRA and SERENA, a desk between them."),
             ("ACTION", 50.4, 53.0, "SERENA slams a pen down beside a share-transfer form."),
             ("SERENA COLE (Cold)", 53.4, 57.0, "Sign the shares over, and nobody learns what your father did."),
             ("MIRA VOSS (Defiant)", 57.4, 60.4, "My father built this tower. You just rent an office in it."),
             ("SERENA COLE (Furious, Shouting)", 60.8, 63.0, "Sign it, or I end you tonight!"),
             ("ACTION", 63.4, 66.0, "A guard grabs MIRA's wrist and forces the pen into her hand."),
             ("AUGUSTINE PELL (Commanding)", 66.4, 69.4, "Let her go. The board is watching this feed live."),
             ("ACTION", 69.8, 72.4, "Every screen on the floor flickers on. Board members stare out from video calls."),
             ("DECLAN HART (Cold)", 72.8, 76.0, "Serena. Step away from her. Now."),
             ("SERENA COLE (Shocked)", 76.4, 79.0, "Declan, you swore you were with me."),
             ("SOUND FX", 79.0, 81.0, "The clock begins to chime midnight."),
             ("ACTION", 79.4, 82.4, "MIRA lifts the pen, looks straight at the camera, and smiles."),
             ("MIRA VOSS (Cold, Commanding)", 82.8, 86.4, "Midnight. So who works for who now?"),
             ("SOUND FX", 86.4, 88.4, "A sharp musical sting."),
             ("ACTION", 86.8, 92.0, "SERENA smiles back and draws a second envelope from her jacket."),
         ]),
     ]},
    {"num": 3, "logline": "A test result claims the new owner is not her father's daughter, and the man who "
                          "brought it has a claim of his own.",
     "continuity": "Continues the Episode 2 cliffhanger: Serena lays a second envelope on the desk.",
     "scenes": [
         ("INT. HIDDEN EXECUTIVE FLOOR - NIGHT", 0.0, 34.0, [
             ("CAMERA", 0.0, 3.0, "Close on the second envelope as it lands on the desk."),
             ("SERENA COLE (Gloating)", 0.6, 3.4, "Read it out loud, heiress."),
             ("ACTION", 3.8, 6.4, "MIRA tears the envelope open. A paternity report slides out."),
             ("MIRA VOSS (Horrified)", 6.8, 9.6, "No match. This says I'm not his daughter."),
             ("AUGUSTINE PELL (Urgent)", 10.0, 13.6, "Then the trust is void, and the shares pass to the next heir."),
             ("DECLAN HART (Quiet, Chilling)", 14.0, 17.0, "And the next heir is standing right here."),
             ("SOUND FX", 17.0, 19.0, "A sharp musical sting."),
             ("ACTION", 17.4, 20.0, "Every head turns to DECLAN. He straightens his cufflinks."),
             ("MIRA VOSS (Shaken, Furious)", 20.4, 23.0, "You? You were his partner!"),
             ("DECLAN HART (Smug)", 23.4, 27.4, "I was his son. He just never put my name on anything."),
             ("ACTION", 27.8, 30.4, "SERENA laughs and reaches for the transfer form."),
             ("SERENA COLE (Mocking)", 30.8, 33.6, "Guards. Escort the intern out of our building."),
         ]),
         ("INT. MIRRORED ELEVATOR - NIGHT", 34.0, 78.0, [
             ("CAMERA", 34.0, 37.0, "Inside a mirrored elevator. MIRA stands between two guards."),
             ("ACTION", 34.4, 37.0, "The doors close. MIRA stares at the report in her shaking hands."),
             ("MIRA VOSS (Quiet)", 37.4, 40.6, "The lab on this stamp closed years ago."),
             ("ACTION", 41.0, 43.6, "One guard glances at the other, then slowly reaches for the emergency stop."),
             ("SOUND FX", 43.6, 45.0, "Metal screech. The elevator jolts to a halt."),
             ("GUARD (Low)", 45.4, 48.6, "Miss Voss. Your father paid me to wait for this night."),
             ("MIRA VOSS (Wary)", 49.0, 51.4, "Paid you to do what?"),
             ("GUARD (Grave)", 51.8, 55.6, "To give you this, if anyone ever called you a fraud."),
             ("ACTION", 56.0, 58.8, "He presses a tiny flash drive into her palm. A red light pulses on it."),
             ("MIRA VOSS (Desperate)", 59.2, 62.0, "What's on it? Tell me!"),
             ("GUARD (Grave)", 62.4, 66.0, "The real test. And a confession, in your father's voice."),
             ("SOUND FX", 66.0, 68.0, "The elevator lurches. The lights go out."),
             ("ACTION", 66.4, 69.0, "Darkness. The elevator starts moving again on its own, upward."),
             ("MIRA VOSS (Horrified)", 69.4, 72.4, "We're going back up. Who is controlling this?"),
             ("SOUND FX", 72.6, 74.6, "Ominous low swell."),
             ("ACTION", 72.8, 78.0, "The display flickers to 43. The doors begin to open."),
         ]),
     ]},
]


def render_demo_episode(ep: dict) -> str:
    """One demo episode as a screenplay in the Kuku text format."""
    out = [f"EPISODE NUMBER: Episode {ep['num']}", f"EPISODE LOGLINE: {ep['logline']}",
           f"CONTINUITY NOTES: {ep['continuity']}", "", "--- SCRIPT START ---", ""]
    for i, (heading, t0, t1, blocks) in enumerate(ep["scenes"], 1):
        out += [f"SCENE {i:02d}", f"Scene heading: {heading}", f"Scene start: {fmt_ts(t0)}",
                f"Scene end: {fmt_ts(t1)}", "", "Actions and dialogue:", ""]
        for label, b0, b1, text in blocks:
            body = text if label in ("CAMERA", "ACTION", "SOUND FX") else f'"{text}"'
            out += [f"**{label}:**", f"Start: ({fmt_ts(b0)}) | End: ({fmt_ts(b1)})", body, ""]
    return "\n".join(out + ["--- SCRIPT END ---", ""])


def write_demo_show(dest: Path) -> Path:
    folder = dest / DEMO_NAME
    folder.mkdir(parents=True)
    for ep in DEMO_EPISODES:
        (folder / f"episode-{ep['num']:03d}-Episode {7000 + ep['num']}.txt").write_text(render_demo_episode(ep), encoding="utf-8")
    return folder


# ---------------------------------------------------------------- run helpers
def extract_zip(data: bytes, dest: Path, show_name: str) -> Path:
    """Unpack only the episode files (.txt scripts or transcripts, .srt, .vtt) into dest/<show_name>/,
    refusing path tricks and oversized archives."""
    folder = dest / show_name
    folder.mkdir(parents=True)
    root = folder.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(EPISODE_SUFFIXES)
                   and "__MACOSX" not in m.filename and not Path(m.filename).name.startswith(".")]
        if not members:
            raise ValueError("the zip has no episode files (.txt, .srt or .vtt)")
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_UNZIPPED:
            raise ValueError(f"the zip is too large (limit {MAX_FILES} files / {MAX_UNZIPPED // 2**20} MB unzipped)")
        for m in members:
            target = (folder / m.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"unsafe path in zip: {m.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(m))
    return folder


def parse_range(text: str) -> tuple[int, int] | None:
    text = text.strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", text)
    if not m:
        raise ValueError("Episodes must look like 5 or 1-10")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    return min(a, b), max(a, b)


@contextlib.contextmanager
def scoped_env(values: dict[str, str | None]) -> Iterator[None]:
    """Set (None = unset) env vars for one run, then restore them. Keys live only in memory, never on disk."""
    def apply(vals: dict[str, str | None]) -> None:
        for k, v in vals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    old = {k: os.environ.get(k) for k in values}
    try:
        apply(values)
        yield
    finally:
        apply(old)


@st.cache_resource
def _env_lock() -> threading.Lock:
    """One lock per server: runs that set credentials in os.environ must not interleave across sessions."""
    return threading.Lock()


def execute(mode: str, env: dict[str, str | None], opt: Options, zip_bytes: bytes | None, show_name: str,
            log: Callable[[object], None]) -> dict:
    """Run the pipeline in a temp folder (input, outputs and model cache) that is deleted on return."""
    with tempfile.TemporaryDirectory(prefix="thumbstop-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        folder = write_demo_show(work / "in") if zip_bytes is None else extract_zip(zip_bytes, work / "in", show_name)
        out = work / "out"
        with (_env_lock() if env else contextlib.nullcontext()), scoped_env(env):
            router = Router(load_config(), single={OFFLINE: "rules", CUSTOM: "custom"}.get(mode), out_dir=out)
            router.cache_dir = work / "cache"   # nothing derived from the scripts outlives the run
            same_family = mode != OFFLINE and router["annotate"].family == router["gate"].family
            run(folder, out, router, opt, log=log)
        return {"doc": json.loads((out / "promos.json").read_text(encoding="utf-8")), "same_family": same_family,
                "files": {name: (out / name).read_bytes() for name in OUTPUTS}}


# ---------------------------------------------------------------- UI
# Look: warm black and gold, liquid-glass panels over a soft light field. Theme colours and fonts are in
# .streamlit/config.toml; this sheet adds the glass, the hero and the result cards.
CSS = """
:root{--ts-gold:#c9a45a;--ts-gold-2:#e6c47e;--ts-ink:#f6f1e7;--ts-mut:#a39c91;--ts-dim:#776f64;--ts-ok:#7cc488;
--ts-warn:#e3b04f;--ts-bad:#f0735f;--ts-line:rgba(255,240,220,.10);--ts-ease:cubic-bezier(.2,.8,.2,1);
--ts-mono:"Geist Mono",ui-monospace,Menlo,monospace}
.stApp{background:radial-gradient(42vmax 32vmax at 8% 4%,rgba(214,170,88,.34),transparent 70%),
radial-gradient(38vmax 30vmax at 96% 22%,rgba(228,104,56,.26),transparent 70%),
radial-gradient(44vmax 34vmax at 30% 100%,rgba(170,98,38,.26),transparent 70%),#0c0a08;background-attachment:fixed}
[data-testid="stHeader"]{background:transparent}
[data-testid="stMainBlockContainer"]{max-width:1200px;padding-top:2.2rem;padding-bottom:4rem}
[data-testid="stSidebar"]{background:rgba(22,18,14,.55);-webkit-backdrop-filter:blur(28px) saturate(160%);backdrop-filter:blur(28px) saturate(160%);
box-shadow:inset -1px 0 0 rgba(255,240,220,.08)}
[data-testid="stSidebar"] > div{background:transparent}
/* liquid glass: translucent fill, blur, a bright rim where light catches the top edge */
.ts-glass,[data-testid="stExpander"] > details,.st-key-ts_input,.st-key-ts_table,.st-key-ts_downloads,[data-testid="stFileUploaderDropzone"]{
position:relative;background:linear-gradient(180deg,rgba(255,248,235,.085),rgba(255,248,235,.035))!important;
-webkit-backdrop-filter:blur(24px) saturate(170%);backdrop-filter:blur(24px) saturate(170%);border:1px solid rgba(255,240,220,.11)!important;
border-radius:22px!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.14),inset 0 -1px 0 rgba(0,0,0,.25),0 30px 60px -32px rgba(0,0,0,.85)}
.ts-glass::before{content:"";position:absolute;inset:0;border-radius:inherit;padding:1px;pointer-events:none;
background:linear-gradient(140deg,rgba(255,255,255,.38),rgba(255,255,255,.05) 32%,rgba(255,255,255,0) 55%,rgba(201,164,90,.32));
-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
.st-key-ts_input,.st-key-ts_table,.st-key-ts_downloads{padding:20px 22px}
[data-testid="stExpander"] > details{border-radius:18px!important;margin-bottom:4px}
[data-testid="stExpander"] summary{font-family:var(--ts-mono);font-size:13.5px}
[data-testid="stExpander"] summary:hover{color:var(--ts-gold-2)}
[data-testid="stFileUploaderDropzone"]{border-style:dashed!important}
/* buttons: step up 1px on hover, sink and shrink a touch on press */
button[data-testid^="stBaseButton"]{transition:transform 140ms var(--ts-ease),background 140ms var(--ts-ease),border-color 140ms var(--ts-ease),box-shadow 140ms var(--ts-ease)}
@media (hover:hover){button[data-testid^="stBaseButton"]:hover:not(:disabled){transform:translateY(-1px)}}
button[data-testid^="stBaseButton"]:active:not(:disabled){transform:translateY(1px) scale(.985);transition-duration:90ms}
button[data-testid="stBaseButton-primary"]{background:linear-gradient(180deg,#e6c47e,#c9a45a)!important;color:#1a1206!important;border:0!important;
font-weight:600;padding:.7rem 1.6rem;box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 10px 26px -10px rgba(201,164,90,.9)}
@media (hover:hover){button[data-testid="stBaseButton-primary"]:hover:not(:disabled){background:linear-gradient(180deg,#f0d28f,#d4b066)!important}}
button[data-testid="stBaseButton-primary"]:disabled{opacity:.4;box-shadow:none}
button[data-testid="stBaseButton-secondary"]{background:rgba(255,248,235,.07)!important;border:1px solid rgba(255,240,220,.14)!important;
box-shadow:inset 0 1px 0 rgba(255,255,255,.10)}
@media (hover:hover){button[data-testid="stBaseButton-secondary"]:hover:not(:disabled){background:rgba(255,248,235,.13)!important;color:var(--ts-ink)!important}}
/* the source picker reads as a glass segmented control */
.st-key-ts_input [role="radiogroup"]{display:inline-flex;gap:4px;padding:4px;border-radius:999px;background:rgba(0,0,0,.3);box-shadow:inset 0 1px 2px rgba(0,0,0,.45)}
.st-key-ts_input [role="radiogroup"] label{margin:0!important;padding:7px 14px;border-radius:999px;transition:background 140ms var(--ts-ease)}
.st-key-ts_input [role="radiogroup"] label:has(input:checked){background:rgba(255,248,235,.14);box-shadow:inset 0 1px 0 rgba(255,255,255,.14)}
.st-key-ts_input [role="radiogroup"] label > div:first-child{display:none}
[data-testid="stDataFrame"]{border-radius:14px;overflow:hidden}
[data-testid="stAlert"]{border-radius:16px}
/* hero */
.ts-hero{padding:8px 0 10px}
.ts-brand{display:flex;align-items:center;gap:10px;font-size:14px;color:var(--ts-mut)}
.ts-brand b{color:var(--ts-ink);font-weight:600;font-size:15px;letter-spacing:-.01em}
.ts-logo{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;
background:radial-gradient(circle at 30% 25%,#e6c47e,#c9a45a 55%,#8a6a2c);box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 6px 18px -6px rgba(201,164,90,.7)}
.ts-logo i{width:10px;height:12px;margin-left:2px;background:#1a1206;clip-path:polygon(0 0,100% 50%,0 100%)}
.ts-h1{font-size:clamp(34px,5.4vw,64px)!important;line-height:1.02!important;letter-spacing:-.045em!important;font-weight:500!important;
margin:22px 0 14px!important;padding:0!important;max-width:15ch;color:var(--ts-ink)}
.ts-h1 em{font-style:normal;background:linear-gradient(180deg,#f3d99c,#c9a45a);-webkit-background-clip:text;background-clip:text;color:transparent}
.ts-lede{font-size:17px;line-height:1.6;color:var(--ts-mut);max-width:60ch;margin:0}
.ts-facts{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0 26px}
.ts-chip{display:inline-flex;align-items:center;gap:7px;font-size:13px;padding:6px 12px;border-radius:999px;color:var(--ts-ink);
background:rgba(255,248,235,.07);border:1px solid var(--ts-line);box-shadow:inset 0 1px 0 rgba(255,255,255,.08)}
.ts-chip i{width:6px;height:6px;border-radius:50%;background:var(--ts-gold);box-shadow:0 0 10px var(--ts-gold)}
.ts-flow{display:grid;grid-template-columns:repeat(5,1fr);gap:0;padding:22px 10px;margin-bottom:8px}
.ts-step{position:relative;padding:0 14px}
.ts-step .n{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font:500 12px var(--ts-mono);color:#1a1206;position:relative;z-index:1;
background:linear-gradient(180deg,#e6c47e,#c9a45a);box-shadow:inset 0 1px 0 rgba(255,255,255,.5),0 0 0 5px rgba(12,10,8,.65)}
.ts-step.ai .n{background:rgba(255,248,235,.12);color:var(--ts-gold-2);box-shadow:inset 0 0 0 1px rgba(201,164,90,.6),0 0 0 5px rgba(12,10,8,.65)}
.ts-step b{display:block;margin:12px 0 4px;font-weight:500;font-size:15px;color:var(--ts-ink)}
.ts-step span{font-size:13px;line-height:1.45;color:var(--ts-mut);display:block}
.ts-step small{display:inline-block;margin-top:8px;font:500 10.5px var(--ts-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--ts-dim)}
.ts-step.ai small{color:var(--ts-gold)}
.ts-flow .rail{position:absolute;left:44px;right:44px;top:37px;height:1px;background:linear-gradient(90deg,rgba(201,164,90,.6),rgba(201,164,90,.15));pointer-events:none}
.ts-flow .rail i{position:absolute;top:-2px;left:0;width:40px;height:5px;border-radius:9px;
background:radial-gradient(closest-side,#f3d99c,rgba(201,164,90,0));animation:ts-run 3.2s linear 3}
@media (hover:hover){.ts-flow:hover .rail i{animation:ts-run2 3.2s linear 3}}
@keyframes ts-run{from{left:0}to{left:calc(100% - 40px)}} @keyframes ts-run2{from{left:0}to{left:calc(100% - 40px)}}
/* section headings and results */
.ts-sec{margin:34px 0 12px}
.ts-sec small{display:block;font:500 11px var(--ts-mono);letter-spacing:.1em;text-transform:uppercase;color:var(--ts-gold)}
.ts-sec h2{font-size:28px!important;font-weight:500!important;letter-spacing:-.03em!important;margin:6px 0 0!important;padding:0!important;color:var(--ts-ink)}
.ts-sec p{margin:6px 0 0;color:var(--ts-mut);font-size:14px}
.ts-stats{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:22px 0 6px}
.ts-stat{padding:16px 18px;border-radius:18px}
.ts-stat b{display:block;font-size:26px;font-weight:500;letter-spacing:-.03em;line-height:1.15;color:var(--ts-ink)}
.ts-stat span{font-size:12px;color:var(--ts-mut)}
.ts-meta{font:12px var(--ts-mono);color:var(--ts-dim);margin:2px 0 0}
.ts-picks{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.ts-pick{padding:20px 22px 22px;display:flex;flex-direction:column;gap:12px}
.ts-pick .top{display:flex;align-items:baseline;gap:12px}
.ts-rank{font-size:44px;line-height:.9;font-weight:500;letter-spacing:-.05em;color:var(--ts-gold-2);font-feature-settings:"tnum"}
.ts-rank small{font-size:20px;color:var(--ts-gold);opacity:.7}
.ts-id{font:12px var(--ts-mono);color:var(--ts-mut)}
.ts-segs{display:flex;flex-wrap:wrap;gap:6px}
.ts-tc{font:12px var(--ts-mono);padding:4px 9px;border-radius:999px;background:rgba(0,0,0,.28);border:1px solid var(--ts-line);color:var(--ts-ink)}
.ts-q{padding:12px 14px;border-radius:14px;background:rgba(0,0,0,.24);border:1px solid var(--ts-line)}
.ts-q.cliff{background:linear-gradient(180deg,rgba(201,164,90,.13),rgba(201,164,90,.04));border-color:rgba(201,164,90,.26)}
.ts-q p{margin:0;font-size:15px;line-height:1.45;color:var(--ts-ink)}
.ts-lbl{display:block;font:500 10.5px var(--ts-mono);letter-spacing:.09em;text-transform:uppercase;color:var(--ts-mut);margin-bottom:4px}
.ts-why{font-size:13.5px;color:var(--ts-mut);margin:0}
.ts-why b{color:var(--ts-gold-2);font-weight:500}
.ts-film{display:flex;gap:4px;margin:4px 0 14px;height:46px}
.ts-film div{min-width:70px;border-radius:10px;padding:6px 10px;display:flex;flex-direction:column;justify-content:center;
background:linear-gradient(180deg,rgba(230,196,126,.95),rgba(201,164,90,.9));color:#1a1206;box-shadow:inset 0 1px 0 rgba(255,255,255,.5)}
.ts-film b{font:600 12px var(--ts-mono)} .ts-film span{font:11px var(--ts-mono);opacity:.75}
.ts-cut{display:grid;gap:10px}
.ts-segc{padding:14px 16px;border-radius:14px;background:rgba(0,0,0,.22);border:1px solid var(--ts-line);font-size:14px}
.ts-segc header{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font:12.5px var(--ts-mono);color:var(--ts-mut);margin-bottom:8px}
.ts-segc header b{color:var(--ts-ink);font-weight:500}
.ts-line{margin:4px 0;color:var(--ts-ink);line-height:1.45}
.ts-line em{font:normal 600 10.5px var(--ts-mono);letter-spacing:.08em;color:var(--ts-gold);margin-right:8px}
.ts-high{color:var(--ts-ok)!important} .ts-medium{color:var(--ts-warn)!important} .ts-low{color:var(--ts-bad)!important}
.ts-flag{margin:10px 0 0;padding:10px 14px;border-radius:12px;font-size:13px;color:#f3d9a0;background:rgba(227,176,79,.10);border:1px solid rgba(227,176,79,.28)}
.ts-ask{margin:0 0 4px;font-size:15px;color:var(--ts-ink)} .ts-rr{margin:0 0 12px;font-size:13px;color:var(--ts-dim)}
.ts-pol{margin:10px 0 0;font-size:12.5px;color:var(--ts-mut)}
.ts-foot{margin-top:40px;padding-top:16px;border-top:1px solid var(--ts-line);font-size:12.5px;color:var(--ts-dim)}
@media (max-width:560px){.ts-brand span:last-child{display:none}}
@media (max-width:900px){.ts-flow{grid-template-columns:1fr;gap:18px;padding:20px}.ts-flow .rail{display:none}.ts-stats{grid-template-columns:repeat(2,1fr)}}
@media (prefers-reduced-motion:reduce){.ts-flow .rail i{animation:none;display:none}button[data-testid^="stBaseButton"]{transition:none}}
@media (prefers-reduced-transparency:reduce){.ts-glass,[data-testid="stExpander"] > details,.st-key-ts_input,.st-key-ts_table,.st-key-ts_downloads{background:#1a1612!important;backdrop-filter:none}}
"""

LOGO = "<i></i>"   # a play mark drawn in CSS (st.html strips inline SVG)

HERO = f"""
<section class="ts-hero">
 <div class="ts-brand"><span class="ts-logo">{LOGO}</span><b>Thumbstop</b><span>· promo finder for microdrama scripts</span></div>
 <h1 class="ts-h1">Find the promo <em>hiding</em> in every episode.</h1>
 <p class="ts-lede">Give it a season of timestamped scripts and it gives back 20 to 60 second promos that open on a hook in the
 first 3 seconds, end on a cliffhanger, and still make sense to someone who has never seen the show, each with a cut
 sheet an editor can use straight away.</p>
 <div class="ts-facts"><span class="ts-chip"><i></i>Hook inside 3 s</span><span class="ts-chip"><i></i>20 to 60 s cuts</span>
  <span class="ts-chip"><i></i>Never cuts a line in half</span><span class="ts-chip"><i></i>Checked by a cold viewer</span>
  <span class="ts-chip"><i></i>About $2.70 for a 50-episode show</span></div>
 <div class="ts-flow ts-glass"><div class="rail"><i></i></div>
  <div class="ts-step"><div class="n">1</div><b>Read</b><span>Every line of every script becomes a numbered beat with its timestamps.</span><small>code</small></div>
  <div class="ts-step ai"><div class="n">2</div><b>Mark</b><span>AI points at the beats worth opening on and the ones worth ending on.</span><small>AI</small></div>
  <div class="ts-step"><div class="n">3</div><b>Cut</b><span>Every pairing is tried and checked against the rules. AI never writes a timestamp.</span><small>code</small></div>
  <div class="ts-step ai"><div class="n">4</div><b>Check</b><span>A different model watches each clip cold and says if a stranger would follow it.</span><small>AI</small></div>
  <div class="ts-step"><div class="n">5</div><b>Rank</b><span>Best first, with cut sheets, an EDL for Premiere and what to post first.</span><small>code</small></div>
 </div>
</section>"""


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text))


def section(eyebrow: str, title: str, note: str = "") -> None:
    st.html(f'<div class="ts-sec"><small>{esc(eyebrow)}</small><h2>{esc(title)}</h2>'
            + (f"<p>{esc(note)}</p>" if note else "") + "</div>")


def seg_label(rec: dict) -> str:
    return " + ".join(f"E{s['episode']} {s['in_ts']}-{s['out_ts']}" for s in rec["segments"])


def cold_viewer(rec: dict) -> str:
    cv = rec.get("cold_viewer") or {}
    return {True: "pass", False: "fail"}.get(cv.get("passed"), "not checked" if not cv else "error")


def pick_card(r: dict, reason: str) -> str:
    segs = "".join(f'<span class="ts-tc">E{s["episode"]} {s["in_ts"]} → {s["out_ts"]}</span>' for s in r["segments"])
    return f"""<article class="ts-pick ts-glass">
 <div class="top"><span class="ts-rank"><small>#</small>{r['rank']}</span><span class="ts-id">{esc(r['id'])} · {r['duration_s']:.0f}s</span></div>
 <div class="ts-segs">{segs}</div>
 <div class="ts-q"><span class="ts-lbl">Hook</span><p>{esc(r['hook'])}</p></div>
 <div class="ts-q cliff"><span class="ts-lbl">Cliffhanger</span><p>{esc(r['cliffhanger'])}</p></div>
 <p class="ts-why"><b>Why first:</b> {esc(reason)}</p></article>"""


def cut_sheet_html(r: dict) -> str:
    segs = r["cut_sheet"]["segments"]
    film = "".join(f'<div style="flex:{max(s["dur_s"], 1):.2f}"><b>E{s["episode"]} · {s["dur_s"]:.1f}s</b>'
                   f'<span>{s["in_ts"]} → {s["out_ts"]}</span></div>' for s in segs)
    blocks = []
    for s in segs:
        lines = [f'<p class="ts-line"><em>IN ON</em>{esc(s["in_on"])}</p>', f'<p class="ts-line"><em>OUT ON</em>{esc(s["out_on"])}</p>']
        if s["stop_before"]:
            lines.append(f'<p class="ts-line"><em>STOP BEFORE</em>{esc(s["stop_before"])}</p>')
        blocks.append(
            f'<div class="ts-segc"><header><b>Segment {s["seg"]}</b><span>{s["reel"]}</span><span>{s["in_tc"]} → {s["out_tc"]}</span>'
            f'<span>{s["dur_s"]:.1f}s</span><span>cut confidence <b class="ts-{s["cut_confidence"]}">{s["cut_confidence"]}</b> '
            f'(gap in {s["gap_in_s"]}s, out {s["gap_out_s"]}s)</span></header>{"".join(lines)}</div>')
    flags = "".join(f'<p class="ts-flag">&#9888; {esc(f)}</p>' for f in r.get("flags") or [])
    evidence = (r.get("policy") or {}).get("evidence") or []
    pol = ('<p class="ts-pol">Ad policy: ' + "; ".join(f"{esc(e['level'])} {esc(e['rule'])} ({esc(e['beat'])})" for e in evidence)
           + "</p>") if evidence else ""
    return (f'<p class="ts-ask"><span class="ts-lbl">Open question</span>{esc(r["open_question"])}</p>'
            f'<p class="ts-rr">{esc(r["rank_reason"])}</p><div class="ts-film">{film}</div>'
            f'<div class="ts-cut">{"".join(blocks)}</div>{flags}{pol}')


def sidebar() -> tuple[str, dict[str, str | None], Options] | None:
    """Model + advanced settings. Returns None (after showing why) when the settings are not usable."""
    sb = st.sidebar
    sb.html(f'<div class="ts-brand"><span class="ts-logo">{LOGO}</span><b>Thumbstop</b></div>')
    sb.header("Model")
    mode = sb.radio("Model", [OFFLINE, BEDROCK, CUSTOM], label_visibility="collapsed", key="mode")
    env: dict[str, str | None] = {}
    if mode == BEDROCK:
        roles = load_config().get("roles", {})
        sb.caption("Uses the roles in config.yaml: " + ", ".join(f"{r}={p}" for r, p in roles.items()))
        env["AWS_REGION"] = sb.text_input("AWS region", value=os.environ.get("AWS_REGION", "us-east-1"),
                                          key="aws_region").strip() or "us-east-1"
        with sb.expander("Access keys (optional)"):
            st.caption("Leave blank to use this machine's AWS login. Keys stay in this session's memory only.")
            key_id = st.text_input("Access key ID", type="password", key="aws_key_id").strip()
            secret = st.text_input("Secret access key", type="password", key="aws_secret").strip()
            token = st.text_input("Session token", type="password", key="aws_token").strip()
        if key_id and secret:
            env |= {"AWS_ACCESS_KEY_ID": key_id, "AWS_SECRET_ACCESS_KEY": secret, "AWS_SESSION_TOKEN": token or None,
                    "AWS_PROFILE": None}
    elif mode == CUSTOM:
        base_url = sb.text_input("Base URL", placeholder="https://api.example.com/v1", key="llm_base_url").strip()
        model = sb.text_input("Model", placeholder="model-id", key="llm_model").strip()
        api_key = sb.text_input("API key", type="password", key="llm_api_key",
                                help="Kept in this browser session only and passed to the run in memory.").strip()
        sb.caption("One model plays every role, so the cold-viewer check is less independent.")
        env = {"LLM_BASE_URL": base_url, "LLM_MODEL": model, "LLM_API_KEY": api_key or "none"}
    else:
        sb.caption("Rules only: no AI and no key, so anyone can try it. Pick a model above for the real thing.")
    with sb.expander("Advanced"):
        top = st.number_input("Top N promos", min_value=1, max_value=50, value=20, key="top")
        lo, hi = st.slider("Promo length (s)", min_value=10, max_value=90, value=(20, 60), key="length")
        ep_text = st.text_input("Episodes", placeholder="all, or e.g. 1-10", key="episodes")
        fast = st.checkbox("Fast mode", key="fast", help="No setup repair and no proposals; every shown promo is still cold-checked.")
    try:
        episodes = parse_range(ep_text)
    except ValueError as e:
        sb.error(str(e))
        return None
    if mode == CUSTOM and not (env["LLM_BASE_URL"] and env["LLM_MODEL"]):
        sb.warning("Enter the base URL and model to use this option.")
        return None
    opt = Options(top=int(top), gate_n=max(12, int(top) + 4) if fast else max(40, int(top) + 4), repair=not fast, episodes=episodes,
                  min_len=float(lo), max_len=float(hi), seeds=not fast)
    return mode, env, opt


def show_results(res: dict) -> None:
    doc = res["doc"]
    recs, meta = doc["promos"], doc["meta"]
    u = meta.get("usage", {})
    passed = sum(cold_viewer(r) == "pass" for r in recs)
    picks = doc.get("post_first", [])
    stats = [(len(recs), "Promos ranked"), (len(picks), "To post first"), (f"{passed}/{len(recs)}", "Cold viewer passed"),
             (u.get("calls", 0), f"Model calls (+{u.get('cache_hits', 0)} cached)"), (f"${u.get('usd', 0):.2f}", "Estimated cost"),
             (f"{meta.get('seconds', 0):.0f}s", "Run time")]
    section("Results", doc["show"])
    st.html('<div class="ts-stats">' + "".join(f'<div class="ts-stat ts-glass"><b>{esc(v)}</b><span>{esc(k)}</span></div>'
                                               for v, k in stats) + f'</div><p class="ts-meta">models: {esc(meta.get("models"))}</p>')
    if res.get("same_family"):
        st.info("The annotator and the cold viewer are the same model family, so the cold-viewer check is less "
                "independent.")
    if not recs:
        st.warning("No clip passed the hard rules (length, hook in 3 s, cliffhanger at the end, no mid-line cuts). "
                   "Try a wider length range or more episodes.")
        return
    by_id = {r["id"]: r for r in recs}

    section("Start here", "Post these first", "Early episodes, different hook types, no policy hold and no spoilers.")
    if not picks:
        st.info("No promo qualified to post first (all are late-season, on policy hold, or failed the cold viewer).")
    else:
        st.html('<div class="ts-picks">' + "".join(pick_card(by_id[p["id"]], p["reason"]) for p in picks) + "</div>")

    section("Everything", "Ranked promos", "Best first. Scroll sideways for the reason each one works.")
    with st.container(key="ts_table"):
        st.dataframe([{
            "Rank": r["rank"], "Segments": seg_label(r), "Length (s)": r["duration_s"], "Hook": r["hook"],
            "Cliffhanger": r["cliffhanger"], "Why it works": r["why_it_works"], "Cold viewer": cold_viewer(r),
            "Policy": (r.get("policy") or {}).get("status", ""),
        } for r in recs], hide_index=True, key="ranked_table", column_config={
            "Rank": st.column_config.NumberColumn(width="small"),
            "Length (s)": st.column_config.NumberColumn(format="%.1f", width="small"),
            "Hook": st.column_config.TextColumn(width="medium"),
            "Cliffhanger": st.column_config.TextColumn(width="medium"),
            "Why it works": st.column_config.TextColumn(width="medium"),
        })

    section("For the editor", "Cut sheets", "Timecodes at 30 fps from the script timestamps, so confirm them on picture.")
    for r in recs:
        with st.expander(f"#{r['rank']}  {r['id']}  ·  {r['duration_s']:.0f}s  ·  {seg_label(r)}", expanded=r["rank"] == 1):
            st.html(cut_sheet_html(r))
            st.code("\n".join(r["cut_sheet"]["paper_edit"]), language=None)

    section("Take it with you", "Downloads", "The EDL drops every pick into Premiere or Resolve; review.html is the editor's sign-off page.")
    with st.container(key="ts_downloads"):
        for col, (name, mime) in zip(st.columns(len(OUTPUTS)), OUTPUTS.items()):
            col.download_button(name, res["files"][name], file_name=name, mime=mime, on_click="ignore", key=f"dl_{name}",
                                width="stretch")


def main() -> None:
    st.set_page_config(page_title="Thumbstop", page_icon="🎬", layout="wide")
    st.html(f"<style>{CSS}</style>")
    st.html(HERO)
    settings = sidebar()

    section("Try it", "Pick the scripts", "The demo is a short original show, so it runs in seconds with no key.")
    with st.container(key="ts_input"):
        source = st.radio("Scripts", [DEMO, UPLOAD], horizontal=True, key="source", label_visibility="collapsed")
        upload = None
        if source == UPLOAD:
            upload = st.file_uploader("Episode files (.zip of .txt screenplays, .srt or .vtt)", type=["zip"], key="upload")
        ready = settings is not None and (source == DEMO or upload is not None)
        clicked = st.button("Find promos", type="primary", disabled=not ready, key="run")
    # ?demo=1 runs the demo once on arrival, so a shared link opens straight onto results
    auto = (st.query_params.get("demo") == "1" and source == DEMO and ready
            and not st.session_state.get("auto_done") and "result" not in st.session_state)
    if auto:
        st.session_state["auto_done"] = True
    if clicked or auto:
        assert settings is not None
        mode, env, opt = settings
        st.session_state.pop("result", None)
        name = DEMO_NAME
        if upload is not None:
            name = re.sub(r"[^\w\- ]+", "_", Path(upload.name).stem)[:60].strip() or "show"
        with st.status("Running ...", expanded=True) as status:
            box = st.empty()
            lines: list[str] = []

            def log(msg: object) -> None:
                lines.extend(str(msg).splitlines())
                box.code("\n".join(lines[-14:]), language=None)

            try:
                st.session_state["result"] = execute(mode, env, opt, upload.getvalue() if upload else None, name, log)
                status.update(label="Done", state="complete", expanded=False)
            except (Exception, SystemExit) as e:   # pipeline exits with SystemExit on an empty episode range
                status.update(label="Run failed", state="error", expanded=True)
                st.error(f"{type(e).__name__}: {str(e)[:500]}")
    if "result" in st.session_state:
        show_results(st.session_state["result"])

    st.html('<p class="ts-foot">Uploaded scripts are processed in memory and a temporary folder that is deleted as soon as '
            "the run ends, so nothing is stored. API keys stay in this browser session and are never written to disk.</p>")


if __name__ == "__main__":
    main()
