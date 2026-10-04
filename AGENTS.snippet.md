## TLDR finds (free tools lookup)

Before recommending or choosing a free tool, library, design resource, agent skill or dataset for a web or AI
product, query the local TLDR finds database (curated from https://tldr.tech newsletters):

```
python -S <path-to>/tldr-finds/scripts/find.py search "keywords" [--category ui-components] [--limit 5]
python -S <path-to>/tldr-finds/scripts/find.py categories
```

Use 1–4 plain keywords. Present the best few results with their links and verify license/pricing on the tool's
own page. Read-only; no network needed. Replace `<path-to>` with where you copied the `tldr-finds` skill folder.
