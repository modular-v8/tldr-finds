#!/usr/bin/env python3
"""Rebuild the skill's bundled database snapshot from the live tldr-finds.db.

  python build_skill.py            snapshot into skill/tldr-finds/data/
  python build_skill.py --zip      also write dist/tldr-finds.skill (zip of the skill folder)

Run after `tldr.py add`. The snapshot is written to a temp file and swapped in only after it passes checks.
"""
import argparse
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "tldr-finds.db"
SKILL = HERE / "skill" / "tldr-finds"
OUT = SKILL / "data" / "tldr-finds.db"


def snapshot():
    if not SRC.is_file():
        sys.exit(f"source database not found: {SRC}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"{SRC.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.execute("INSERT INTO finds_fts(finds_fts) VALUES('rebuild')")  # guarantee the index matches the rows
    dst.commit()
    dst.execute("PRAGMA journal_mode = DELETE")  # single self-contained file, no -wal sidecar
    dst.execute("VACUUM")
    n, fts, last = dst.execute("SELECT (SELECT count(*) FROM finds), (SELECT count(*) FROM finds_fts), "
                               "(SELECT max(date_found) FROM finds)").fetchone()
    check = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    if check != "ok" or n != fts or n == 0:
        tmp.unlink(missing_ok=True)
        sys.exit(f"snapshot failed checks: integrity={check} finds={n} fts={fts}")
    os.replace(tmp, OUT)
    print(f"snapshot ok: {n} finds, latest {last}, {OUT.stat().st_size // 1024} KB -> {OUT.relative_to(HERE)}")


def make_zip():
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "tldr-finds.skill"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SKILL.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".tmp":
                z.write(p, Path("tldr-finds") / p.relative_to(SKILL))
        z.write(HERE / "LICENSE", "tldr-finds/LICENSE")
    print(f"wrote {target.relative_to(HERE)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", action="store_true")
    a = ap.parse_args()
    snapshot()
    if a.zip:
        make_zip()
