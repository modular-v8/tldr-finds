#!/usr/bin/env python3
"""Rebuild the skill's bundled database snapshot, or publish the live database for the skill's auto-update.

  python build_skill.py            snapshot into skill/tldr-finds/data/
  python build_skill.py --zip      also write dist/tldr-finds.skill (zip of the skill folder)
  python build_skill.py --publish  upload the live database + manifest.json to the `db-latest` GitHub pre-release
                                   (daily; needs the `gh` CLI logged in). Makes no commits, touches no tracked files.

Run after `tldr.py add`. The bundled snapshot is written to a temp file and swapped in only after it passes checks.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "tldr-finds.db"
SKILL = HERE / "skill" / "tldr-finds"
OUT = SKILL / "data" / "tldr-finds.db"

REPO = "modular-v8/tldr-finds"
TAG = "db-latest"
RELEASE_URL = f"https://github.com/{REPO}/releases/download/{TAG}/"
GH = "gh"


def _show(path):
    path = Path(path)
    return path.relative_to(HERE) if HERE in path.parents else path


def snapshot(out=OUT, atomic=True):
    """Copy the live database to `out`, rebuild its search index and run integrity checks.

    atomic=True builds beside `out` and swaps it in (the bundled snapshot). atomic=False builds in place, for a
    fresh temp folder (publish). Both avoid SQLite journal files so a sandbox that cannot delete files still works.
    Returns (finds, latest date)."""
    if not SRC.is_file():
        sys.exit(f"source database not found: {SRC}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp") if atomic else out
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"{SRC.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    dst.execute("PRAGMA journal_mode = MEMORY")  # no journal file to create and delete
    src.backup(dst)
    src.close()
    dst.execute("INSERT INTO finds_fts(finds_fts) VALUES('rebuild')")  # guarantee the index matches the rows
    dst.commit()
    dst.execute("VACUUM")
    n, fts, last = dst.execute("SELECT (SELECT count(*) FROM finds), (SELECT count(*) FROM finds_fts), "
                               "(SELECT max(date_found) FROM finds)").fetchone()
    check = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    with open(tmp, "rb") as f:
        header = f.read(20)
    if header[18:20] == b"\x02\x02":  # the copy inherited WAL mode: make it a single self-contained file
        fix = sqlite3.connect(tmp)
        fix.execute("PRAGMA journal_mode = DELETE")
        fix.close()
    if check != "ok" or n != fts or n == 0:
        tmp.unlink(missing_ok=True)
        sys.exit(f"snapshot failed checks: integrity={check} finds={n} fts={fts}")
    if atomic:
        os.replace(tmp, out)
    print(f"snapshot ok: {n} finds, latest {last}, {out.stat().st_size // 1024} KB -> {_show(out)}")
    return n, last


def write_manifest(db, dest):
    """Describe `db` in a manifest.json at `dest` (what find.py compares and verifies). Returns the manifest dict."""
    db, dest = Path(db), Path(dest)
    data = db.read_bytes()
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        finds, latest = con.execute("SELECT count(*), max(date_found) FROM finds").fetchone()
    finally:
        con.close()
    manifest = {"schema": 1, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "finds": finds,
                "latest": latest, "published": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    dest.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def run_gh(*args):
    try:
        return subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=180)
    except FileNotFoundError:
        sys.exit("publish failed: the `gh` CLI is not installed (https://cli.github.com), then run `gh auth login`")
    except subprocess.TimeoutExpired:
        sys.exit(f"publish failed: `gh {args[0]} {args[1]}` timed out")


def gh_must(*args, what):
    r = run_gh(*args)
    if r.returncode != 0:
        sys.exit(f"publish failed: {what}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def ensure_release():
    if run_gh("auth", "status").returncode != 0:
        sys.exit("publish failed: `gh` is not logged in; run `gh auth login` (or set GH_TOKEN)")
    r = run_gh("release", "view", TAG, "--repo", REPO)
    if r.returncode == 0:
        return
    if "not found" not in (r.stderr or "").lower():
        sys.exit(f"publish failed: could not look up release {TAG}: {(r.stderr or r.stdout).strip()[-400:]}")
    gh_must("release", "create", TAG, "--repo", REPO, "--prerelease", "--title", "Rolling database (auto-updated)",
            "--notes", "Latest tldr-finds database, replaced in place by `build_skill.py --publish`. "
            "The skill downloads it automatically; there is nothing to install from here.",
            what=f"could not create release {TAG}")
    print(f"created pre-release {TAG}")


def fetch_public_manifest():
    """The manifest users will see, fetched the same way find.py does. None if unreachable."""
    from urllib.request import Request, urlopen
    try:
        with urlopen(Request(RELEASE_URL + "manifest.json", headers={"User-Agent": "tldr-finds-build"}), timeout=10) as r:
            return json.loads(r.read(65536))
    except Exception:
        return None


def verify_public(manifest, attempts=5, delay=3):
    for i in range(attempts):
        got = fetch_public_manifest()
        if got and got.get("sha256") == manifest["sha256"]:
            return
        if i < attempts - 1:
            time.sleep(delay)
    sys.exit("publish failed: uploaded, but the public manifest does not match yet "
             f"({RELEASE_URL}manifest.json); re-run `python build_skill.py --publish`")


def publish():
    tmp = Path(tempfile.mkdtemp(prefix="tldr-finds-publish-"))
    try:
        db = tmp / "tldr-finds.db"  # the asset takes its name from the file
        snapshot(db, atomic=False)  # exits before any gh call if the checks fail
        manifest_path = tmp / "manifest.json"
        manifest = write_manifest(db, manifest_path)
        ensure_release()
        # database first, manifest second: a manifest never points at a database that is not there yet
        gh_must("release", "upload", TAG, str(db), "--clobber", "--repo", REPO, what="uploading tldr-finds.db")
        gh_must("release", "upload", TAG, str(manifest_path), "--clobber", "--repo", REPO, what="uploading manifest.json")
        verify_public(manifest)
        print(f"published: {manifest['finds']} finds, latest {manifest['latest']} "
              f"(sha256 {manifest['sha256'][:12]}) -> {RELEASE_URL}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # best effort; a sandbox that cannot delete just leaves a temp folder


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
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    if a.publish:
        publish()
    elif SRC.is_file() or not a.zip:
        snapshot()
    else:  # CI: the live database is not in the repo, so package the committed snapshot as is
        print("live database not present; packaging the committed snapshot")
    if a.zip:
        make_zip()
