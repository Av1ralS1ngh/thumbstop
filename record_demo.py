"""Record real model answers for the app's built-in demo show (original fiction), so the hosted app can show an
AI run to anyone without a key. Runs the normal pipeline with the configured models into a fresh cache and saves
that cache as demo_answers.json. Needs the AWS login; costs well under $1.

    .venv/bin/python record_demo.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import diskcache

from thumbstop import hosted
from thumbstop.llm import Router, load_config
from thumbstop.pipeline import run


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="thumbstop-demo-") as tmp:
        work = Path(tmp)
        folder = hosted.write_demo_show(work / "in")
        router = Router(load_config(), out_dir=work / "out")
        router.cache_dir = work / "cache"
        run(folder, work / "out", router, hosted.recorded_options())
        with diskcache.Cache(str(router.cache_dir)) as cache:
            answers = {k: cache[k] for k in sorted(cache.iterkeys())}
    hosted.DEMO_ANSWERS.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {len(answers)} answers to {hosted.DEMO_ANSWERS.name}")
    hosted.write_teaser()   # the hero preview replays these answers


if __name__ == "__main__":
    main()
