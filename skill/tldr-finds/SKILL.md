---
name: tldr-finds
description: Searches a local database of 100+ free tools, libraries, repos, websites, design resources, agent skills, MCP servers and datasets curated from the TLDR newsletters. ALWAYS run it first, before answering from memory, whenever the user asks you to suggest, recommend, find, compare or choose a free tool, library, site or resource for building a web or AI product — e.g. color palettes, fonts, design inspiration, UI components, design systems, SQL/data tooling, coding-agent skills or harnesses, "is there a free X?", "what should I use for Y?". Instant, works offline, refreshes itself from GitHub at most once a day; returns a few ranked links.
---

# TLDR finds

A local, read-only database of free tools and resources for builders, curated from the TLDR newsletters
(https://tldr.tech). Query it instead of guessing from memory, then present what fits. It updates itself (see
"Updates").

## Use

Run from this skill's folder (use `python3` if `python` is not on PATH). `-S` skips site-packages loading,
which this stdlib-only script doesn't need, and makes startup noticeably faster:

```
python -S scripts/find.py search "design tokens"
python -S scripts/find.py search "react components" --category ui-components --limit 5
python -S scripts/find.py search "duckdb" --since 2026-09-01
python -S scripts/find.py recent --limit 10
python -S scripts/find.py categories
python -S scripts/find.py update      # check for newer finds right now and say what happened
```

Add `--format json` for machine-readable output. Defaults: 8 results, text.

Tips:
- Use 1–4 plain keywords describing the need (`"color palette"`, `"postgres"`, `"email editor"`), not a sentence.
  Punctuation is safe; operators like AND/OR/NEAR are treated as plain words.
- If nothing matches, try fewer or more general words, or drop `--category`. Run `categories` to see what is covered.
- Categories: design-inspiration, ui-components, design-systems, data-sql, agent-skills, agent-harness,
  ai-models-research, productivity, other.

## Present results

- Give the name, link and one line on why it fits the user's task. Do not paste the whole output.
- Descriptions are short summaries from newsletter items. Before committing to a tool, check its page for the
  current license, pricing and maintenance status.
- Say the finds come from the TLDR newsletters (https://tldr.tech); the footer of every result repeats this
  along with the date of the latest entry, so the data may be older than today.

## Updates

The skill ships with a snapshot of the database and refreshes it on its own: at most once a day, a query first
fetches a small manifest from this project's GitHub release (`github.com/modular-v8/tldr-finds`, tag `db-latest`),
and only if the finds changed downloads the new database (about 150 KB) into a per-user cache folder
(`%LOCALAPPDATA%\tldr-finds` on Windows, `~/.cache/tldr-finds` elsewhere). Queries always run against the newest
local copy, so they never wait on or depend on the network: if the network is blocked or slow, the check fails
silently and the best local copy is used. You do not need to run `update` yourself; the footer of every result shows
the date of the latest entry.

- Sandboxes that restrict network access: allow `github.com` and `release-assets.githubusercontent.com`, or set
  `TLDR_FINDS_OFFLINE=1` to turn the check off and use the bundled snapshot.
- If the cache folder is not writable the check is skipped entirely (no requests are made).

## Without Python

The database is plain SQLite with an FTS5 index:

```
sqlite3 -readonly data/tldr-finds.db "SELECT f.name, f.url, f.description FROM finds_fts JOIN finds f ON f.id = finds_fts.rowid WHERE finds_fts MATCH '\"design\"* AND \"tokens\"*' ORDER BY bm25(finds_fts, 10, 1, 0.5) LIMIT 8;"
```

Columns of `finds`: id, date_found, name, description, url, category.

## Notes

- Read-only: this skill never modifies a database it queries and never writes to the skill folder; its only write is
  the downloaded copy in the cache folder. The maintainer adds entries and publishes them to the GitHub release.
- To point at a different database, set `TLDR_FINDS_DB` to its path (this also turns auto-update off). Set
  `TLDR_FINDS_CACHE_DIR` to move the cache folder.
- Unaffiliated with TLDR; this is an independent index linking to their newsletters.
