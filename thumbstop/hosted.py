"""Everything the hosted app does except drawing the page: the built-in demo show, safe zip upload, the no-key
recorded mode, and one pipeline run in a throwaway folder. server.py serves it; record_demo.py records the demo.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
import re
import tempfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

from .llm import Budget, BudgetExceeded, Router, load_config
from .models import fmt_ts
from .pipeline import Options, run

ROOT = Path(__file__).resolve().parent.parent
RECORDED, OFFLINE, BEDROCK, CUSTOM = "recorded", "offline", "bedrock", "custom"
MODES = {
    RECORDED: ("Recorded AI run", "Claude Opus 4.6 + GPT-OSS-120B, replayed, no key"),
    OFFLINE: ("Offline rules", "No AI and no key, simple keyword rules"),
    BEDROCK: ("AWS Bedrock", "Your AWS keys, every model call live"),
    CUSTOM: ("OpenAI-compatible API", "Any base URL, model and key"),
}
OUTPUTS = {"promos.csv": "text/csv", "promos.json": "application/json", "selects.edl": "text/plain",
           "review.html": "text/html"}
MAX_FILES, MAX_UNZIPPED = 1000, 200 * 1024 * 1024
EPISODE_SUFFIXES = (".txt", ".srt", ".vtt")   # same formats the CLI reads
DEMO_ANSWERS = ROOT / "demo_answers.json"   # written by record_demo.py
# Optional extra recorded answers kept outside the repo (on the hosted server: the assignment's input pack).
# Each answer is looked up by a hash of its prompt, which contains the script text, so only someone who
# uploads those exact scripts can replay them.
EXTRA_ANSWERS = os.environ.get("THUMBSTOP_RECORDED_ANSWERS", "")
ENV_LOCK = threading.Lock()   # runs that set credentials in os.environ must not interleave


class NotRecorded(ValueError):
    """The recorded mode met a question it has no answer for, and it never makes a live call."""


def has_extra_answers() -> bool:
    return bool(EXTRA_ANSWERS) and Path(EXTRA_ANSWERS).is_file()


def recorded_answers() -> dict[str, str]:
    """The demo answers plus the optional extra file (.json or .json.gz)."""
    answers = json.loads(DEMO_ANSWERS.read_text(encoding="utf-8")) if DEMO_ANSWERS.exists() else {}
    if has_extra_answers():
        opener = gzip.open if EXTRA_ANSWERS.endswith(".gz") else open
        with opener(EXTRA_ANSWERS, "rt", encoding="utf-8") as f:
            answers |= json.load(f)
    return answers


def recorded_options() -> Options:
    """The settings the recorded answers were made with (the defaults); other settings would ask new questions."""
    return Options(top=20, gate_n=40, repair=True, episodes=None, min_len=20.0, max_len=60.0, seeds=True)


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


def episode_files(data: bytes) -> int:
    """Check an uploaded zip without unpacking it: how many episode files it holds (raises ValueError if unusable)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("that file is not a zip") from e
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir() and m.filename.lower().endswith(EPISODE_SUFFIXES)
                   and "__MACOSX" not in m.filename and not Path(m.filename).name.startswith(".")]
    if not members:
        raise ValueError("the zip has no episode files (.txt, .srt or .vtt)")
    if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_UNZIPPED:
        raise ValueError(f"the zip is too large (limit {MAX_FILES} files / {MAX_UNZIPPED // 2**20} MB unzipped)")
    return len(members)


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


def execute(mode: str, env: dict[str, str | None], opt: Options, zip_bytes: bytes | None, show_name: str,
            log: Callable[[object], None]) -> dict:
    """Run the pipeline in a temp folder (input, outputs and model cache) that is deleted on return."""
    with tempfile.TemporaryDirectory(prefix="thumbstop-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        folder = write_demo_show(work / "in") if zip_bytes is None else extract_zip(zip_bytes, work / "in", show_name)
        out = work / "out"
        with (ENV_LOCK if env else contextlib.nullcontext()), scoped_env(env):
            budget = Budget(max_calls=0, max_usd=0.0) if mode == RECORDED else None   # replay only, never a live call
            router = Router(load_config(), single={OFFLINE: "rules", CUSTOM: "custom"}.get(mode), out_dir=out, budget=budget)
            router.cache_dir = work / "cache"   # nothing derived from the scripts outlives the run
            if mode == RECORDED:
                import diskcache

                with diskcache.Cache(str(router.cache_dir)) as cache:
                    for k, v in recorded_answers().items():
                        cache.set(k, v)
            same_family = mode != OFFLINE and router["annotate"].family == router["gate"].family
            try:
                run(folder, out, router, opt, log=log)
            except BudgetExceeded as e:
                if mode != RECORDED:
                    raise
                raise NotRecorded(
                    "There is no recorded AI run for these scripts, and this mode never calls a model live. "
                    "Pick Offline rules to try them with no key, or AWS Bedrock or another API with your own key.") from e
        doc = json.loads((out / "promos.json").read_text(encoding="utf-8"))
        view = json.loads((out / "review.json").read_text(encoding="utf-8"))
        return {"doc": doc, "view": view, "same_family": same_family,
                "files": {name: (out / name).read_bytes() for name in OUTPUTS}}


TEASER = ROOT / "thumbstop" / "web" / "teaser.json"


def write_teaser() -> None:
    """The hero's looping preview: the top promo of the demo show (original fiction), from the recorded answers."""
    res = execute(RECORDED, {}, recorded_options(), None, DEMO_NAME, lambda m: None)
    view = res["view"]
    TEASER.write_text(json.dumps({"show": view["show"], "promo": view["promos"][0]}, ensure_ascii=False) + "\n",
                      encoding="utf-8")
