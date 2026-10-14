# Plan: hosted database (phase 2)

## Approach Summary

The maintainer's machine stays the only place finds are written. After the daily scheduled task adds finds,
`build_skill.py --publish` builds a checked snapshot in a temporary folder and uploads it, then a small
`manifest.json`, to a pre-release called `db-latest` that is overwritten in place, using the `gh` CLI. No commits
are made. On the user side, `find.py` gets a small update step: at most once a day it downloads the manifest. If the
fingerprint (sha256) differs from its cached copy, it downloads the database, checks it and swaps it into a per-user
cache. Every query is then answered from whichever local copy, cache or bundled snapshot, has the newest finds. Any
failure falls back silently to the best local copy. The root database and generated Markdown leave git. The bundled
snapshot inside the skill is refreshed only at versioned releases.

## Architecture

```
MAINTAINER (this PC)                                   GITHUB (public)              USER'S MACHINE
--------------------                                   ---------------              --------------
Claude scheduled task (desktop app)
  └─ python tldr.py add ...      -> tldr-finds.db (untracked, local source of truth)
  └─ python build_skill.py --publish
       1. snapshot -> <tmpdir>/tldr-finds.db   (checks: integrity, fts == rows, rows > 0)
       2. write    -> <tmpdir>/manifest.json   (sha256, bytes, finds, latest, published)
       3. gh release create db-latest --prerelease   (only if missing)
       4. gh release upload db-latest tldr-finds.db --clobber      ──►  releases/download/db-latest/tldr-finds.db
       5. gh release upload db-latest manifest.json --clobber      ──►  releases/download/db-latest/manifest.json
       6. fetch public manifest, confirm sha256 matches               │
                                                                      │   find.py <any command>
                                                                      │     maybe_refresh()  (skipped if checked < 24 h ago,
                                                                      │       │                TLDR_FINDS_OFFLINE=1 or TLDR_FINDS_DB set)
                                                                      └──── ◄─┤ GET manifest.json (3 s timeout)
                                                                            ◄─┤ GET tldr-finds.db if sha256 changed
                                                                              │ verify -> atomic replace in user cache
                                                                            pick_db(): newest of cache / bundled
                                                                            query (read-only, trusted_schema OFF)
```

`find.py` stays a single self-contained script, because the skill is installed by copying one folder.

## Tech Stack & Key Decisions

| Decision | Choice | Why |
|---|---|---|
| HTTP client in `find.py` | `urllib.request` (stdlib), imported lazily inside the refresh function | Constraint: stdlib only, no top-level heavy imports. Follows the 302 redirect to GitHub's storage URL by default. |
| Change detection | Compare the manifest's `sha256` with the cached manifest's | Prior decision: release downloads redirect, so ETags are unreliable. |
| Download integrity | Stream to `<cache>/tldr-finds.db.<pid>.tmp` while hashing; size cap = manifest `bytes` (hard max 50 MB); then sha256 match, `PRAGMA quick_check`, `finds` + `finds_fts` present, `count(*) > 0`; then `os.replace` | Spec: unwanted behavior (tampered, corrupt, empty, concurrent refresh). |
| "Last checked" record | `<cache>/last-check` file; its modification time is the timestamp (`os.utime`); touched *before* the network call | Spec: once per 24 h, and a failed check must not retry on every query. Touching instead of rewriting avoids rename/delete. |
| Picking the copy to query | Open cache and bundled read-only, compare `max(date_found)`; cache wins ties | Spec: newest copy wins. Costs about 1 ms. |
| Untrusted DB hardening | `PRAGMA trusted_schema = OFF` on every connection, plus the existing `mode=ro` | Spec: always active. Applies to the bundled DB too. |
| Cache folder | `TLDR_FINDS_CACHE_DIR`, else `%LOCALAPPDATA%\tldr-finds` (Windows), else `$XDG_CACHE_HOME/tldr-finds`, else `~/.cache/tldr-finds` | Spec: data & integrations. The env override exists for tests and for users with read-only home folders. **Not in spec; flagged.** |
| Release URL | Constant `https://github.com/modular-v8/tldr-finds/releases/download/db-latest/`; test-only override `TLDR_FINDS_RELEASE_URL`, accepted only if it starts with `https://` or `http://127.0.0.1` | Spec says "only the fixed release URLs"; the override is needed for offline tests against a local server. **Not in spec; flagged.** |
| Publishing | `subprocess.run(["gh", ...])` with `--repo modular-v8/tldr-finds` | Prior decision. An explicit repo makes it independent of the working folder. |
| Where `--publish` builds | A fresh `tempfile.mkdtemp()` folder; a file named exactly `tldr-finds.db` (asset name = file name); best-effort cleanup that ignores errors | Spec: publishing modifies no tracked files (so it must not overwrite the bundled snapshot). Also avoids delete/rename in the repo folder, which the scheduled sandbox can't do. |
| GitHub auth for the scheduled task | The existing `gh` keyring login. If the task's sandbox can't reach the keyring, a fine-grained token (this repo only, Contents: read/write) in `GH_TOKEN` for that task | Task runs in the desktop app on this PC. To be verified in slice 1. |
| Version | 0.2.0 (adds the `update` command; nothing removed) | Constraint: SemVer, additions only. |

## Data Model

No database schema change (`finds`, `finds_fts`, `batch_notes`).

**`manifest.json`** (published, and cached as the user's last-seen manifest):

```json
{"schema": 1,
 "sha256": "64 lowercase hex chars of tldr-finds.db",
 "bytes": 180224,
 "finds": 175,
 "latest": "2026-10-14",
 "published": "2026-10-14T18:03:00Z"}
```

Validation in `find.py`: `schema == 1`, `sha256` is 64 hex chars, `0 < bytes <= 50_000_000`. Anything else means
ignore the manifest. The manifest body is read with a 64 KB cap.

**User cache folder:**

| File | Purpose |
|---|---|
| `tldr-finds.db` | Last verified download |
| `manifest.json` | Manifest that matched `tldr-finds.db` (its `sha256` is compared on the next check) |
| `last-check` | Empty file; its modification time = last check attempt |
| `tldr-finds.db.<pid>.tmp` | In-progress download; removed on failure (errors ignored) |

## File / Module Structure

```
skill/tldr-finds/scripts/find.py
    + RELEASE_URL, CHECK_EVERY = 86400, TIMEOUT = 3, MAX_BYTES = 50_000_000
    + cache_dir()                    resolve cache folder (env, platform default); None if unusable
    + db_latest(path)                read-only max(date_found) or None
    + pick_db()                      TLDR_FINDS_DB > newest of cache/bundled
    + refresh(force=False) -> str    the update step; never raises; returns a status line
    + verify_db(path, manifest)      sha256/size already checked while streaming; quick_check, tables, rows
    ~ connect()                      uses pick_db(); sets PRAGMA trusted_schema = OFF
    ~ main()                         calls refresh() before running the command (unless the command is `update`)
    + "update" command               refresh(force=True), print status, exit 0
    ~ __doc__ / help                 document `update`, TLDR_FINDS_OFFLINE, TLDR_FINDS_CACHE_DIR
build_skill.py
    ~ snapshot(out)                  takes a target path (default: bundled snapshot as today)
    + write_manifest(db, out)        computes sha256/bytes/finds/latest/published
    + publish()                      tmpdir snapshot -> manifest -> ensure release -> upload db -> upload manifest
                                     -> fetch public manifest and confirm sha256; clear exit messages for: gh missing,
                                     not logged in, upload failure, verification mismatch
    + --publish flag
tests/test_find.py
    ~ snapshot/Markdown sync tests   skipUnless the root tldr-finds.db / TLDR-Agent-Finds.md exist
    ~ set TLDR_FINDS_OFFLINE=1 in the run() helper so existing tests never touch the network
tests/test_update.py                 (new) local http.server on 127.0.0.1 serving a temp folder, with a request counter;
                                     temp TLDR_FINDS_CACHE_DIR per test. Cases: fresh update; second query within 24 h
                                     makes 0 requests; stale check (mtime moved back) re-checks; same sha256 -> no DB
                                     download; sha256 mismatch, corrupt file, empty DB, bad manifest JSON, unknown
                                     schema -> rejected and previous copy kept; server down -> exit 0 using bundled;
                                     read-only cache folder -> exit 0; TLDR_FINDS_OFFLINE and TLDR_FINDS_DB -> no
                                     requests; older cache than bundled -> bundled used; `update` prints status;
                                     a non-https, non-localhost override URL is ignored
tests/test_publish.py                (new) write_manifest() output matches the file; publish() refuses (exit
                                     non-zero, no gh call) when snapshot checks fail; gh missing -> clear error.
                                     gh is replaced with a stub, and no real upload runs in CI.
tests/bench.py                       unchanged; rerun to confirm the no-refresh query cost is unchanged
.gitignore                           + tldr-finds.db, TLDR-Agent-Finds.md
git                                  git rm --cached tldr-finds.db TLDR-Agent-Finds.md
skill/tldr-finds/SKILL.md            description and Notes: "auto-updates daily from GitHub, works offline"; `update`
                                     command; TLDR_FINDS_OFFLINE; network domain to allow in sandboxes (github.com,
                                     release-assets.githubusercontent.com)
README.md, AGENTS.snippet.md         replace "no network" claims; install note: reinstall once to get auto-update
CONTRIBUTING.md                      maintainer flow: add -> build_skill.py --publish (daily); build_skill.py + tag
                                     (versioned releases)
CHANGELOG.md                         0.2.0 entry
Scheduled task prompt (desktop app)  append: "then run `python build_skill.py --publish` in the repo folder and report
                                     its last line; if it fails, report the error"
```

## Build slices

**Slice 1: publish side.** `build_skill.py --publish` plus `tests/test_publish.py`, the untracking in `.gitignore`/git
and the scheduled-task prompt change. The maintainer then checks:
- One manual `--publish` creates `db-latest` with both files, and `git status` stays clean.
- The release page looks right and the public manifest's sha256 matches.
- One scheduled-task "Run now" publishes unattended. If not, set up `GH_TOKEN` and retry.

**Stop for review before slice 2.**

**Slice 2: client side.** `find.py` update step and `update` command, `tests/test_update.py`, the `test_find.py`
adjustments, the bench rerun, docs, CHANGELOG 0.2.0, and a refreshed bundled snapshot. The maintainer then:
- Runs the spec's acceptance criteria (fresh install in a clean folder -> `update` -> matching latest date; offline run).
- Confirms CI is green on all three operating systems.
