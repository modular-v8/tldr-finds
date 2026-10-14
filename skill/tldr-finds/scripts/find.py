#!/usr/bin/env python3
"""Query the TLDR finds database (free tools, repos and resources for builders). Stdlib only.

  find.py search WORDS... [--category C] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--limit N] [--format text|json]
  find.py recent [--category C] [--since D] [--until D] [--limit N] [--format text|json]
  find.py categories [--format text|json]
  find.py update                       check for newer finds now and say what happened

Queries are answered from a local file: the newest of the bundled ../data/tldr-finds.db and a per-user cache.
At most once a day the script fetches a small manifest from the project's GitHub release `db-latest` and, if the
finds changed, downloads and verifies the new database into the cache (any failure silently keeps the local copy).
Environment: TLDR_FINDS_OFFLINE=1 disables network checks; TLDR_FINDS_DB=path queries that file only (no updates);
TLDR_FINDS_CACHE_DIR=dir moves the cache (default %LOCALAPPDATA%\\tldr-finds, $XDG_CACHE_HOME/tldr-finds or
~/.cache/tldr-finds). Databases are only ever opened read-only; the cache folder is the only thing written.
Deliberately avoids argparse/pathlib/json/re/urllib at startup: import time dominates query time (~1 ms).
Tip: `python -S find.py ...` skips site-packages loading (this script is stdlib-only) and starts faster.
"""
import os
import sqlite3
import sys

OVERRIDE = os.environ.get("TLDR_FINDS_DB")
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tldr-finds.db")
RELEASE_URL = "https://github.com/modular-v8/tldr-finds/releases/download/db-latest/"
CHECK_EVERY, TIMEOUT, MAX_BYTES, MAX_MANIFEST = 86400, 3, 50_000_000, 65536
SOURCE = "Curated from TLDR newsletters (https://tldr.tech)"
DEFAULT_LIMIT, MAX_LIMIT, MAX_TERMS, DESC_CHARS = 8, 50, 12, 220
STOPWORDS = {"a", "an", "and", "or", "the", "for", "to", "of", "in", "on", "with", "free", "tool", "tools",
             "that", "i", "need", "want", "like", "using", "use", "can", "me", "my", "is", "any"}
VALUE_FLAGS = {"--category", "--since", "--until", "--limit", "--format"}


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def ro_uri(path):
    path = os.path.abspath(path).replace("\\", "/").replace("%", "%25").replace("?", "%3f").replace("#", "%23")
    return f"file:///{path.lstrip('/')}?mode=ro" if os.name == "nt" else f"file:{path}?mode=ro"


def open_ro(path):
    con = sqlite3.connect(ro_uri(path), uri=True)
    con.execute("PRAGMA trusted_schema = OFF")  # a database from the network must not run schema-defined functions
    return con


def cache_dir():
    """The per-user cache folder (not created here), or None if there is no sensible place for one."""
    d = os.environ.get("TLDR_FINDS_CACHE_DIR")
    if d:
        return d
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
    else:
        home = os.path.expanduser("~")
        base = os.environ.get("XDG_CACHE_HOME") or (os.path.join(home, ".cache") if home != "~" else None)
    return os.path.join(base, "tldr-finds") if base else None


def db_latest(path):
    """Latest find date in the database at `path`, or None if it is unreadable."""
    try:
        con = open_ro(path)
        try:
            return con.execute("SELECT max(date_found) FROM finds").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def pick_db():
    """TLDR_FINDS_DB if set; otherwise the cached download or the bundled snapshot, whichever is newer (cache on a tie)."""
    if OVERRIDE:
        return OVERRIDE
    folder = cache_dir()
    cached = os.path.join(folder, "tldr-finds.db") if folder else None
    if cached and os.path.isfile(cached):
        c = db_latest(cached)
        if c and c >= (db_latest(BUNDLED) or ""):
            return cached
    return BUNDLED


def connect():
    db = pick_db()
    if not os.path.isfile(db):
        fail(f"database not found: {db} (set TLDR_FINDS_DB or rebuild the skill with build_skill.py)")
    con = open_ro(db)
    con.row_factory = sqlite3.Row
    return con


# --- auto-update: runs at most once a day, never raises, and no query ever depends on it ---------------------------

def release_url():
    u = os.environ.get("TLDR_FINDS_RELEASE_URL", "")  # for tests against a local server; anything else is ignored
    if u.startswith("https://") or u.startswith("http://127.0.0.1"):
        return u if u.endswith("/") else u + "/"
    return RELEASE_URL


def fetch(url):
    """Open `url` with a short timeout; redirects must stay on HTTPS (except for the local test server)."""
    from urllib.request import HTTPRedirectHandler, Request, build_opener
    local = url.startswith("http://127.0.0.1")

    class Https(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://") and not local:
                raise ValueError(f"refusing non-HTTPS redirect to {newurl[:60]}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return build_opener(Https).open(Request(url, headers={"User-Agent": "tldr-finds-skill"}), timeout=TIMEOUT)


def valid_manifest(m):
    if not isinstance(m, dict) or m.get("schema") != 1:
        return False
    sha, size = m.get("sha256"), m.get("bytes")
    return (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
            and isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_BYTES)


def verify_db(path):
    """Raise ValueError unless `path` is a healthy database with finds in it."""
    con = open_ro(path)
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("database failed its integrity check")
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name IN ('finds', 'finds_fts')")}
        if have != {"finds", "finds_fts"}:
            raise ValueError("database is missing the finds tables")
        if con.execute("SELECT count(*) FROM finds").fetchone()[0] == 0:
            raise ValueError("database has no finds")
    finally:
        con.close()


def describe(path):
    try:
        con = open_ro(path)
        try:
            n, last = con.execute("SELECT count(*), max(date_found) FROM finds").fetchone()
        finally:
            con.close()
        return f"{n} finds, latest {last}"
    except sqlite3.Error:
        return "unreadable"


def swap_in(src, dst):
    import time
    for attempt in range(4):  # on Windows a concurrent reader can briefly block the swap
        try:
            return os.replace(src, dst)
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.2)


def update_cache(folder):
    import hashlib
    import json
    base = release_url()
    with fetch(base + "manifest.json") as r:
        body = r.read(MAX_MANIFEST + 1)
    if len(body) > MAX_MANIFEST:
        return "ignored an oversized manifest; keeping the current copy"
    try:
        m = json.loads(body)
    except ValueError:
        m = None
    if not valid_manifest(m):
        return "ignored an invalid manifest; keeping the current copy"
    cached, cached_manifest = os.path.join(folder, "tldr-finds.db"), os.path.join(folder, "manifest.json")
    try:
        with open(cached_manifest, encoding="utf-8") as f:
            have = json.load(f).get("sha256")
    except (OSError, ValueError, AttributeError):
        have = None
    if have == m["sha256"] and os.path.isfile(cached):
        return f"already current: {describe(cached)}"
    before = describe(pick_db())
    tmp = os.path.join(folder, f"tldr-finds.db.{os.getpid()}.tmp")
    try:
        digest, size = hashlib.sha256(), 0
        with fetch(base + "tldr-finds.db") as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > m["bytes"]:
                    raise ValueError("download is larger than the manifest says")
                digest.update(chunk)
                f.write(chunk)
        if size != m["bytes"] or digest.hexdigest() != m["sha256"]:
            raise ValueError("download does not match the manifest")
        verify_db(tmp)
        swap_in(tmp, cached)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    mtmp = os.path.join(folder, f"manifest.json.{os.getpid()}.tmp")
    with open(mtmp, "w", encoding="utf-8") as f:
        json.dump(m, f)
    swap_in(mtmp, cached_manifest)
    return f"updated: {before} -> {describe(cached)}"


def refresh(force=False):
    """Check for a newer database (at most once a day unless `force`). Never raises; returns a one-line status."""
    if OVERRIDE:
        return "auto-update is off because TLDR_FINDS_DB is set"
    if os.environ.get("TLDR_FINDS_OFFLINE") == "1":
        return "auto-update is off because TLDR_FINDS_OFFLINE=1"
    folder = cache_dir()
    if not folder:
        return "no cache folder available; using the bundled snapshot"
    try:
        os.makedirs(folder, exist_ok=True)
        stamp = os.path.join(folder, "last-check")
        if not force:
            import time
            try:
                age = time.time() - os.stat(stamp).st_mtime
                if -60 < age < CHECK_EVERY:
                    return "checked within the last day"
            except OSError:
                pass
        try:
            os.utime(stamp)  # record the attempt before the request, so a failure is not retried on every query
        except OSError:
            open(stamp, "ab").close()
    except (OSError, ValueError) as e:  # read-only home, sandbox: nowhere to remember the check, so make no request at all
        return f"cache folder is not writable ({e.__class__.__name__}); using the bundled snapshot"
    try:
        return update_cache(folder)
    except Exception as e:
        return f"could not update ({e.__class__.__name__}: {str(e)[:80]}); using the best local copy"


# --- queries ------------------------------------------------------------------------------------------------------

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
    if cmd not in COMMANDS and cmd != "update":
        fail(f"unknown command {cmd!r}; use search, recent, categories or update")
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
    if cmd == "update":
        print(refresh(force=True))
        return
    refresh()
    try:
        COMMANDS[cmd](connect(), query, opts)
    except sqlite3.Error as e:
        fail(f"database error: {e}", 1)


if __name__ == "__main__":
    main()
