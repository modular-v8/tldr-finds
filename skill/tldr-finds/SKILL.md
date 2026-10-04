---
name: tldr-finds
description: Searches a local database of 100+ free tools, libraries, repos, websites, design resources, agent skills, MCP servers and datasets curated from the TLDR newsletters. ALWAYS run it first, before answering from memory, whenever the user asks you to suggest, recommend, find, compare or choose a free tool, library, site or resource for building a web or AI product — e.g. color palettes, fonts, design inspiration, UI components, design systems, SQL/data tooling, coding-agent skills or harnesses, "is there a free X?", "what should I use for Y?". Instant, offline, read-only; returns a few ranked links.
---

# TLDR finds

A local, read-only database of free tools and resources for builders, curated from the TLDR newsletters
(https://tldr.tech). Query it instead of guessing from memory, then present what fits.

## Use

Run from this skill's folder (use `python3` if `python` is not on PATH). `-S` skips site-packages loading,
which this stdlib-only script doesn't need, and makes startup noticeably faster:

```
python -S scripts/find.py search "design tokens"
python -S scripts/find.py search "react components" --category ui-components --limit 5
python -S scripts/find.py search "duckdb" --since 2026-09-01
python -S scripts/find.py recent --limit 10
python -S scripts/find.py categories
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

## Without Python

The database is plain SQLite with an FTS5 index:

```
sqlite3 -readonly data/tldr-finds.db "SELECT f.name, f.url, f.description FROM finds_fts JOIN finds f ON f.id = finds_fts.rowid WHERE finds_fts MATCH '\"design\"* AND \"tokens\"*' ORDER BY bm25(finds_fts, 10, 1, 0.5) LIMIT 8;"
```

Columns of `finds`: id, date_found, name, description, url, category.

## Notes

- Read-only: this skill never modifies the database. The maintainer adds entries in the source repo and rebuilds the skill.
- To point at a different database, set `TLDR_FINDS_DB` to its path.
- Unaffiliated with TLDR; this is an independent index linking to their newsletters.
