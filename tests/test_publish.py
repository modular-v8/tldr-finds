"""Tests for `build_skill.py --publish`. gh and the network are stubbed; nothing is ever uploaded.
Run: python -m unittest discover -s tests -v"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_skill  # noqa: E402
import tldr  # noqa: E402


def make_db(path, rows=2):
    con = sqlite3.connect(path)
    con.executescript(tldr.SCHEMA)
    for i in range(rows):
        con.execute("INSERT INTO finds(date_found,name,description,url,category) VALUES (?,?,?,?,?)",
                    (f"2026-10-{10 + i:02d}", f"Tool {i}", "a free tool", f"https://example.com/{i}", "other"))
    con.commit()
    con.close()
    return Path(path)


def done(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeGh:
    """Records gh calls; `release view` fails with "release not found" until a release is created."""

    def __init__(self, release_exists=True, auth_ok=True, fail_upload_of=None):
        self.calls, self.release_exists, self.auth_ok, self.fail_upload_of = [], release_exists, auth_ok, fail_upload_of

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("auth", "status"):
            return done(0 if self.auth_ok else 1, err="" if self.auth_ok else "not logged in")
        if args[:2] == ("release", "view"):
            return done(0) if self.release_exists else done(1, err="release not found")
        if args[:2] == ("release", "create"):
            self.release_exists = True
        if args[:2] == ("release", "upload") and Path(args[3]).name == self.fail_upload_of:
            return done(1, err="HTTP 500 boom")
        return done(0)

    def verbs(self):
        return [" ".join(a[:2]) for a in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.src = make_db(self.tmp / "live.db")
        patch = mock.patch.object(build_skill, "SRC", self.src)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmpdir.cleanup)


class Manifest(Base):
    def test_manifest_describes_the_file(self):
        db = self.tmp / "tldr-finds.db"
        build_skill.snapshot(db, atomic=False)
        m = build_skill.write_manifest(db, self.tmp / "manifest.json")
        on_disk = json.loads((self.tmp / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m, on_disk)
        self.assertEqual(m["schema"], 1)
        self.assertEqual(m["sha256"], hashlib.sha256(db.read_bytes()).hexdigest())
        self.assertEqual(m["bytes"], db.stat().st_size)
        self.assertEqual((m["finds"], m["latest"]), (2, "2026-10-11"))
        self.assertRegex(m["published"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_snapshot_is_a_single_self_contained_file(self):
        db = self.tmp / "snap" / "tldr-finds.db"
        build_skill.snapshot(db, atomic=False)
        self.assertEqual([p.name for p in db.parent.iterdir()], ["tldr-finds.db"])
        self.assertEqual(db.read_bytes()[18:20], b"\x01\x01")  # rollback-journal format, not WAL


class Publish(Base):
    def publish(self, gh, public_ok=True):
        def fake_fetch():
            return {"sha256": self.last_sha} if public_ok else None

        real_write = build_skill.write_manifest

        def spy_write(db, dest):
            m = real_write(db, dest)
            self.last_sha = m["sha256"]
            return m

        with mock.patch.object(build_skill, "run_gh", gh), \
                mock.patch.object(build_skill, "write_manifest", spy_write), \
                mock.patch.object(build_skill, "fetch_public_manifest", fake_fetch), \
                mock.patch.object(build_skill.time, "sleep"):
            build_skill.publish()

    def test_uploads_database_before_manifest_with_clobber(self):
        gh = FakeGh()
        self.publish(gh)
        uploads = [a for a in gh.calls if a[:2] == ("release", "upload")]
        self.assertEqual(len(uploads), 2)
        self.assertEqual([Path(a[3]).name for a in uploads], ["tldr-finds.db", "manifest.json"])
        for a in uploads:
            self.assertIn("--clobber", a)
            self.assertEqual(a[2], "db-latest")
            self.assertEqual(a[a.index("--repo") + 1], "modular-v8/tldr-finds")
        self.assertNotIn("release create", gh.verbs())

    def test_creates_prerelease_when_missing(self):
        gh = FakeGh(release_exists=False)
        self.publish(gh)
        create = next(a for a in gh.calls if a[:2] == ("release", "create"))
        self.assertIn("--prerelease", create)
        self.assertLess(gh.verbs().index("release create"), gh.verbs().index("release upload"))

    def test_bundled_snapshot_is_not_modified(self):
        bundled = build_skill.OUT
        before = bundled.read_bytes(), bundled.stat().st_mtime_ns
        self.publish(FakeGh())
        self.assertEqual((bundled.read_bytes(), bundled.stat().st_mtime_ns), before)

    def test_failed_snapshot_checks_upload_nothing(self):
        empty = self.tmp / "empty.db"
        con = sqlite3.connect(empty)
        con.executescript(tldr.SCHEMA)
        con.close()
        gh = FakeGh()
        with mock.patch.object(build_skill, "SRC", empty), self.assertRaises(SystemExit) as cm:
            self.publish(gh)
        self.assertIn("snapshot failed checks", str(cm.exception))
        self.assertEqual(gh.calls, [])

    def test_missing_source_uploads_nothing(self):
        gh = FakeGh()
        with mock.patch.object(build_skill, "SRC", self.tmp / "nope.db"), self.assertRaises(SystemExit):
            self.publish(gh)
        self.assertEqual(gh.calls, [])

    def test_gh_missing_is_a_clear_error(self):
        with mock.patch.object(build_skill.subprocess, "run", side_effect=FileNotFoundError), \
                self.assertRaises(SystemExit) as cm:
            build_skill.publish()
        self.assertIn("`gh` CLI is not installed", str(cm.exception))

    def test_not_logged_in_is_a_clear_error(self):
        gh = FakeGh(auth_ok=False)
        with self.assertRaises(SystemExit) as cm:
            self.publish(gh)
        self.assertIn("not logged in", str(cm.exception))
        self.assertNotIn("release upload", gh.verbs())

    def test_upload_failure_names_the_cause(self):
        with self.assertRaises(SystemExit) as cm:
            self.publish(FakeGh(fail_upload_of="manifest.json"))
        self.assertIn("uploading manifest.json", str(cm.exception))
        self.assertIn("HTTP 500 boom", str(cm.exception))

    def test_public_manifest_mismatch_is_an_error(self):
        with self.assertRaises(SystemExit) as cm:
            self.publish(FakeGh(), public_ok=False)
        self.assertIn("does not match", str(cm.exception))

    def test_temp_folder_is_cleaned_up(self):
        seen = []
        orig = build_skill.tempfile.mkdtemp

        def spy(*a, **k):
            seen.append(orig(*a, **k))
            return seen[-1]

        with mock.patch.object(build_skill.tempfile, "mkdtemp", spy):
            self.publish(FakeGh())
        self.assertFalse(Path(seen[0]).exists())


class Cli(unittest.TestCase):
    def test_help_documents_publish(self):
        r = subprocess.run([sys.executable, str(ROOT / "build_skill.py"), "--help"], capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--publish", r.stdout)


if __name__ == "__main__":
    unittest.main()
