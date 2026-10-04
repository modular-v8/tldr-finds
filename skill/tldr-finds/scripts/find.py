#!/usr/bin/env python3
"""Query the TLDR finds database (free tools, repos and resources for builders). Read-only, stdlib only.

  find.py search WORDS... [--category C] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N] [--format text|json]
  find.py recent [--category C] [--since D] [--until D] [--limit N] [--format text|json]
  find.py categories [--format text|json]

Database: $TLDR_FINDS_DB if set, otherwise ../data/tldr-finds.db next to this script.
Deliberately avoids argparse/pathlib/json/re at startup: import time dominates query time (~1 ms).
Tip: `python -S find.py ...` skips site-packages loading (this script is stdlib-only) and starts faster.
"""
import os
import sqlite3
import sys

DB = os.environ.get("TLDR_FINDS_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tldr-finds.db")
SOURCE = "Curated from TLDR newsletters (https://tldr.tech)"
DEFAULT_LIMIT, MAX_LIMIT, MAX_TERMS, DESC_CHARS = 8, 50, 12, 220
STOPWORDS = {"a", "an", "and", "or", "the", "for", "to", "of", "in", "on", "with", "free", "tool", "tools",
             "that", "i", "need", "want", "like", "using", "use", "can", "me", "my", "is", "any"}
VALUE_FLAGS = {"--category", "--since", "--until", "--limit", "--format"}


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def connect():
    if not os.path.isfile(DB):
        fail(f"database not found: {DB} (set TLDR_FINDS_DB or rebuild the skill with build_skill.py)")
    path = os.path.abspath(DB).replace("\\", "/").replace("%", "%25").replace("?", "%3f").replace("#", "%23")
    con = sqlite3.connect(f"file:///{path.lstrip('/')}?mode=ro" if os.name == "nt" else f"file:{path}?mode=ro",
                          uri=True)
    con.row_factory = sqlite3.Row
    return con


def tokenize(text, limit):
    """Split on anything that is not a letter or digit (str.isalnum), stopping after `limit` tokens."""
    toks, cur = [], []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            toks.append("".join(cur)); cur = []
            if len(toks) >= limit:
                return toks
    if cur:
        toks.append("".join(cur))
    return toks


def is_date(s):
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and (s[:4] + s[5:7] + s[8:]).isdigit()


def terms(query):
    """Alphanumeric tokens only, so FTS5 operators and punctuation in agent input can never break MATCH."""
    toks = tokenize(query, MAX_TERMS * 2)
    keep = [t[:40] for t in toks if t not in STOPWORDS and len(t) > 1]
    return (keep or [t[:40] for t in toks])[:MAX_TERMS]


def fts_expr(toks, joiner):
    return f" {joiner} ".join(f'"{t}"*' for t in toks)


def filters(opts, where, params):
    for col, op, key in (("category", "=", "category"), ("date_found", ">=", "since"), ("date_found", "<=", "until")):
        if opts.get(key):
            where.append(f"f.{col} {op} ?"); params.append(opts[key])


def search_rows(con, query, opts):
    toks = terms(query)
    if not toks:
        return []
    for joiner in ("AND", "OR"):  # precise first, then broader
        where, params = ["finds_fts MATCH ?"], [fts_expr(toks, joiner)]
        filters(opts, where, params)
        sql = ("SELECT f.* FROM finds_fts JOIN finds f ON f.id = finds_fts.rowid "
               f"WHERE {' AND '.join(where)} ORDER BY bm25(finds_fts, 10.0, 1.0, 0.5), f.date_found DESC LIMIT ?")
        try:
            rows = con.execute(sql, params + [opts["limit"]]).fetchall()
        except sqlite3.OperationalError:  # FTS5 missing in this SQLite build, or index unreadable
            break
        if rows:
            return rows
    like, params = [], []  # last resort: substring match
    for t in toks:
        like.append("(f.name LIKE ? OR f.description LIKE ? OR f.category LIKE ?)")
        params += [f"%{t}%"] * 3
    where = ["(" + " OR ".join(like) + ")"]
    filters(opts, where, params)
    sql = f"SELECT f.* FROM finds f WHERE {' AND '.join(where)} ORDER BY f.date_found DESC LIMIT ?"
    return con.execute(sql, params + [opts["limit"]]).fetchall()


def recent_rows(con, opts):
    where, params = ["1=1"], []
    filters(opts, where, params)
    sql = f"SELECT f.* FROM finds f WHERE {' AND '.join(where)} ORDER BY f.date_found DESC, f.id DESC LIMIT ?"
    return con.execute(sql, params + [opts["limit"]]).fetchall()


def stats(con):
    return con.execute("SELECT count(*), max(date_found) FROM finds").fetchone()


def footer(con, shown=None):
    total, last = stats(con)
    count = f"{shown} of {total} finds" if shown is not None else f"{total} finds"
    return f"{SOURCE} · latest find {last} · {count}"


def short(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def emit(con, rows, opts):
    if opts["format"] == "json":
        import json
        total, last = stats(con)
        print(json.dumps({"source": SOURCE, "latest_find": last, "total_finds": total,
                          "results": [dict(r) for r in rows]}, ensure_ascii=False))
        return
    if not rows:
        print("No matches. Try fewer or broader words, drop --category, or run `categories`.")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['name']} [{r['category']}] {r['date_found']}\n   {short(r['description'], DESC_CHARS)}\n   {r['url']}")
    print(f"\n{footer(con, len(rows))}")


def cmd_categories(con, query, opts):
    rows = con.execute("SELECT category, count(*) c FROM finds GROUP BY 1 ORDER BY c DESC").fetchall()
    if opts["format"] == "json":
        import json
        print(json.dumps({"source": SOURCE, "categories": {r["category"]: r["c"] for r in rows}}))
        return
    for r in rows:
        print(f"{r['category']:<20} {r['c']}")
    print(f"\n{footer(con)}")


COMMANDS = {
    "search": lambda con, q, o: emit(con, search_rows(con, q, o), o),
    "recent": lambda con, q, o: emit(con, recent_rows(con, o), o),
    "categories": cmd_categories,
}


def parse(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__); sys.exit(0)
    cmd, words, opts, i = argv[0], [], {"limit": DEFAULT_LIMIT, "format": "text"}, 1
    if cmd not in COMMANDS:
        fail(f"unknown command {cmd!r}; use search, recent or categories")
    while i < len(argv):
        a = argv[i]
        if a in VALUE_FLAGS:
            if i + 1 >= len(argv):
                fail(f"{a} needs a value")
            opts[a[2:]] = argv[i + 1]; i += 2
        elif a.startswith("--") and "=" in a and a.split("=", 1)[0] in VALUE_FLAGS:
            k, v = a.split("=", 1); opts[k[2:]] = v; i += 1
        else:
            words.append(a); i += 1
    try:
        opts["limit"] = max(1, min(int(opts["limit"]), MAX_LIMIT))
    except ValueError:
        fail(f"--limit must be a number, got {opts['limit']!r}")
    if opts["format"] not in ("text", "json"):
        fail("--format must be text or json")
    for k in ("since", "until"):
        if opts.get(k) and not is_date(opts[k]):
            fail(f"--{k} must be YYYY-MM-DD, got {opts[k]!r}")
    query = " ".join(words).strip()
    if cmd == "search" and not query:
        fail('empty query. Usage: find.py search "what you need"; run `categories` to see what is covered')
    return cmd, query, opts


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, query, opts = parse(sys.argv[1:])
    try:
        COMMANDS[cmd](connect(), query, opts)
    except sqlite3.Error as e:
        fail(f"database error: {e}", 1)


if __name__ == "__main__":
    main()
