"""CLI.

  python -m thumbstop run   <folder> [--out runs/x] [--profile NAME] [--annotate P --gate P --repair P]
                                     [--fast] [--offline] [--episodes 1-10] [--top 20]
  python -m thumbstop whatif <folder> --out runs/x2 [--min 15 --max 30] [--episodes 1-10] [--no-violence]
  python -m thumbstop ingest <folder>          # parse + report only, no model calls
  python -m thumbstop estimate <folder>        # token/cost estimate, no model calls
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .llm import ROOT, Router, get_profile, load_config
from .parse import ingest_report, parse_show
from .pipeline import Options, run


def _range(s: str | None):
    if not s:
        return None
    a, _, b = s.partition("-")
    return (int(a), int(b or a))


def _load_env() -> None:
    """Load .env, then drop empty values so a blank AWS_* line never overrides the normal AWS login."""
    import os

    load_dotenv(ROOT / ".env")
    for k in [k for k, v in os.environ.items() if v == "" and (k.startswith("AWS_") or k.endswith("_API_KEY"))]:
        del os.environ[k]


def main(argv=None) -> int:
    _load_env()
    ap = argparse.ArgumentParser(prog="thumbstop", description="Find, cut-sheet and rank promos in microdrama screenplays")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "whatif"):
        r = sub.add_parser(name)
        r.add_argument("folder")
        r.add_argument("--out", default=None)
        r.add_argument("--profile", help="one-profile mode: every role uses this model profile")
        r.add_argument("--annotate")
        r.add_argument("--gate")
        r.add_argument("--repair")
        r.add_argument("--config")
        r.add_argument("--fast", action="store_true", help="interview mode: gate top 12, no repair")
        r.add_argument("--offline", action="store_true", help="no network: rules-only annotator and gate")
        r.add_argument("--episodes", help="e.g. 1-10")
        r.add_argument("--top", type=int, default=20)
        r.add_argument("--min", type=float, default=20.0)
        r.add_argument("--max", type=float, default=60.0)
        r.add_argument("--no-violence", action="store_true")
        r.add_argument("--hook-types", default="")
        r.add_argument("--no-gate", action="store_true")
        r.add_argument("--budget-usd", type=float, default=None)
        r.add_argument("--no-notes", action="store_true", help="ablation: ignore writers' continuity notes")
    for name in ("ingest", "estimate"):
        s = sub.add_parser(name)
        s.add_argument("folder")
    a = ap.parse_args(argv)

    if a.cmd == "ingest":
        print(ingest_report(parse_show(a.folder)))
        return 0
    if a.cmd == "estimate":
        from .annotate import SYSTEM, render_episode
        show = parse_show(a.folder)
        chars = sum(len(render_episode(e)) + len(SYSTEM) for e in show.episodes)
        tin = chars / 3.6
        print(ingest_report(show))
        print(f"Annotate input ~{tin/1000:.0f}k tokens; whole run ~{(tin*1.6)/1000:.0f}k in / ~{len(show.episodes)*1.4:.0f}k out")
        cfg = load_config()
        for name in ("opus47", "bedrock_gptoss", "gemini_lite"):
            p = get_profile(cfg, name)
            usd = tin * 1.6 / 1e6 * p.price_in + len(show.episodes) * 1400 / 1e6 * p.price_out
            print(f"  {name:15s} ~${usd:.2f} per run")
        return 0

    cfg = load_config(a.config)
    if a.budget_usd:
        cfg.setdefault("budget", {})["max_usd"] = a.budget_usd
    single = "rules" if a.offline else a.profile
    out = Path(a.out) if a.out else ROOT / "runs" / (("offline" if a.offline else (a.profile or "default")))
    router = Router(cfg, overrides={"annotate": a.annotate, "gate": a.gate, "repair": a.repair}, single=single, out_dir=out)
    if router["annotate"].family == router["gate"].family and not a.offline:
        print(f"NOTE: annotate and gate use the same model family ({router['gate'].family}); "
              "the cold-viewer check is less independent. Prefer a different family for --gate.")
    opt = Options(top=a.top, gate=not a.no_gate, repair=not a.fast, episodes=_range(a.episodes), min_len=a.min,
                  max_len=a.max, exclude_policy=["violence"] if a.no_violence else [],
                  hook_types=[h for h in a.hook_types.split(",") if h], gate_n=12 if a.fast else 40,
                  no_notes=a.no_notes)
    out.mkdir(parents=True, exist_ok=True)
    run(a.folder, out, router, opt)
    print(f"\nOutputs in {out}/ : promos.json, promos.csv, report.md, review.html, selects.edl, trace.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
