# spec: hosted database (phase 2)

## outcome

Anyone who installs the `tldr-finds` skill from GitHub gets the maintainer's latest finds automatically, within a
day of them being added, without reinstalling. The maintainer's daily scheduled run publishes the database to a
GitHub Release file that is replaced in place, so there are no daily commits and git history stops growing with
database copies. The skill still works offline and stays fast: the bundled snapshot remains as a fallback.

## in scope

- A release `db-latest` (pre-release) on `modular-v8/tldr-finds` holding two files that are replaced daily:
  `tldr-finds.db` and `manifest.json` (sha256, finds count, latest date).
- `build_skill.py --publish`: build the snapshot, write the manifest, upload both with `gh release upload --clobber`.
- `find.py` auto-update: at most once a day, fetch the manifest; if the content changed, download, verify and cache
  the new database in a per-user cache folder; query whichever copy (cache or bundled) is newest.
- `find.py update` command to force a refresh; `TLDR_FINDS_OFFLINE=1` to disable network checks.
- Stop tracking the root `tldr-finds.db` and `TLDR-Agent-Finds.md` in git (both stay on the maintainer's machine).
- Tests: cover the update path without the network; skip the root-DB/Markdown sync checks when those files are
  absent (CI).
- Docs: SKILL.md, README, CONTRIBUTING, AGENTS.snippet.md and CHANGELOG describe the auto-update, the network use
  and the new maintainer flow; update the daily scheduled task's prompt to run the publish step.

## out of scope

- A hosted query API or cloud database (Turso, Supabase, D1, Datasette): adds per-query network latency, a public
  access key and an offline failure mode, for a ~150 KB dataset that changes once a day.
- Moving the daily curation itself to GitHub Actions: it is done by a Claude scheduled task and needs no change
  beyond adding the publish step.
- Delta/partial updates: the whole file is small enough to download in one go.
- Auto-updating `find.py` code itself: users reinstall once to get the auto-update; after that only data changes.
- Rewriting existing git history to remove past database commits.

## users & context

- **Skill users:** developers running coding agents (mainly Claude Code; also other `SKILL.md`-aware agents and
  agents using `AGENTS.snippet.md`) on Windows, macOS or Linux. The agent calls `find.py` many times a day; the user
  rarely looks at the skill directly. Some environments block or restrict network access (Claude Code sandbox,
  claude.ai code execution, corporate proxies).
- **Maintainer:** one person (Bhavya). Finds are added daily by a scheduled Claude task that runs `tldr.py add`,
  possibly inside a sandbox that can create and write files but not delete them. Occasional manual runs from a
  terminal also happen.

## constraints

- Stack: Python 3.9+ standard library only for `find.py` (no `requests`); SQLite with FTS5; `gh` CLI for publishing
  (installed and logged in as `modular-v8`).
- `find.py` must stay fast: no heavy imports at module top level (`urllib`, `json`, `hashlib` imported lazily, only
  when a refresh is due); queries that don't need a refresh must not touch the network.
- The skill never writes to the skill folder or to any database it queries (stays read-only); the only write is the
  per-user cache.
- `find.py`'s existing commands, flags and output format are the public API (SemVer): additions only.
- Cost: $0. Only GitHub (public repo, release files) is used; no new accounts or secrets.

## data & integrations

- **GitHub Releases** (public, no auth to download): fixed URLs
  `https://github.com/modular-v8/tldr-finds/releases/download/db-latest/manifest.json` and
  `.../db-latest/tldr-finds.db`. Downloads redirect to a signed storage URL, so change detection uses the manifest's
  sha256, not HTTP ETags. Uploading requires the maintainer's `gh` login.
- **manifest.json:** `{"schema": 1, "sha256": "<hex>", "bytes": N, "finds": N, "latest": "YYYY-MM-DD",
  "published": "<UTC ISO time>"}`.
- **User cache:** a folder per user (`%LOCALAPPDATA%\tldr-finds\` on Windows, `$XDG_CACHE_HOME/tldr-finds/` or
  `~/.cache/tldr-finds/` elsewhere) holding the downloaded `tldr-finds.db`, the last `manifest.json` and a timestamp
  of the last check.
- **Database:** unchanged schema (`finds`, `finds_fts`, `batch_notes`).

## prior decisions

- Host as a rolling GitHub Release file, not a commit on `main`: the maintainer does not want daily commits or a
  growing git history.
- Keep SQLite as the format and keep a bundled snapshot in the skill as the offline fallback.
- Use a sha256 manifest for change detection (release downloads redirect, so ETags are unreliable).
- Stop tracking the root `tldr-finds.db` and `TLDR-Agent-Finds.md` in git.
- Daily updates come from a Claude scheduled task; publishing must work unattended.
- No cloud database or hosted query API (see out of scope).

## requirements

### always active

- The skill SHALL answer every query from a local SQLite file (cache or bundled snapshot); a query SHALL never
  depend on the network succeeding.
- The skill SHALL use whichever available copy (cache or bundled) has the newest `latest` date, preferring the
  cache on a tie; `TLDR_FINDS_DB`, when set, SHALL override both and disable auto-update.
- The skill SHALL make at most one update check per 24 hours per user, and none when `TLDR_FINDS_OFFLINE=1`.
- Every network request SHALL use HTTPS, a short timeout (about 3 s per request) and only the fixed release URLs.
- The skill SHALL open databases read-only and with untrusted-schema features disabled.
- The result footer SHALL keep showing the latest find date, so users can see how fresh their data is.
- Publishing SHALL create no git commits and modify no tracked files.

### event-driven

- WHEN a query runs and the last check is older than 24 hours, the skill SHALL fetch the manifest; if its sha256
  differs from the cached copy, it SHALL download the database, verify it and replace the cache, then answer the
  query from the newest copy.
- WHEN `find.py update` runs, the skill SHALL check immediately (ignoring the 24-hour limit) and print what it did:
  updated (old → new count and date), already current, or why it could not update.
- WHEN the maintainer runs `python build_skill.py --publish`, the tool SHALL build and check the snapshot, write
  `manifest.json`, upload the database first and the manifest second to `db-latest`, and print the published count
  and date.
- WHEN the `db-latest` release does not exist, `--publish` SHALL create it as a pre-release before uploading.
- WHEN the daily scheduled task finishes adding finds, it SHALL run `python build_skill.py --publish`.

### unwanted behavior

- IF the network is unavailable, blocked, slow or returns an error, the skill SHALL silently use the best local
  copy, record the check time (so it does not retry on every query) and still exit 0.
- IF the downloaded file's sha256 or size does not match the manifest, or it fails `PRAGMA quick_check`, lacks the
  `finds`/`finds_fts` tables or has zero rows, the skill SHALL discard it and keep the current copy.
- IF the manifest is malformed or has an unknown `schema` version, the skill SHALL ignore it and keep the current
  copy.
- IF the cache folder cannot be created or written (read-only home, sandbox), the skill SHALL fall back to the
  bundled snapshot without error.
- IF two agent processes refresh at the same time, the cache SHALL never be left half-written (write to a temp file,
  then atomic replace).
- IF `gh` is missing, not logged in or the upload fails, `--publish` SHALL exit non-zero with a message naming the
  cause; the local database keeps the new finds and the next successful publish uploads them.
- IF the snapshot fails its integrity checks, `--publish` SHALL upload nothing.

## acceptance criteria

- [ ] After `python build_skill.py --publish`, the `db-latest` release shows `tldr-finds.db` and `manifest.json`, the
      manifest's sha256 matches the uploaded file, and `git status` shows no new changes to tracked files.
- [ ] A fresh install of the skill (bundled snapshot older than the release) in a clean folder: `find.py update`
      prints "updated", and `find.py recent --limit 1` shows the same latest date as the published manifest.
- [ ] With the network disabled (or `TLDR_FINDS_OFFLINE=1`), every command still works and exits 0 using the
      bundled or cached copy.
- [ ] A second query within 24 hours makes no network request (verified by a test with a counting local server).
- [ ] A tampered download (wrong sha256), a corrupt file and an empty database are each rejected and the previous copy
      is kept (automated tests).
- [ ] A read-only or missing cache folder does not cause an error (automated test).
- [ ] `python -m unittest discover -s tests` passes locally and in GitHub Actions on Windows, macOS and Linux (Python
      3.9 and 3.13), with the root `tldr-finds.db` and `TLDR-Agent-Finds.md` no longer in the repo.
- [ ] A query that doesn't trigger a check stays within the existing speed budget (`tests/bench.py`, about 100 ms).
- [ ] The scheduled task's next daily run publishes without manual help.
