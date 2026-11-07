"""Record real model answers for the app's built-in demo show (original fiction), so the hosted app can show an
AI run to anyone without a key. Runs the normal pipeline with the configured models into a fresh cache and saves
that cache as demo_answers.json. Needs the AWS login; costs well under $1.

    .venv/bin/python record_demo.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import diskcache

from thumbstop.llm import Router, load_config
from thumbstop.pipeline import run

ROOT = Path(__file__).resolve().parent


def main() -> None:
    spec = importlib.util.spec_from_file_location("thumbstop_app", ROOT / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    with tempfile.TemporaryDirectory(prefix="thumbstop-demo-") as tmp:
        work = Path(tmp)
        folder = app.write_demo_show(work / "in")
        router = Router(load_config(), out_dir=work / "out")
        router.cache_dir = work / "cache"
        run(folder, work / "out", router, app.recorded_options())
        with diskcache.Cache(str(router.cache_dir)) as cache:
            answers = {k: cache[k] for k in sorted(cache.iterkeys())}
    app.DEMO_ANSWERS.write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {len(answers)} answers to {app.DEMO_ANSWERS.name}")


if __name__ == "__main__":
    main()
