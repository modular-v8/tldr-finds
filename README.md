# TLDR finds

A fast, local lookup of **free tools, libraries, repos and resources for people building web and AI products**,
as an agent skill any coding agent can use. When your agent needs "a free color palette tool" or "something for
DuckDB", it queries a bundled, read-only SQLite database instead of guessing from memory, and gets a few ranked,
linked results back in milliseconds. No network, no API key, no server.

All finds are curated from the [TLDR newsletters](https://tldr.tech). Every result carries that credit.
This project is independent and unaffiliated with TLDR.

```
$ python -S scripts/find.py search "design tokens" --limit 2
1. Hue [design-inspiration] 2026-08-16
   Open-source skill (`git clone` install) that learns a brand from a URL, name, or screenshot and generates a full design system — tokens, typography, spacing, components, dark mode.
   https://github.com/dominikmartn/hue
2. ...

Curated from TLDR newsletters (https://tldr.tech) · latest find 2026-10-01 · 2 of 142 finds
```

## Install

Requires Python 3 (standard library only). No Python? The skill documents a plain `sqlite3` query as a fallback.

**Claude Code**: copy the skill folder `skill/tldr-finds` (or unzip `tldr-finds.skill` from the
[releases](../../releases)) into one of:

- `~/.claude/skills/tldr-finds/` for all your projects
- `<your-project>/.claude/skills/tldr-finds/` for one project

**Other agents that read `SKILL.md` skills**: copy the same folder into that agent's skills directory (see its docs).

**Agents without skills support**: copy the folder anywhere and paste [AGENTS.snippet.md](AGENTS.snippet.md) into your
`AGENTS.md` (or equivalent), replacing `<path-to>` with where you put it.

## Use

```
python -S scripts/find.py search "react components" --category ui-components --limit 5
python -S scripts/find.py search "duckdb" --since 2026-09-01 --format json
python -S scripts/find.py recent --limit 10
python -S scripts/find.py categories
```

Categories: design-inspiration, ui-components, design-systems, data-sql, agent-skills, agent-harness,
ai-models-research, productivity, other. Set `TLDR_FINDS_DB` to query a different database file.

See [skill/tldr-finds/SKILL.md](skill/tldr-finds/SKILL.md) for how agents are told to use it.

## How it stays fast

A query is about 1 ms; the rest is Python starting up. The script adds roughly 25–40 ms on top of the interpreter, and
`python -S` (skip site-packages; the script is stdlib-only) trims interpreter startup, which puts a whole call at
about 90 ms on a typical Windows machine. `find.py` opens the database read-only, sanitises input so FTS5 syntax
in agent-written queries can't break the search, ranks with bm25 (name weighted above description), and falls back
to substring matching when nothing matches.

## Repo layout

```
skill/tldr-finds/     the installable skill: SKILL.md, scripts/find.py, data/tldr-finds.db (snapshot)
tldr-finds.db         source of truth (SQLite + FTS5). Edit via tldr.py, never by hand.
tldr.py               maintainer CLI: add / search / list / stats / export / sql
build_skill.py        refresh the skill's snapshot, optionally build dist/tldr-finds.skill
TLDR-Agent-Finds.md   generated human-readable list of every find
AGENTS.snippet.md     paste-in for agents without skills support
tests/                smoke tests (python -m unittest discover -s tests)
spec.md               spec, plan and task status
```

## Maintaining

```
python tldr.py add 2026-10-05 "Name" "https://url" "What it is / how it helps" [--category C]
python build_skill.py
python -m unittest discover -s tests
```

Duplicate URLs are rejected; back up by copying `tldr-finds.db`. Release steps are in [CONTRIBUTING.md](CONTRIBUTING.md).
Want to suggest a tool? Open an issue with the **Suggest a find** template.

## License

Code: [MIT](LICENSE). The database is a curated list of links with short summaries; the linked tools belong to
their respective authors and have their own licenses, so check each before use.
