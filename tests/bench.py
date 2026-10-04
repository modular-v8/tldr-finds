"""Timing and output-size check for find.py (not part of CI: wall time depends on the machine).

  python tests/bench.py

Wall time is mostly the Python interpreter starting up, which varies a lot between machines and even between
runs, so the number to watch is the skill's overhead over bare `python -S`.
"""
import statistics
import subprocess
import sys
import time
from pathlib import Path

FIND = Path(__file__).resolve().parent.parent / "skill" / "tldr-finds" / "scripts" / "find.py"
N = 30
QUERIES = ["design tokens", "react components", "duckdb", "free color palette", "mcp server for agents"]
PY = sys.executable


def wall_ms(cmd):
    t = time.perf_counter()
    subprocess.run(cmd, capture_output=True)
    return (time.perf_counter() - t) * 1000


def stats(label, cmd):
    runs = sorted(wall_ms(cmd) for _ in range(N))
    med = statistics.median(runs)
    print(f"{label:<44} median {med:6.1f} ms   p95 {runs[int(N * .95) - 1]:6.1f} ms")
    return med


base = stats("bare python -S (-c pass)", [PY, "-S", "-c", "pass"])
meds = [stats(f"find.py -S search {q!r}", [PY, "-S", str(FIND), "search", q]) for q in QUERIES]
plain = stats("find.py (no -S) search 'design tokens'", [PY, str(FIND), "search", "design tokens"])
print(f"\nskill overhead over bare `python -S`: ~{statistics.median(meds) - base:.0f} ms "
      f"(whole call target: < 100 ms with -S on a typical machine)")
print("\noutput size (default 8 results):")
for q in QUERIES:
    out = subprocess.run([PY, "-S", str(FIND), "search", q], capture_output=True, text=True, encoding="utf-8").stdout
    print(f"  {q!r:<28} {len(out):5d} chars  ~{len(out) // 4} tokens")
