"""Core data model. Code owns every timestamp; models only ever see and return beat IDs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["dialogue", "action", "camera", "sfx"]


def fmt_ts(t: float) -> str:
    """Seconds -> MM:SS.ss (the screenplay's own format)."""
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def fmt_tc(t: float, fps: int = 30) -> str:
    """Seconds -> HH:MM:SS:FF editor timecode (nearest frame, non-drop)."""
    s, f = divmod(int(round(max(t, 0.0) * fps)), fps)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


@dataclass
class Beat:
    id: str            # "E07-B031" (stable across runs: episode + order in file)
    ep: int
    idx: int           # order within the episode file
    t0: float
    t1: float
    kind: Kind
    text: str
    speaker: str | None = None      # raw label without parentheticals, e.g. "LADY ELEANOR"
    tags: list[str] = field(default_factory=list)   # emotion tags, inline [chuckles]
    flags: list[str] = field(default_factory=list)  # OS, VO
    scene: int = 0
    src_line: int = 0

    @property
    def is_cut_unit(self) -> bool:
        """Beats a promo may start/end on (what a viewer hears or sees happen)."""
        return self.kind in ("dialogue", "action")

    def render(self) -> str:
        span = f"[{fmt_ts(self.t0)}-{fmt_ts(self.t1)}]"
        if self.kind == "dialogue":
            tag = f" ({', '.join(self.tags)})" if self.tags else ""
            return f'{self.id} {span} {self.speaker}{tag}: "{self.text}"'
        return f"{self.id} {span} {self.kind.upper()}: {self.text}"


@dataclass
class Scene:
    num: int
    heading: str
    t0: float | None
    t1: float | None


@dataclass
class Episode:
    num: int
    file: str
    logline: str = ""
    continuity: str = ""
    scenes: list[Scene] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [b.t1 for b in self.beats] + [s.t1 for s in self.scenes if s.t1 is not None]
        return max(ends) if ends else 0.0

    @property
    def cut_units(self) -> list[Beat]:
        return sorted((b for b in self.beats if b.is_cut_unit), key=lambda b: (b.t0, b.idx))

    def beat(self, beat_id: str) -> Beat | None:
        return self._index().get(beat_id)

    def _index(self) -> dict[str, Beat]:
        if not hasattr(self, "_idx_cache"):
            self._idx_cache = {b.id: b for b in self.beats}  # type: ignore[attr-defined]
        return self._idx_cache  # type: ignore[attr-defined]


@dataclass
class Show:
    name: str
    episodes: list[Episode]

    def ep(self, num: int) -> Episode | None:
        return next((e for e in self.episodes if e.num == num), None)

    def beat(self, beat_id: str) -> Beat | None:
        try:
            ep = int(beat_id.split("-")[0][1:])
        except (ValueError, IndexError):
            return None
        e = self.ep(ep)
        return e.beat(beat_id) if e else None


@dataclass
class Segment:
    ep: int
    start: float
    end: float
    first_beat: str
    last_beat: str

    @property
    def dur(self) -> float:
        return self.end - self.start
