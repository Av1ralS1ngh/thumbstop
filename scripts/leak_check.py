"""Fail if any tracked/staged file contains text from the confidential input pack (8-word shingles).
Usage: python scripts/leak_check.py [PACK_DIR]   (default: ../input_pack). Used as a git pre-commit hook."""
import re, subprocess, sys
from pathlib import Path

N = 8
pack = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "input_pack")
if not pack.exists():
    print(f"leak_check: pack not found at {pack}; skipping"); sys.exit(0)
def shingles(text):
    w = re.findall(r"[a-z']+", text.lower())
    return {" ".join(w[i:i + N]) for i in range(len(w) - N + 1)}
secret = set()
for f in pack.rglob("*.txt"):
    secret |= shingles(f.read_text(errors="ignore"))
files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True,
                       text=True).stdout.split()
bad = []
for f in files:
    p = Path(f)
    if not p.is_file() or p.stat().st_size > 5_000_000:
        continue
    hits = shingles(p.read_text(errors="ignore")) & secret
    if hits:
        bad.append((f, len(hits), next(iter(hits))))
if bad:
    print("LEAK CHECK FAILED - these files contain text from the confidential pack:")
    for f, n, ex in bad:
        print(f"  {f}: {n} matching 8-word runs, e.g. '{ex}'")
    sys.exit(1)
print(f"leak_check: OK ({len(files)} files checked against {len(secret)} pack shingles)")
