# Contributing

## Suggest a find

Open an issue with the **Suggest a find** template: name, link, and one or two sentences on what it is and how
it helps someone building a product. Please include where you saw it (ideally a TLDR newsletter issue).

What belongs in the list: **free** tools, libraries, repos and resources useful to people building web or AI
products. What doesn't: paid-only products, general news, funding or hardware news.

The maintainer adds entries (so categories and wording stay consistent) and publishes a new release.

## Code changes

Pull requests are welcome for `skill/tldr-finds/scripts/find.py`, `tldr.py`, `build_skill.py` and the docs.

- Keep `find.py` standard-library only and fast. Import time dominates its cost (a query takes ~1 ms), so avoid
  heavy imports (`argparse`, `pathlib`, `json`, `re` at the top level).
- Keep the skill read-only: nothing under `skill/` may write to the database.
- Run the tests before opening a PR:

  ```
  python -m unittest discover -s tests -v
  ```

## Maintainer workflow

The live database (`tldr-finds.db`) and the generated `TLDR-Agent-Finds.md` stay on the maintainer's machine and are
not in git. Users get new finds from the rolling `db-latest` GitHub pre-release, so adding finds makes no commits.

Daily (done by the scheduled task, or by hand):

```
python tldr.py add 2026-10-05 "Name" "https://url" "What it is / how it helps" [--category C]
python build_skill.py --publish     # snapshot + checks + manifest.json -> replaces both files on db-latest
```

`--publish` needs the `gh` CLI logged in (or `GH_TOKEN` set), creates the release if it is missing, and prints the
published count and date. It modifies no tracked files. If it fails, the finds are still in the local database and
the next successful publish uploads them.

Versioned release (code changes, or to refresh the snapshot new installs start from):

```
python build_skill.py               # refresh the bundled snapshot
python -m unittest discover -s tests
# update CHANGELOG.md, commit, tag vX.Y.Z, then attach dist/tldr-finds.skill to the release:
python build_skill.py --zip
```
