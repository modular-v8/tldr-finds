# Changelog

Versions follow [SemVer](https://semver.org): the skill interface (commands and flags of `find.py`) is the
public API; adding finds to the database is a patch/minor release.

## 0.2.0 — 2026-10-14

- Auto-update: at most once a day `find.py` fetches `manifest.json` from the `db-latest` GitHub release and, if the
  finds changed, downloads, verifies (sha256, size, integrity check) and caches the new database in a per-user
  folder. Queries use the newest of the cache and the bundled snapshot and never depend on the network; every
  failure falls back silently to the best local copy.
- New `find.py update` command to check immediately. `TLDR_FINDS_OFFLINE=1` disables network checks;
  `TLDR_FINDS_CACHE_DIR` moves the cache. Databases are opened read-only with `trusted_schema` off.
- Maintainer: `build_skill.py --publish` uploads the database and manifest to `db-latest` with no commits. The root
  `tldr-finds.db` and `TLDR-Agent-Finds.md` are no longer tracked in git.
- Bundled snapshot refreshed to 175 finds (latest 2026-10-14). Tests cover the update path with a local server.
- Existing commands, flags and output are unchanged. Reinstall once to get the auto-update.

## 0.1.0 — 2026-10-04

- First release: `tldr-finds` skill with `search`, `recent` and `categories` commands, read-only SQLite
  (FTS5) snapshot of 142 finds (latest 2026-10-01), TLDR attribution in every result.
- `AGENTS.snippet.md` for agents without skills support.
- Smoke tests (`tests/`), benchmark (`tests/bench.py`) and a GitHub Actions workflow.
- Maintainer tooling: `tldr.py` (add / search / export), `build_skill.py` (snapshot + `.skill` zip).
