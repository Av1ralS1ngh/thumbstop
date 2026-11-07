"""Compare two runs (model swap, ablation, re-run): how many of the top-k picks are the same moment,
rank agreement on shared picks, and cost/time.   python -m thumbstop.compare runs/A runs/B [k]"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(run: Path) -> dict:
    return json.loads((run / "promos.json").read_text())


def _spans(p: dict) -> list[tuple[int, float, float]]:
    return [(s["episode"], _sec(s["in_ts"]), _sec(s["out_ts"])) for s in p["segments"]]


def _sec(ts: str) -> float:
    m, s = ts.split(":")
    return int(m) * 60 + float(s)


def same_moment(a: dict, b: dict) -> bool:
    """Same moment = the clips overlap for at least half of the shorter one."""
    inter = sum(max(0.0, min(x2, y2) - max(x1, y1)) for (e1, x1, x2) in _spans(a) for (e2, y1, y2) in _spans(b) if e1 == e2)
    return inter >= 0.5 * min(a["duration_s"], b["duration_s"])


def kendall_tau(xs: list[int], ys: list[int]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    c = d = 0
    for i in range(n):
        for j in range(i + 1, n):
            s = (xs[i] - xs[j]) * (ys[i] - ys[j])
            c += s > 0
            d += s < 0
    return round((c - d) / (n * (n - 1) / 2), 2)


def compare(a_dir: Path, b_dir: Path, k: int = 10) -> str:
    A, B = _load(a_dir), _load(b_dir)
    ta, tb = A["promos"][:k], B["promos"][:k]
    pairs = []
    for pa in ta:
        m = next((pb for pb in tb if same_moment(pa, pb)), None)
        if m:
            pairs.append((pa["rank"], m["rank"]))
    ua, ub = A["meta"]["usage"], B["meta"]["usage"]
    lines = [f"Compare top-{k}: {a_dir.name} vs {b_dir.name}",
             f"  models A: {A['meta']['models']}", f"  models B: {B['meta']['models']}",
             f"  same moment in both top-{k}: {len(pairs)}/{k}",
             f"  rank agreement on shared picks (Kendall tau): {kendall_tau([p[0] for p in pairs], [p[1] for p in pairs])}",
             f"  episodes covered: A {len({e for p in A['promos'] for e in p['episodes']})}, "
             f"B {len({e for p in B['promos'] for e in p['episodes']})}",
             f"  cost: A ${ua['usd']:.2f} ({ua['calls']} calls, {A['meta']['seconds']:.0f}s) | "
             f"B ${ub['usd']:.2f} ({ub['calls']} calls, {B['meta']['seconds']:.0f}s)",
             f"  funnel A: {A['meta']['funnel']}", f"  funnel B: {B['meta']['funnel']}"]
    return "\n".join(lines)



def cold_cost(run_dir: Path) -> str:
    """True cost of producing a run from scratch: every non-cached call in its trace (incl. retries),
    priced at the profile's list price."""
    from .llm import get_profile, load_config

    cfg = load_config()
    prices = {}
    for name in cfg["profiles"]:
        try:
            p = get_profile(cfg, name)
            prices[p.model] = (p.price_in, p.price_out)
        except Exception:
            pass
    roles: dict[str, list[float]] = {}
    for line in open(run_dir / "trace.jsonl"):
        r = json.loads(line)
        if r.get("cached") or not r.get("tin"):
            continue
        pi, po = prices.get(r["model"], (0, 0))
        acc = roles.setdefault(f"{r['role']} ({r['model']})", [0, 0, 0, 0.0])
        acc[0] += 1
        acc[1] += r["tin"]
        acc[2] += r["tout"]
        acc[3] += r["tin"] / 1e6 * pi + r["tout"] / 1e6 * po
    tot = sum(v[3] for v in roles.values())
    rows = [f"Cold-run cost for {run_dir.name} (list prices):"]
    rows += [f"  {k}: {v[0]} calls, {v[1]/1000:.0f}k in / {v[2]/1000:.0f}k out, ${v[3]:.2f}" for k, v in roles.items()]
    rows.append(f"  TOTAL ${tot:.2f}")
    return "\n".join(rows)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "cost":
        print(cold_cost(Path(sys.argv[2])))
    else:
        a, b = Path(sys.argv[1]), Path(sys.argv[2])
        print(compare(a, b, int(sys.argv[3]) if len(sys.argv) > 3 else 10))
