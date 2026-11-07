# thumbstop

Finds 20-60 second promo clips in timestamped microdrama scripts: a hook in the first 3 seconds, a cliffhanger
ending, and a clip that makes sense on its own.

## New input formats

`python -m thumbstop run <folder>` (and `ingest`, `estimate`) reads every `.txt`, `.srt` and `.vtt` file in the
folder, and a folder may mix them. The content decides how a file is read, not its extension:

| Format | How it is recognised | What you get |
|---|---|---|
| Kuku screenplay | `Start: (…) \| End: (…)` block lines or `--- SCRIPT START ---` | scenes, camera, action, dialogue and sound beats |
| SRT subtitles | numbered cues with `-->` timings | one dialogue beat per cue |
| WebVTT subtitles | file starts with `WEBVTT` | one dialogue beat per cue |
| Timed transcript | most lines look like `[00:01:02] Name: line` or `00:01:02 Name: line` | one dialogue beat per line |

- **Screenplay drift** that is handled: upper-case field names (`SCENE START:`), `HH:MM:SS.ss` timestamps, CRLF
  line endings, a UTF-8 BOM, missing continuity notes, missing CAMERA blocks, stacked speaker notes such as
  `NAME (O.S.) (Pleading)`, and inline tags like `[laughs]`, which are moved out of the text into the beat's tags.
- **Subtitles** are read with `pysubs2` (`uv pip install pysubs2`; screenplays and transcripts do not need it). A leading `NAME:` in capitals, or a WebVTT `<v Name>` voice tag, becomes
  the speaker; otherwise the speaker is empty. A cue that is only `[door slams]`, `(sighs)` or `♪ … ♪` becomes a
  sound beat. A cue with two `- ` lines is split into two lines, and the cue time is shared by text length.
- **Transcripts** give start times only, so each line ends at the next line's start or after an estimate from its
  word count, whichever comes first. Lines without a timestamp continue the line above.
- **Episode numbers** come from the screenplay header or, for subtitles and transcripts, from the filename
  (`episode-003.srt`, `ep03.vtt`, `S01E03.srt`).
- **Failing loudly:** an unknown format, a missing episode number, an end time before its start, a seconds field
  of 60 or more, or transcript times that go backwards all stop the run with `file:line` and a plain message.
  Subtitles and transcripts carry no actions or scene headings, so the ingest report warns that annotation relies
  on dialogue only.

Rehearsal data: `python scripts/make_synthetic_show.py` writes an original 10-episode drama with deliberate
format drift to `tests/fixtures/new_show/`, a 2-episode `mini_show/`, and `subs_show/` (the same show as SRT,
WebVTT and a transcript). `python -m thumbstop run tests/fixtures/new_show --offline` runs it with no network.
