"""Shared fixtures: a tiny ORIGINAL two-episode office-revenge microdrama ("The Forty-Second Floor").
All names and plot are invented for these tests.

Episodes are kept as compact beat specs and rendered into the Kuku screenplay format at test time,
so this source file never contains the format's own boilerplate runs (which the leak check would flag)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Union

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thumbstop.models import Show, fmt_ts  # noqa: E402
from thumbstop.parse import parse_show  # noqa: E402

Time = Union[float, str, None]          # float -> MM:SS.ss; str -> written verbatim; None -> line omitted
Block = Union[tuple[str, Time, Time, str], str]   # (label, start, end, text) or a raw line
SceneSpec = tuple[str, float, float, list[Block]]


def render_episode(num: int, scenes: list[SceneSpec], logline: str = "An auditor finds a second set of books.",
                   continuity: str = "None.", upper: bool = False) -> str:
    """Write one episode in the screenplay format (upper=True uses SCENE HEADING:/SCENE START: field names)."""
    def ts(x: Time) -> str:
        return x if isinstance(x, str) else fmt_ts(x or 0.0)

    fields = [f.upper() if upper else f for f in ("Scene heading", "Scene start", "Scene end")]
    out = [f"EPISODE NUMBER: Episode {num}", f"EPISODE LOGLINE: {logline}", f"CONTINUITY NOTES: {continuity}", "",
           "--- SCRIPT START ---", ""]
    for n, (heading, t0, t1, blocks) in enumerate(scenes, 1):
        out += [f"SCENE {n:02d}", f"{fields[0]}: {heading}", f"{fields[1]}: {ts(t0)}", f"{fields[2]}: {ts(t1)}", "",
                "Actions and dialogue:", ""]
        for b in blocks:
            if isinstance(b, str):
                out += [b, ""]
                continue
            label, b0, b1, text = b
            out.append(f"**{label}:**")
            if b0 is not None:
                out.append(f"Start: ({ts(b0)}) | End: ({ts(b1)})")
            out += [text, ""]
    out.append("--- SCRIPT END ---")
    return "\n".join(out) + "\n"


EP1_SCENES: list[SceneSpec] = [
    ("INT. GLASS BOARDROOM – DAY", 0.0, 48.0, [
        ("CAMERA", 0.0, 4.0, "Slow push along a long table of executives toward NADIA at the far end."),
        ("ACTION", 0.5, 3.0, "Grant slams a red folder onto the table in front of Nadia."),
        ("GRANT ELLERBY (Smug, Mocking)", 3.2, 6.8, '"Forty million dollars, gone. And your badge opened the vault."'),
        ("NADIA VOSS (Shocked)", 7.0, 9.4, '"That\'s impossible. I was at my desk all night."'),
        ("CELESTE ELLERBY (Cold, Measured)", 9.8, 14.0,
         '"Then explain the footage, Miss Voss. [taps the screen] Take your time."'),
        ("SOUND FX", 13.5, 15.0, "A low electronic hum as the wall screen flickers on."),
        ("ACTION", 14.2, 18.0, "On the screen, a hooded figure swipes a badge at the vault door."),
        ("NADIA VOSS (Desperate)", 18.4, 22.0, '"That isn\'t me. Look at the hands, she\'s wearing gloves!"'),
        ("GRANT ELLERBY (Furious)", 22.3, 25.0, '"Enough excuses! Security, take her badge!"'),
        ("ACTION", 25.2, 28.0, "Nadia stands so fast her chair crashes to the floor."),
        ("NADIA VOSS (Defiant)", 28.2, 32.5, '"You want a thief? Check whose card printed that footage."'),
        ("CELESTE ELLERBY (Icy)", 33.0, 36.0, '"You have until morning to return the money."'),
        ("NADIA VOSS (Quiet, Shaken)", 36.4, 39.0, '"And if I can\'t?"'),
        ("CELESTE ELLERBY (Menacing)", 39.5, 44.0,
         '"Then you will leave this tower in handcuffs. Tomorrow, before the board."'),
        ("SOUND FX", 44.0, 47.0, "An ominous sting as the boardroom doors swing shut."),
    ]),
    ("INT. ELEVATOR LOBBY – NIGHT", 48.0, 92.0, [
        ("CAMERA", 48.0, 52.0, "Wide on the empty lobby, city lights behind the glass."),
        ("ACTION", 48.5, 51.5, "Nadia waits alone, clutching a cardboard box of her things."),
        ("OWEN PIKE (O.S.) (Whispering)", 52.0, 55.0, '"Don\'t turn around. They\'re watching the cameras."'),
        ("NADIA VOSS (Startled)", 55.3, 57.0, '"Owen? What are you doing here?"'),
        ("OWEN PIKE (Urgent)", 57.4, 63.0,
         '"The vault log was edited at two a.m. From an executive terminal. [glances at the ceiling] Not yours."'),
        ("NADIA VOSS (Hopeful)", 63.4, 65.5, '"Whose terminal?"'),
        ("ACTION", 65.8, 68.0, "The elevator chimes. Owen steps back into the shadows."),
        ("OWEN PIKE (Tense)", 68.2, 72.0, '"Meet me in the parking garage at midnight. Come alone."'),
        ("ACTION", 72.5, 75.0, "The elevator doors open. Grant is standing inside, smiling."),
        ("GRANT ELLERBY (Gloating)", 75.3, 79.0, '"Going somewhere, Nadia? Get in. We need to talk."'),
        ("ACTION", 79.4, 82.0, "Nadia steps in. Grant presses the button for the basement."),
        ("GRANT ELLERBY (Threatening)", 82.5, 87.0, '"I know who you met tonight. And I know what he told you."'),
        ("NADIA VOSS (Horrified)", 87.4, 90.0, '"What did you do to him?"'),
        ("SOUND FX", 89.5, 92.0, "Music swells as the elevator lights cut out."),
    ]),
]

EP2_SCENES: list[SceneSpec] = [
    ("INT. ELEVATOR – NIGHT", 0.0, 46.0, [
        ("CAMERA", 0.0, 3.0, "Emergency red light floods the elevator car."),
        ("GRANT ELLERBY (Cold)", 0.5, 4.0, '"Relax. I didn\'t touch your guard. Yet."'),
        ("NADIA VOSS (Furious)", 4.3, 7.0, '"If anything happens to him, I will end you!"'),
        ("ACTION", 7.2, 10.0, "Grant grabs her wrist and pins it to the elevator wall."),
        ("GRANT ELLERBY (Venomous)", 10.3, 15.0,
         '"You signed a confession this afternoon. [scoffs] You just don\'t remember it."'),
        ("NADIA VOSS (Shocked)", 15.4, 17.5, '"I signed nothing."'),
        ("ACTION", 17.8, 21.0, "He holds up a page. Her signature sits at the bottom, perfectly forged."),
        ("GRANT ELLERBY (Smug)", 21.3, 26.0, '"Sign one more page, quit quietly, and the confession disappears."'),
        ("NADIA VOSS (Defiant)", 26.4, 29.0, '"And if I refuse?"'),
        ("GRANT ELLERBY (Menacing)", 29.4, 34.0,
         '"Then the police get it at nine. Your mother gets a call at nine-oh-one."'),
        ("SOUND FX", 34.0, 36.0, "Ominous sting."),
        ("NADIA VOSS (Steady)", 36.4, 40.0, '"Give me the pen."'),
        ("ACTION", 40.3, 46.0, "She takes the pen and jams it into the emergency panel. Sparks fly. The doors grind open."),
    ]),
    ("INT. PARKING GARAGE – NIGHT", 46.0, 88.0, [
        ("ACTION", 46.5, 50.0, "Nadia sprints between parked cars, breathing hard."),
        ("NADIA VOSS (V.O.) (Breathless)", 50.2, 54.0, '"Midnight. Owen said midnight. Please be here."'),
        ("ACTION", 54.4, 58.0, "She finds a security cap lying on the concrete beside an open car door."),
        ("MYSTERY CALLER (Distorted Voice) (Calm)", 58.5, 63.0, '"Looking for your friend, Miss Voss? He talks too much."'),
        ("NADIA VOSS (Shouting)", 63.3, 66.0, '"Who is this? Where is he?"'),
        ("MYSTERY CALLER (Distorted Voice) (Calm)", 66.4, 72.0,
         '"Bring the original footage to the roof. Alone. Or he never leaves this building."'),
        ("ACTION", 72.3, 76.0, "Headlights snap on behind her. An engine roars."),
        ("NADIA VOSS (Terrified)", 76.3, 78.5, '"No, no, no!"'),
        ("ACTION", 78.8, 82.0, "The car lunges straight at her."),
        ("SOUND FX", 81.5, 84.0, "Tyres screech, then a hard cut to black."),
    ]),
]

EP1 = render_episode(
    1, EP1_SCENES,
    logline="A junior analyst is publicly blamed for forty missing millions on the morning of her promotion.",
    continuity="Series opener; sets up the Ellerby family firm and the cloned access badge.")
EP2 = render_episode(
    2, EP2_SCENES, upper=True,
    logline="Trapped in a dark elevator with her accuser, the analyst is offered a forged confession to sign.",
    continuity="Continues the Episode 1 cliffhanger: seconds later, the elevator is dark and Nadia is trapped with Grant.")


def write_text(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def screenplay() -> Callable[..., str]:
    """The renderer, for tests that need a custom (often malformed) episode."""
    return render_episode


@pytest.fixture
def tiny_show_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "forty_second_floor"
    write_text(folder, "episode-001-Episode 9100001.txt", EP1)
    write_text(folder, "episode-002-Episode 9100002.txt", EP2)
    return folder


@pytest.fixture
def tiny_show(tiny_show_dir: Path) -> Show:
    return parse_show(tiny_show_dir)
