# TLDR finds skill — spec & plan

Source of truth for the work. Update this file when decisions change.

## Goal

When any coding agent is building a product (web, AI or otherwise) and needs a free tool, library,
design resource or dataset, it queries the TLDR finds database and gets a few relevant, trustworthy
results back in well under a second. The skill is published and maintained as a public Git repo.

## Context (verified)

- `tldr-finds.db`: SQLite, 142 finds + 1 batch note, FTS5 index `finds_fts` (name, description, category).
- `finds(id, date_found, name, description, url UNIQUE, category)`; categories: design-inspiration,
  ui-components, design-systems, data-sql, agent-skills, agent-harness, ai-models-research,
  productivity, other.
- `tldr.py` is the maintainer CLI. Its `connect()` runs the schema script and opens read-write on every
  call, so the skill gets its own read-only query path instead of reusing it.
- Source of the finds: TLDR newsletters, https://tldr.tech

## Requirements

- **R1 Portable:** one skill folder in the open `SKILL.md` format (name, description, instructions,
  `scripts/`). Agents without skills support get an `AGENTS.md` snippet pointing at the same script.
- **R2 Fast:** a single stdlib-only Python script opens the DB read-only (`file:...?mode=ro`), no schema
  setup, no network, no server process. Target < 100 ms per query.
- **R3 Agent-shaped output:** compact name, URL, category, date, one-line description. Default limit 8.
  `--format json` available.
- **R4 Robust queries:** raw agent input (`next.js auth`, `design-tokens`) must never break FTS5 `MATCH`.
  Sanitise to quoted prefix terms, rank with bm25 (name weighted above description), fall back to
  `LIKE` if nothing matches.
- **R5 Read-only:** the skill never writes to the DB. `add` stays in `tldr.py` (maintainer only).
- **R6 Self-describing:** `categories` command lists categories with counts.
- **R7 Attribution:** every result footer, the SKILL.md and the README credit the TLDR newsletters with a
  link, e.g. `Curated from TLDR newsletters (https://tldr.tech) · DB as of DATE · N finds`. Include an
  "unaffiliated with TLDR" note in the README. (Credit and link the source; not a legal review.)
- **R8 Maintainable in the open:** the repo holds both the data and the skill. Flow: `tldr.py add` →
  `build_skill.py` → changelog/version → tag.
- **R9 Bundled snapshot:** the DB ships inside the skill (works offline, instant, portable).
  `TLDR_FINDS_DB` env var overrides the path (e.g. the maintainer's live copy).
- **Fallback for no Python:** SKILL.md also documents the raw `sqlite3` CLI query.

Out of scope for v1: MCP server, embeddings, hosted API.

## Acceptance checks

- Cold query < 100 ms.
- Ten ugly queries (punctuation, empty string, very long string, FTS operators) never crash.
- Typical output < ~600 tokens.
- Works when the skill folder is copied elsewhere with no repo present.

## Repo layout

```
tldr-finds/                     (git repo root)
  skill/tldr-finds/             <- the installable skill
    SKILL.md
    scripts/find.py
    data/tldr-finds.db          read-only snapshot, built by build_skill.py
                                (footer date/count are read from the DB itself; no meta.json)
  tldr.py                       maintainer CLI (add / import / export)
  build_skill.py                snapshot DB + stamp date/count
  TLDR-Agent-Finds.md           generated human-readable list
  AGENTS.snippet.md             paste-in for agents without skills support
  README.md  LICENSE  CHANGELOG.md  CONTRIBUTING.md  .gitignore
```

## Licensing

MIT for the code. README states the data is a curated link list with source credit. Owner chooses the final license.

## Release flow

`tldr.py add` → `build_skill.py` → bump version in `CHANGELOG.md` → tag → `.skill` zip on the GitHub release.
CI rebuilds, checks DB and generated Markdown agree, runs smoke tests.

## Install paths (README)

Copy `skill/tldr-finds` into `.claude/skills/` (Claude Code) or the equivalent skills directory of other
agents; paste `AGENTS.snippet.md` for agents without skills support; `.skill` zip on the release page.

## Tasks

| # | Task | Done when | Status |
|---|---|---|---|
| 1 | `find.py`: read-only connection, query sanitiser, bm25 ranking, `LIKE` fallback | passes the ugly-query set | done (17 ugly queries, 0 crashes; ro enforced) |
| 2 | `recent`, `categories`, `--format json`, TLDR attribution footer | output matches R3, R7 | done |
| 3 | `SKILL.md` (trigger description, sqlite3 fallback, credit line) + `AGENTS.snippet.md` | a fresh agent uses it unprompted | written; "fresh agent uses it unprompted" still to be verified in task 7/8 |
| 4 | `build_skill.py` (snapshot DB, stamp date/count) | one command rebuilds the skill | done (`--zip` also writes `dist/tldr-finds.skill`) |
| 5 | Repo files: README, LICENSE, CHANGELOG, CONTRIBUTING, `.gitignore`, issue template | repo is publish-ready | done (MIT; LICENSE holder "Bhavya Parekh" is derived from the account email, confirm) |
| 6 | Smoke tests + GitHub Action | CI green | tests done (19, pass on Python 3.9 and 3.14); workflow written, not yet run on GitHub |
| 7 | Timing and token-size checks | < 100 ms, ~600 tokens | done: `python -S` median 66-94 ms (bare `-S` interpreter 34 ms); 190-460 tokens per query; `tests/bench.py` |
| 8 | Release: tag, `.skill` zip, install check from a clean dir | install works from scratch | done locally: zip installs into a clean project; fresh-agent test below; local git commit + tag v0.1.0 (not pushed) |

Current scope: tasks 1–8 done locally. Remaining: push to GitHub and confirm CI is green.

## Implementation notes (tasks 1–4)

- Measured on Windows, Python 3.14: ~88 ms wall per query vs ~56 ms for bare `python -c pass`; the query
  itself is ~1 ms. The first version used argparse/pathlib and took ~245 ms, so `find.py` now hand-parses
  args, builds the DB URI without pathlib and imports `json` lazily. Keep it that way: import time is the cost.
- Output for a typical query is ~1.4 KB (~340 tokens).
- `dist/` (zip output) must be in `.gitignore` (task 5).
- `sqlite3` CLI fallback SQL in SKILL.md was verified through Python, not the CLI (not installed here).

## Housekeeping before first commit

- `.gitignore` for `.decisions/` (local session notes).
- Drop `TLDR-Agent-Finds.original.md` (the DB supersedes it).

## Open decisions

- License choice (default MIT).
- Whether to keep `TLDR-Agent-Finds.original.md` in the repo.

## Implementation notes (tasks 5-8)

- `-S` is the documented way to run the script: it skips site-packages (not needed, stdlib only) and saved ~30-40 ms
  here. `re` was also dropped for the same reason (~20 ms import).
- Wall time on this machine is noisy (bare `python -c pass` ranged 56-100 ms between runs), so judge by the
  overhead over bare `python -S` (~40 ms), not the absolute number.
- `TLDR-Agent-Finds.original.md` is gitignored, not deleted; delete it yourself if you don't want the local backup.
- **Fresh-agent trigger test** (`claude -p` in a clean project containing only the unzipped skill, prompts that never
  mention the skill; 3 task prompts + 1 control): Sonnet used the skill and ran `find.py` on 3/3 and correctly
  skipped the control; Haiku used it on 0/3 even after the description was made more directive (it answers from
  memory). Known limitation: small models may not call the skill unprompted; `AGENTS.snippet.md` (always in context)
  is the stronger nudge for those.
