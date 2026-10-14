"""Tests for find.py's auto-update, run against a local HTTP server (127.0.0.1); the real network is never used.
Run: python -m unittest discover -s tests -v"""
import functools
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skill" / "tldr-finds"
sys.path.insert(0, str(ROOT))

import tldr  # noqa: E402


def make_db(path, dates):
    """A real finds database with one row per date in `dates`."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = MEMORY")
    con.executescript(tldr.SCHEMA)
    for i, d in enumerate(dates):
        con.execute("INSERT INTO finds(date_found,name,description,url,category) VALUES (?,?,?,?,?)",
                    (d, f"Tool {d}-{i}", "a free tool", f"https://example.com/{d}/{i}", "other"))
    con.commit()
    con.close()
    return Path(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Server:
    """Serves a folder and records every requested path."""

    def __init__(self, folder):
        self.hits = []
        hits = self.hits

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                super().do_GET()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(folder)))
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache = self.tmp / "cache"
        # a private copy of the skill whose bundled snapshot is old: 2 finds, latest 2026-10-01
        self.skill = self.tmp / "skill"
        shutil.copytree(SKILL, self.skill, ignore=shutil.ignore_patterns("__pycache__"))
        (self.skill / "data" / "tldr-finds.db").unlink()
        make_db(self.skill / "data" / "tldr-finds.db", ["2026-09-30", "2026-10-01"])
        self.find = self.skill / "scripts" / "find.py"
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.server = Server(self.release)
        self.addCleanup(self.server.close)

    def publish(self, dates, **tamper):
        """Put a release on the server. tamper: sha=, size=, body=bytes, manifest_text=, schema="""
        db = make_db(self.tmp / "published.db", dates) if "body" not in tamper else self.tmp / "published.db"
        if "body" in tamper:
            db.write_bytes(tamper["body"])
        served = self.release / "tldr-finds.db"
        shutil.copyfile(db, served)
        if "served" in tamper:  # serve something other than what the manifest describes
            served.write_bytes(tamper["served"])
        manifest = {"schema": tamper.get("schema", 1), "sha256": tamper.get("sha", sha(db)),
                    "bytes": tamper.get("size", db.stat().st_size), "finds": len(dates),
                    "latest": max(dates) if dates else None, "published": "2026-10-14T00:00:00Z"}
        text = tamper.get("manifest_text") or json.dumps(manifest)
        (self.release / "manifest.json").write_text(text, encoding="utf-8")
        db.unlink()
        return manifest

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("TLDR_FINDS_")}
        e.update(TLDR_FINDS_CACHE_DIR=str(self.cache), TLDR_FINDS_RELEASE_URL=self.server.url)
        for k, v in extra.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def run_find(self, *args, **env):
        return subprocess.run([sys.executable, str(self.find), *args], capture_output=True, text=True,
                              encoding="utf-8", env=self.env(**env))

    def latest(self, **env):
        r = self.run_find("recent", "--limit", "1", "--format", "json", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        return data["latest_find"], data["total_finds"]

    def age_last_check(self, seconds):
        stamp = self.cache / "last-check"
        t = time.time() - seconds
        os.utime(stamp, (t, t))

    def cached_latest(self):
        con = sqlite3.connect(f"{(self.cache / 'tldr-finds.db').as_uri()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT max(date_found), count(*) FROM finds").fetchone()
        finally:
            con.close()


class Updating(Base):
    def test_update_downloads_and_queries_use_the_newer_copy(self):
        self.publish(["2026-10-05", "2026-10-10", "2026-10-12"])
        r = self.run_find("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("updated:"), r.stdout)
        self.assertIn("2 finds, latest 2026-10-01 -> 3 finds, latest 2026-10-12", r.stdout)
        self.assertEqual(self.latest(), ("2026-10-12", 3))
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ["last-check", "manifest.json", "tldr-finds.db"])

    def test_first_query_checks_automatically_then_stays_quiet_for_a_day(self):
        self.publish(["2026-10-12"])
        self.assertEqual(self.latest(), ("2026-10-12", 1))  # the check ran as part of this query
        made = len(self.server.hits)
        self.assertEqual(made, 2)  # manifest + database
        self.latest()
        self.run_find("search", "tool")
        self.assertEqual(len(self.server.hits), made, "no request within 24 hours")

    def test_stale_check_asks_again_but_skips_unchanged_download(self):
        self.publish(["2026-10-12"])
        self.run_find("update")
        self.server.hits.clear()
        self.age_last_check(2 * 86400)
        self.latest()
        self.assertEqual(self.server.hits, ["/manifest.json"])

    def test_update_ignores_the_daily_limit_and_reports_already_current(self):
        self.publish(["2026-10-12"])
        self.run_find("update")
        self.server.hits.clear()
        r = self.run_find("update")
        self.assertTrue(r.stdout.startswith("already current:"), r.stdout)
        self.assertEqual(self.server.hits, ["/manifest.json"])

    def test_a_new_release_replaces_the_cache(self):
        self.publish(["2026-10-10"])
        self.run_find("update")
        self.publish(["2026-10-10", "2026-10-13"])
        r = self.run_find("update")
        self.assertTrue(r.stdout.startswith("updated:"), r.stdout)
        self.assertEqual(self.cached_latest(), ("2026-10-13", 2))

    def test_bundled_snapshot_is_never_written(self):
        before = (self.skill / "data" / "tldr-finds.db").read_bytes()
        self.publish(["2026-10-12"])
        self.run_find("update")
        self.latest()
        self.assertEqual((self.skill / "data" / "tldr-finds.db").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in (self.skill / "data").iterdir()), ["tldr-finds.db"])


class Rejecting(Base):
    """Bad downloads are discarded and the previous copy is kept."""

    def good_cache_then(self, **tamper):
        self.publish(["2026-10-05", "2026-10-10"])
        self.run_find("update")
        self.assertEqual(self.cached_latest(), ("2026-10-10", 2))
        self.publish(["2026-10-11", "2026-10-12", "2026-10-13"], **tamper)
        r = self.run_find("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(r.stdout.startswith("updated"), r.stdout)
        self.assertEqual(self.cached_latest(), ("2026-10-10", 2), "previous copy must be kept")
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-10", 2))
        self.assertEqual([p.name for p in self.cache.glob("*.tmp")], [], "no temp files left behind")
        return r.stdout

    def test_wrong_sha256(self):
        self.assertIn("does not match", self.good_cache_then(sha="0" * 64))

    def test_wrong_size(self):
        self.assertIn("larger than the manifest", self.good_cache_then(size=5000))  # file is bigger than declared

    def test_download_larger_than_the_manifest_says(self):
        out = self.good_cache_then(served=b"x" * 400_000)
        self.assertIn("larger than the manifest", out)

    def test_same_size_but_one_byte_flipped(self):
        self.publish(["2026-10-11", "2026-10-12"])
        served = self.release / "tldr-finds.db"
        data = bytearray(served.read_bytes())
        data[-1] ^= 0xFF
        served.write_bytes(bytes(data))
        r = self.run_find("update")
        self.assertIn("does not match", r.stdout)
        self.assertFalse((self.cache / "tldr-finds.db").exists())
        self.assertEqual(self.latest(), ("2026-10-01", 2))

    def test_corrupt_file_with_matching_manifest(self):
        out = self.good_cache_then(body=b"this is not a database " * 200)
        self.assertIn("could not update", out)

    def test_empty_database_is_rejected(self):
        self.publish(["2026-10-10"])
        self.run_find("update")
        self.publish([])  # valid database with the schema but zero rows, honest manifest
        r = self.run_find("update")
        self.assertIn("no finds", r.stdout)
        self.assertEqual(self.cached_latest(), ("2026-10-10", 1))

    def test_database_without_the_tables(self):
        self.publish(["2026-10-10"])
        self.run_find("update")
        other = self.tmp / "other.db"
        con = sqlite3.connect(other)
        con.execute("CREATE TABLE unrelated(x)")
        con.execute("INSERT INTO unrelated VALUES (1)")
        con.commit()
        con.close()
        self.publish(["2026-10-11"], body=other.read_bytes())
        r = self.run_find("update")
        self.assertIn("missing the finds tables", r.stdout)
        self.assertEqual(self.cached_latest(), ("2026-10-10", 1))

    def test_bad_manifest_json(self):
        out = self.good_cache_then(manifest_text="{not json")
        self.assertIn("invalid manifest", out)

    def test_unknown_manifest_schema(self):
        out = self.good_cache_then(schema=2)
        self.assertIn("invalid manifest", out)

    def test_manifest_with_bad_fields(self):
        for bad in ('{"schema": 1, "sha256": "xyz", "bytes": 10}', '{"schema": 1, "sha256": "%s", "bytes": -1}' % ("a" * 64),
                    '{"schema": 1, "sha256": "%s", "bytes": 999999999}' % ("a" * 64), "[]", "null"):
            with self.subTest(bad=bad[:40]):
                self.publish(["2026-10-12"], manifest_text=bad)
                r = self.run_find("update")
                self.assertEqual(r.returncode, 0)
                self.assertIn("invalid manifest", r.stdout)
                self.assertFalse((self.cache / "tldr-finds.db").exists())


class NeverBreaksAQuery(Base):
    def test_server_down_exits_zero_with_bundled_copy(self):
        self.server.close()
        self.assertEqual(self.latest(), ("2026-10-01", 2))
        r = self.run_find("update")
        self.assertEqual(r.returncode, 0)
        self.assertIn("could not update", r.stdout)

    def test_failed_check_is_not_retried_on_every_query(self):
        self.server.close()
        self.latest()
        self.assertTrue((self.cache / "last-check").exists(), "the attempt is recorded even though it failed")
        self.server = Server(self.release)  # a fresh server would see any retry
        self.addCleanup(self.server.close)
        self.latest(TLDR_FINDS_RELEASE_URL=self.server.url)
        self.assertEqual(self.server.hits, [])

    def test_http_error_from_server(self):
        r = self.run_find("update")  # nothing published: 404
        self.assertEqual(r.returncode, 0)
        self.assertIn("could not update", r.stdout)
        self.assertEqual(self.latest(), ("2026-10-01", 2))

    def test_unwritable_cache_folder_makes_no_request_and_no_error(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("a file where a folder is needed", encoding="utf-8")
        self.publish(["2026-10-12"])
        env = {"TLDR_FINDS_CACHE_DIR": str(blocker / "tldr-finds")}
        self.assertEqual(self.latest(**env), ("2026-10-01", 2))
        r = self.run_find("update", **env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("not writable", r.stdout)
        self.assertEqual(self.server.hits, [])

    def test_offline_and_override_make_no_requests(self):
        self.publish(["2026-10-12"])
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-01", 2))
        r = self.run_find("update", TLDR_FINDS_OFFLINE="1")
        self.assertIn("TLDR_FINDS_OFFLINE", r.stdout)
        other = make_db(self.tmp / "other.db", ["2026-08-01"])
        self.assertEqual(self.latest(TLDR_FINDS_DB=str(other)), ("2026-08-01", 1))
        r = self.run_find("update", TLDR_FINDS_DB=str(other))
        self.assertIn("TLDR_FINDS_DB", r.stdout)
        self.assertEqual(self.server.hits, [])
        self.assertFalse(self.cache.exists())

    def test_every_command_still_works_after_an_update(self):
        self.publish(["2026-10-05", "2026-10-12"])
        self.run_find("update")
        for args in (["search", "tool"], ["recent"], ["categories"]):
            with self.subTest(args=args):
                r = self.run_find(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("latest find 2026-10-12", r.stdout)


class PickingTheCopy(Base):
    def put_cache(self, dates):
        self.cache.mkdir(parents=True, exist_ok=True)
        make_db(self.cache / "tldr-finds.db", dates)

    def test_cache_older_than_bundled_is_ignored(self):
        self.put_cache(["2026-09-01"])
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-01", 2))

    def test_cache_newer_than_bundled_wins(self):
        self.put_cache(["2026-10-09"])
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-09", 1))

    def test_cache_wins_a_tie(self):
        self.put_cache(["2026-10-01", "2026-10-01", "2026-10-01"])
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-01", 3))

    def test_unreadable_cache_falls_back_to_bundled(self):
        self.cache.mkdir(parents=True)
        (self.cache / "tldr-finds.db").write_bytes(b"garbage" * 100)
        self.assertEqual(self.latest(TLDR_FINDS_OFFLINE="1"), ("2026-10-01", 2))


class InProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SKILL / "scripts"))
        import find
        cls.find = find

    def with_env(self, **env):
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])

    def test_release_url_override_is_limited(self):
        f = self.find
        self.assertEqual(f.release_url(), f.RELEASE_URL)
        self.with_env(TLDR_FINDS_RELEASE_URL="http://example.com/x/")
        self.assertEqual(f.release_url(), f.RELEASE_URL)
        self.with_env(TLDR_FINDS_RELEASE_URL="ftp://127.0.0.1/x/")
        self.assertEqual(f.release_url(), f.RELEASE_URL)
        self.with_env(TLDR_FINDS_RELEASE_URL="http://127.0.0.1:9/x")
        self.assertEqual(f.release_url(), "http://127.0.0.1:9/x/")
        self.with_env(TLDR_FINDS_RELEASE_URL="https://example.org/r/")
        self.assertEqual(f.release_url(), "https://example.org/r/")

    def test_default_release_url_is_https_github(self):
        self.assertTrue(self.find.RELEASE_URL.startswith("https://github.com/modular-v8/tldr-finds/releases/"))

    def test_cache_dir_resolution(self):
        f = self.find
        self.with_env(TLDR_FINDS_CACHE_DIR="/somewhere/else")
        self.assertEqual(f.cache_dir(), "/somewhere/else")

    def test_connection_does_not_trust_the_schema(self):
        con = self.find.open_ro(SKILL / "data" / "tldr-finds.db")
        try:
            self.assertEqual(con.execute("PRAGMA trusted_schema").fetchone()[0], 0)
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM finds")
        finally:
            con.close()

    def test_valid_manifest(self):
        v = self.find.valid_manifest
        good = {"schema": 1, "sha256": "a" * 64, "bytes": 100}
        self.assertTrue(v(good))
        for patch in ({"schema": 2}, {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"bytes": 0}, {"bytes": True},
                      {"bytes": 50_000_001}, {"bytes": "100"}):
            with self.subTest(patch=patch):
                self.assertFalse(v({**good, **patch}))


if __name__ == "__main__":
    unittest.main()
