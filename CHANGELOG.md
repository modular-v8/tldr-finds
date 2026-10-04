# Changelog

Versions follow [SemVer](https://semver.org): the skill interface (commands and flags of `find.py`) is the
public API; adding finds to the database is a patch/minor release.

## 0.1.0 — 2026-10-04

- First release: `tldr-finds` skill with `search`, `recent` and `categories` commands, read-only SQLite
  (FTS5) snapshot of 142 finds (latest 2026-10-01), TLDR attribution in every result.
- `AGENTS.snippet.md` for agents without skills support.
- Smoke tests (`tests/`), benchmark (`tests/bench.py`) and a GitHub Actions workflow.
- Maintainer tooling: `tldr.py` (add / search / export), `build_skill.py` (snapshot + `.skill` zip).
