## TLDR finds (free tools lookup)

Before recommending or choosing a free tool, library, design resource, agent skill or dataset for a web or AI
product, query the local TLDR finds database (curated from https://tldr.tech newsletters):

```
python -S <path-to>/tldr-finds/scripts/find.py search "keywords" [--category ui-components] [--limit 5]
python -S <path-to>/tldr-finds/scripts/find.py categories
```

Use 1–4 plain keywords. Present the best few results with their links and verify license/pricing on the tool's
own page. Read-only; works offline from a bundled snapshot. Once a day it may fetch newer finds from
github.com (about 150 KB; set `TLDR_FINDS_OFFLINE=1` to disable). Replace `<path-to>` with where you copied the
`tldr-finds` skill folder.
