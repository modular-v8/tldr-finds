#!/usr/bin/env python3
"""TLDR finds database: SQLite is the source of truth, the Markdown is generated from it.

  python tldr.py import [file.md]          one-time import of the old Markdown table
  python tldr.py add DATE NAME URL DESC [--category C]   add one find (skips duplicate URLs)
  python tldr.py search "words" [--category C] [--since D] [--until D] [--limit N]
  python tldr.py list [--category C] [--since D] [--until D] [--limit N]
  python tldr.py stats
  python tldr.py export                    regenerate TLDR-Agent-Finds.md
  python tldr.py sql "SELECT ..."          read-only ad-hoc query

Dates are YYYY-MM-DD. `add` regenerates the Markdown automatically unless --no-export.
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "tldr-finds.db"
MD = HERE / "TLDR-Agent-Finds.md"

HEADER = """# Useful Free Tools & Repos — from TLDR Newsletters

> Generated from `tldr-finds.db` by `tldr.py export` — do not edit by hand; use `tldr.py add`.

Curated list of free tools, websites, and repos (found in TLDR AI / Design / Data / Tech newsletters) that could be useful for everyday work — not limited to AI coding agents. This includes AI-agent tooling, design resources (fonts, gradients, UI kits), and general productivity/writing/research tools. Paid-only products, general AI news, funding news, and hardware news are intentionally excluded.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS finds (
  id          INTEGER PRIMARY KEY,
  date_found  TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT NOT NULL,
  url         TEXT NOT NULL UNIQUE,
  category    TEXT
);
CREATE INDEX IF NOT EXISTS idx_finds_date ON finds(date_found);
CREATE INDEX IF NOT EXISTS idx_finds_cat  ON finds(category);
CREATE TABLE IF NOT EXISTS batch_notes (
  id INTEGER PRIMARY KEY, date_found TEXT NOT NULL, note TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS finds_fts USING fts5(
  name, description, category, content='finds', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS finds_ai AFTER INSERT ON finds BEGIN
  INSERT INTO finds_fts(rowid,name,description,category) VALUES (new.id,new.name,new.description,new.category);
END;
CREATE TRIGGER IF NOT EXISTS finds_ad AFTER DELETE ON finds BEGIN
  INSERT INTO finds_fts(finds_fts,rowid,name,description,category) VALUES ('delete',old.id,old.name,old.description,old.category);
END;
CREATE TRIGGER IF NOT EXISTS finds_au AFTER UPDATE ON finds BEGIN
  INSERT INTO finds_fts(finds_fts,rowid,name,description,category) VALUES ('delete',old.id,old.name,old.description,old.category);
  INSERT INTO finds_fts(rowid,name,description,category) VALUES (new.id,new.name,new.description,new.category);
END;
"""

# First match wins, so more specific categories come first. Override with --category.
CATEGORY_RULES = [
    ("design-inspiration", r"gallery|inspiration|palette|color|colour|font|typograph|typeface|gradient|logo|branding|emoji|mockup|motion|moodboard|dither|ascii|glass|museum|artworks"),
    ("ui-components", r"react|tailwind|component|ui kit|ui blocks|skeleton|loading|css|particle|web components|shadcn|email editor"),
    ("design-systems", r"design system|design\.md|design skill|ux|onboarding"),
    ("data-sql", r"duckdb|postgres|sql|dbt|parquet|snowflake|mysql|lakehouse|replication|dataset|record linkage|neo4j"),
    ("agent-skills", r"\bskill|skills\b|npx skills|plugin"),
    ("agent-harness", r"harness|coding agent|agents?\b.*(terminal|sandbox|runtime|orchestrat)|mcp|sandbox|orchestrat|memory"),
    ("ai-models-research", r"model|benchmark|inference|rl\b|kernels|gpu|llm"),
    ("productivity", r"editor|recorder|notetaker|pdf|document|screenshot|photo|video|keyboard|shortcut"),
]


def guess_category(name, desc):
    text = f"{name} {desc}".lower()
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, text):
            return cat
    return "other"


BUSY_SECONDS = 30  # wait this long for another process (e.g. a scheduled run) to release the write lock


def connect(readonly=False):
    """Read-only commands open the file read-only and never touch the schema; writers wait for locks."""
    if readonly and DB.is_file():
        con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True, timeout=BUSY_SECONDS)
    else:
        con = sqlite3.connect(DB, timeout=BUSY_SECONDS)
        # PERSIST reuses the journal file instead of deleting it at every commit. Sandboxed scheduled runs that may
        # create/write files in this folder but not delete them otherwise fail every write with "disk I/O error".
        # Not remembered by the file, so it is set on each writer connection.
        con.execute("PRAGMA journal_mode = PERSIST")
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name IN ('finds','finds_fts','finds_au')")}
        if len(have) < 3:  # fresh or partial database: create what is missing (all statements are IF NOT EXISTS)
            con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\S.*?)\s*\|$")


def cmd_import(args):
    src = Path(args.file) if args.file else HERE / "TLDR-Agent-Finds.original.md"
    con = connect()
    n = dup = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            date, name, desc, url = m.groups()
            cat = guess_category(name, desc)
            try:
                con.execute("INSERT INTO finds(date_found,name,description,url,category) VALUES (?,?,?,?,?)",
                            (date, name, desc, url, cat))
                n += 1
            except sqlite3.IntegrityError:
                dup += 1
        elif line.startswith("**Note on the "):
            d = re.search(r"\d{4}-\d{2}-\d{2}", line)
            note = re.sub(r"^\*\*Note on the [^*]*\*\*\s*", "", line)
            con.execute("INSERT INTO batch_notes(date_found,note) VALUES (?,?)", (d.group(0) if d else "", note))
    con.commit()
    print(f"imported {n} finds ({dup} duplicates skipped)")
    cmd_export(args)


def cmd_add(args):
    con = connect()
    cat = args.category or guess_category(args.name, args.desc)
    try:
        con.execute("INSERT INTO finds(date_found,name,description,url,category) VALUES (?,?,?,?,?)",
                    (args.date, args.name, args.desc, args.url, cat))
    except sqlite3.IntegrityError:
        row = con.execute("SELECT date_found,name FROM finds WHERE url=?", (args.url,)).fetchone()
        sys.exit(f"duplicate URL, already stored: {row['date_found']} {row['name']}")
    con.commit()
    print(f"added [{cat}] {args.name}")
    if not args.no_export:
        cmd_export(args)


def _filters(args, where, params):
    if args.category:
        where.append("f.category = ?"); params.append(args.category)
    if args.since:
        where.append("f.date_found >= ?"); params.append(args.since)
    if args.until:
        where.append("f.date_found <= ?"); params.append(args.until)


def _print(rows):
    for r in rows:
        print(f"{r['date_found']}  [{r['category']}]  {r['name']}\n    {r['description']}\n    {r['url']}")
    print(f"\n{len(rows)} result(s)")


def cmd_search(args):
    con = connect(readonly=True)
    where, params = ["finds_fts MATCH ?"], [args.query]
    _filters(args, where, params)
    sql = ("SELECT f.* FROM finds_fts JOIN finds f ON f.id = finds_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY rank LIMIT ?")
    _print(con.execute(sql, params + [args.limit]).fetchall())


def cmd_list(args):
    con = connect(readonly=True)
    where, params = ["1=1"], []
    _filters(args, where, params)
    sql = f"SELECT f.* FROM finds f WHERE {' AND '.join(where)} ORDER BY date_found DESC, id DESC LIMIT ?"
    _print(con.execute(sql, params + [args.limit]).fetchall())


def cmd_stats(args):
    con = connect(readonly=True)
    total, first, last = con.execute("SELECT count(*),min(date_found),max(date_found) FROM finds").fetchone()
    print(f"{total} finds, {first} to {last}\n\nby category:")
    for r in con.execute("SELECT category,count(*) c FROM finds GROUP BY 1 ORDER BY c DESC"):
        print(f"  {r['category']:<20} {r['c']}")
    print("\nby month:")
    for r in con.execute("SELECT substr(date_found,1,7) m,count(*) c FROM finds GROUP BY 1 ORDER BY 1"):
        print(f"  {r['m']}  {r['c']}")


def cmd_sql(args):
    con = connect(readonly=True)
    con.execute("PRAGMA query_only = ON")
    for r in con.execute(args.query).fetchall():
        print(" | ".join(str(v) for v in tuple(r)))


def cmd_export(args):
    con = connect(readonly=True)
    out = [HEADER, "| Date Found | Name | What It Is / How It Helps | Link |", "|---|---|---|---|"]
    for r in con.execute("SELECT * FROM finds ORDER BY date_found, id"):
        out.append(f"| {r['date_found']} | {r['name']} | {r['description']} | {r['url']} |")
    notes = con.execute("SELECT * FROM batch_notes ORDER BY date_found, id").fetchall()
    if notes:
        out.append("")
        for n in notes:
            out.append(f"**Note on the {n['date_found']} batch:** {n['note']}")
    MD.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {MD.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import"); s.add_argument("file", nargs="?"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("add")
    s.add_argument("date"); s.add_argument("name"); s.add_argument("url"); s.add_argument("desc")
    s.add_argument("--category"); s.add_argument("--no-export", action="store_true"); s.set_defaults(fn=cmd_add)
    for name, fn in (("search", cmd_search), ("list", cmd_list)):
        s = sub.add_parser(name)
        if name == "search":
            s.add_argument("query")
        s.add_argument("--category"); s.add_argument("--since"); s.add_argument("--until")
        s.add_argument("--limit", type=int, default=25); s.set_defaults(fn=fn)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("export").set_defaults(fn=cmd_export)
    s = sub.add_parser("sql"); s.add_argument("query"); s.set_defaults(fn=cmd_sql)

    args = p.parse_args()
    try:
        args.fn(args)
    except sqlite3.OperationalError as e:
        sys.exit(f"database error: {e}\n"
                 f"  - 'locked': another process is writing {DB.name}; retry later or avoid overlapping runs\n"
                 "  - 'readonly' / 'unable to open': this process cannot write the file or its folder "
                 "(sandbox, sync client, permissions)\n"
                 f"  - 'disk I/O error': often a sandbox that cannot delete files here; if {DB.name}-journal is left over, "
                 "run any tldr.py command from a normal terminal so SQLite rolls it back")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
