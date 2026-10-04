"""Smoke tests for the tldr-finds skill. Run: python -m unittest discover -s tests -v"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skill" / "tldr-finds"
FIND = SKILL / "scripts" / "find.py"
SNAPSHOT = SKILL / "data" / "tldr-finds.db"
LIVE_DB = ROOT / "tldr-finds.db"
SOURCE_LINE = "Curated from TLDR newsletters (https://tldr.tech)"

sys.path.insert(0, str(ROOT))


def run(*args, script=FIND, env=None):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env)


UGLY = ['"unbalanced', "a AND OR NOT (", "next.js auth", "design-tokens", "*", "col:name", "NEAR(a b)", "x" * 5000,
        "'; DROP TABLE finds;--", "日本語 ✨", "100%", "C:\\path?x#y", "AND", "-", '"', "()", "^", "{a}"]


class SearchBehaviour(unittest.TestCase):
    def test_ugly_queries_never_crash(self):
        for q in UGLY:
            with self.subTest(q=q[:30]):
                r = run("search", q, "--limit", "2")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_empty_query_is_a_clean_usage_error(self):
        for q in ("", "   "):
            r = run("search", q)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("empty query", r.stderr)

    def test_bad_flags_are_clean_errors(self):
        for args in (["search", "x", "--since", "bad"], ["search", "x", "--limit", "abc"], ["search", "x", "--limit"],
                     ["search", "x", "--format", "xml"], ["bogus"]):
            with self.subTest(args=args):
                r = run(*args)
                self.assertEqual(r.returncode, 2)
                self.assertNotIn("Traceback", r.stderr)

    def test_relevant_result_and_attribution(self):
        r = run("search", "duckdb")
        self.assertEqual(r.returncode, 0)
        self.assertIn("duckdb", r.stdout.lower())
        self.assertIn(SOURCE_LINE, r.stdout)

    def test_limit_and_category_filter(self):
        out = run("search", "ui", "--limit", "2", "--format", "json")
        data = json.loads(out.stdout)
        self.assertLessEqual(len(data["results"]), 2)
        out = run("recent", "--category", "data-sql", "--limit", "5", "--format", "json")
        rows = json.loads(out.stdout)["results"]
        self.assertTrue(rows)
        self.assertTrue(all(r["category"] == "data-sql" for r in rows))

    def test_unknown_category_returns_no_matches_not_error(self):
        r = run("search", "ui", "--category", "nope")
        self.assertEqual(r.returncode, 0)
        self.assertIn("No matches", r.stdout)

    def test_json_shape(self):
        data = json.loads(run("search", "design", "--format", "json").stdout)
        self.assertEqual(data["source"], SOURCE_LINE)
        self.assertGreater(data["total_finds"], 100)
        self.assertTrue({"name", "url", "description", "category", "date_found"} <= set(data["results"][0]))

    def test_categories(self):
        r = run("categories")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ui-components", r.stdout)
        self.assertIn(SOURCE_LINE, r.stdout)

    def test_since_filter(self):
        rows = json.loads(run("recent", "--since", "2026-09-20", "--limit", "50", "--format", "json").stdout)["results"]
        self.assertTrue(all(r["date_found"] >= "2026-09-20" for r in rows))

    def test_typical_output_is_small(self):
        out = run("search", "design tokens").stdout
        self.assertLess(len(out) // 4, 800, "typical output should stay around 600 tokens or fewer")


class SkillIsSelfContained(unittest.TestCase):
    def test_database_is_read_only(self):
        uri = SNAPSHOT.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM finds")
        con.close()

    def test_works_when_copied_elsewhere_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "my skills" / "tldr-finds"
            shutil.copytree(SKILL, dest, ignore=shutil.ignore_patterns("__pycache__"))
            r = run("search", "duckdb", "--limit", "1", script=dest / "scripts" / "find.py")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("1.", r.stdout)

    def test_runs_with_site_disabled(self):
        r = subprocess.run([sys.executable, "-S", str(FIND), "search", "duckdb", "--limit", "1"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1.", r.stdout)

    def test_env_override_and_missing_db(self):
        env = dict(os.environ, TLDR_FINDS_DB=str(ROOT / "does-not-exist.db"))
        r = run("categories", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("database not found", r.stderr)

    def test_skill_md_frontmatter(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head = text.split("---", 2)[1]
        self.assertIn("name: tldr-finds", head)
        desc = [l for l in head.splitlines() if l.startswith("description:")][0]
        self.assertLessEqual(len(desc) - len("description: "), 1024)
        self.assertIn("tldr.tech", text)

    def test_in_process_query_is_fast(self):
        sys.path.insert(0, str(SKILL / "scripts"))
        import find
        con = find.connect()
        opts = {"limit": 8, "format": "text"}
        find.search_rows(con, "warm up", opts)
        t = time.perf_counter()
        for _ in range(20):
            find.search_rows(con, "design tokens", opts)
        per_query_ms = (time.perf_counter() - t) / 20 * 1000
        self.assertLess(per_query_ms, 25)


class DataInSync(unittest.TestCase):
    """The shipped snapshot and the generated Markdown must match the source-of-truth database."""

    def rows(self, path):
        con = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT id, date_found, name, description, url, category FROM finds ORDER BY id").fetchall()
        finally:
            con.close()

    def test_snapshot_matches_live_db(self):
        self.assertEqual(self.rows(SNAPSHOT), self.rows(LIVE_DB),
                         "skill snapshot is stale: run `python build_skill.py`")

    def test_fts_index_matches_rows(self):
        con = sqlite3.connect(f"{SNAPSHOT.resolve().as_uri()}?mode=ro", uri=True)
        n, fts = con.execute("SELECT (SELECT count(*) FROM finds), (SELECT count(*) FROM finds_fts)").fetchone()
        con.close()
        self.assertEqual(n, fts)

    def test_markdown_lists_every_find(self):
        import tldr
        md = (ROOT / "TLDR-Agent-Finds.md").read_text(encoding="utf-8")
        md_urls = set()
        for line in md.splitlines():
            m = tldr.ROW_RE.match(line)
            if m:
                md_urls.add(m.group(4))
        db_urls = {r[4] for r in self.rows(LIVE_DB)}
        self.assertEqual(md_urls, db_urls, "TLDR-Agent-Finds.md is stale: run `python tldr.py export`")


if __name__ == "__main__":
    unittest.main()
