# Thumbstop

Finds the best 20-60 second promos in a microdrama's timestamped screenplays: a hook in the first 3 seconds, a
cliffhanger ending, and a clip that makes sense to someone who has never seen the show. For each promo it gives an
editor-ready cut sheet (episode, in/out timecodes for every segment, the hook, the cliffhanger, why it works),
ranks them, and says which to post first.

**The idea:** writers already end most episodes on a cliffhanger, so "take the last 45 seconds" is a strong
baseline. What it gets wrong is the opening. Thumbstop keeps the writer's cliffhanger as the ending and searches
for an opening a stranger can follow.

## Quick start

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt pytest
.venv/bin/python -m thumbstop run tests/fixtures/new_show --offline        # no network, no key, ~1 s
.venv/bin/python -m thumbstop run <folder of episodes> --out runs/myshow   # real models (AWS login), see below
open runs/myshow/review.html                                               # cut sheets + accept / nudge / reject
streamlit run app.py                                                       # the web app
# open http://localhost:8501/?demo=1 to run the built-in demo show on arrival
.venv/bin/python record_demo.py   # re-record the AI answers the app replays for its demo show (no-key mode)
# THUMBSTOP_RECORDED_ANSWERS=/path/answers.json.gz adds more recorded answers (kept outside the repo); they only
# replay for someone who uploads the exact scripts they were recorded on, since each is keyed by a hash of its prompt
```

Outputs in `--out`: `promos.json` and `promos.csv` (rank, episode, start/end of every segment, hook, cliffhanger,
why it works), `report.md` (post-first picks and the ranked table), `review.html` (cut sheets: IN ON / OUT ON /
STOP BEFORE lines, timecodes with handles, cut confidence, cold-viewer verdict, ad-policy status), `selects.edl`
(CMX3600, all picks back to back for Premiere / Resolve), `trace.jsonl` (every model call).

## How it works

```
episodes -> [code]  parse into numbered beats (E07-B031: start, end, speaker, text); fail loudly with file:line
         -> [AI]    per episode: rank the few cliffhanger beats to cut after and the hook beats to open on
         -> [code]  anchored search: every hook -> cliffhanger pair that makes a valid 20-60 s clip
                    (+ the model's own whole-promo proposals, validated the same way)
                    validator: hook within 3 s, no mid-line cut, <=3 segments, cliffhanger at the end
         -> [AI, a different model family] cold viewer reads ONLY the clip: hook / cliffhanger / makes-sense
         -> [agent loop, <=2 rounds] if the viewer is lost, the model picks one earlier setup moment; re-checked
         -> [code]  rank, diversity (<=2 per episode, no overlaps), ad-policy status, post-first picks, exports
         -> [human] editor accepts / nudges / rejects in review.html
```

Models never write timestamps or quotes: they point at beat IDs and code does all time arithmetic, so a cut
point or a quote cannot be hallucinated. Every model call is cached (re-runs are free and identical), traced, and
capped by a budget.

## Swapping models

Profiles live in `config.yaml`; each role (`annotate`, `gate`, `repair`, `propose`) names one.

```bash
python -m thumbstop run <folder> --profile deepseek              # one model for every role
python -m thumbstop run <folder> --annotate kimi --gate gptoss   # per role
LLM_BASE_URL=https://... LLM_MODEL=... LLM_API_KEY=... python -m thumbstop run <folder> --profile custom
```

Providers: Amazon Bedrock through the Converse API (your normal AWS login: Claude Opus/Sonnet 4.6, GPT-OSS,
Kimi, DeepSeek, Mistral, Llama, ...), Claude Opus 4.7+ through the Bedrock Mantle endpoint (Anthropic SDK), any
OpenAI-compatible endpoint (Gemini, Groq, OpenRouter, a Kuku-provided API, Ollama), and `rules` (offline). The
default judge, GPT-OSS-120B, is the same model Groq serves on its free tier.

## Interview / live mode

- `--fast`: no repair loop and no proposals (fastest run on a new show); the cold viewer still checks every promo it shows.
- `--offline`: rules only, no network - always prints promos.
- What-if re-ranking: `--episodes 1-10`, `--min 15 --max 30`, `--no-violence`,
  `--hook-types physical_aggression,threat_ultimatum`. Episode annotations come from the cache, so only newly
  shortlisted clips need a cold-viewer check (about a minute and a few cents); add `--no-gate` for an instant, free
  re-rank.
- `python -m thumbstop ingest <folder>` / `estimate <folder>`: parse report / token and cost estimate, no calls.

## Evaluation

- `python -m thumbstop.evaluate build <pack> <run>`: blind labelling page (`labels/blind.html`) with same-ending
  pairs (tool vs last-45s) and three-way design comparisons (tool vs "model proposes whole promos" vs last-45s),
  plus AI-judge ratings and negative controls (random windows, truncated endings, hook removed).
- `python -m thumbstop.evaluate score <pack> <run> labels/blind_answers.csv`: unblind and report.
- `python -m thumbstop.compare runs/A runs/B 10` (model swap, ablations); `python -m thumbstop.compare cost runs/A`.
- `--no-notes` ablation (ignore the writers' continuity notes); `--no-seeds` (anchored search only).

## Data safety

The input pack is confidential. `.gitignore` excludes `input_pack/`, `runs/`, `labels/`, `.cache/` and `.env`; a
git pre-commit hook (`scripts/leak_check.py`) blocks any commit containing an 8-word run of text from the pack.
Test fixtures are original fiction. The Streamlit app processes uploads in a temporary folder and deletes it.

## Tests

`PYTHONPATH=. .venv/bin/python -m pytest -q` - parser, format drift, validator, exports, LLM cache / retry /
budget, policy, ranking, offline end-to-end, and the app.

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
