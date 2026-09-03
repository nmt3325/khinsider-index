# Album metadata pipeline

Builds `library.json`, the metadata file that
[khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay)
serves to Subsonic clients: album title, year, platform, album type, publisher
and developer for the whole archive.

## Why it is not one request per album

There are ~104,500 albums. Crawling one page each, politely, takes over a day,
which does not fit in a GitHub Actions job and is rude to a site that gives
this away for free.

Almost all of it is available in bulk instead:

| source | requests | what it yields |
| --- | --- | --- |
| `/game-soundtracks?page=N` | 210 | title, platform, type, year for **every** album (500 rows/page) |
| `/game-soundtracks/publisher/<key>` | ~1,700 | publisher for **79,305** albums (1,625 companies) |
| `/game-soundtracks/developer/<key>` | ~1,000 | developer for **34,172** albums (961 companies) |
| `/game-soundtracks/album/<slug>` | 1 per album | everything else: date added, track count, duration, cover, catalogue number, formats |

So ~2,900 requests - under an hour at one request per second - cover the fields
that matter. Only the remainder needs per-album pages.

khinsider only lists companies with 11+ albums on the facet index pages, so the
long tail of small publishers is part of that remainder.

## Scripts

| script | role |
| --- | --- |
| `album_list.py` | shared fetcher and parser for every list-shaped page (`table.albumList`), Cloudflare-aware retries, resumable state files |
| `crawl_index_pages.py` | sweeps `/game-soundtracks?page=N` -> `album-list.ndjson` |
| `crawl_facets.py` | sweeps publisher or developer facets -> `facet-<kind>.ndjson` plus a per-company stats file |
| `crawl_album_meta.py` | one request per album; used only for the residual queue |
| `residual_slugs.py` | picks the albums still missing a field, newest first then a stable hash order |
| `build_library.py` | merges all four sources into `library.json` with a coverage manifest |
| `release_notes.py` | turns that manifest into release notes / a job summary |

### Known, empty, unknown

The merge keeps these three states apart, because a resumable crawl needs to
tell "we looked and there is nothing" from "we never looked":

- **known** - the field is present with a value
- **empty** - the field is present as `null` or `[]`: a source looked and the
  album genuinely has no value
- **unknown** - the key is *absent*: nothing has looked yet, so
  `residual_slugs.py` will queue it

When two sources disagree, the album page wins for `year`, `platforms` and
`album_type`; for `publishers` and `developers` a facet hit beats an empty
album page, since the facet listing is itself positive evidence.

## Workflows

`album-meta.yaml` (daily) runs the three sweeps in one job, folds in whatever
album pages have been collected so far, builds `library.json`, and publishes it
as a dated release. It creates the release as a draft, uploads the assets, then
un-drafts it, so `releases/latest/download/library.json` never points at a
half-written file. Coverage gates (`--min-list-coverage`, `--min-publisher-coverage`)
fail the build rather than publish a library gutted by a site layout change.
The seven newest library releases are kept.

`album-meta-residual.yaml` (daily) nibbles at the residual queue: ~45 minutes,
~3 requests per second, ~7k albums per run, then stops and saves state. It
publishes nothing; its output goes back into the `crawl-data` prerelease and
the next sweep picks it up.

Intermediate state lives in release assets rather than in git, so the
repository does not grow by a daily NDJSON snapshot and the two workflows never
contend for the same commit.

## Running it by hand

```sh
pip install -r scripts/requirements.txt

python scripts/crawl_index_pages.py --max-pages 3      # resumable; rerun to continue
python scripts/crawl_facets.py --kind publisher --limit 20
python scripts/crawl_facets.py --kind developer --limit 20
python scripts/build_library.py --pretty --out /tmp/library.json
```

Every crawler is resumable: interrupt it and rerun the same command, and it
skips what it already has. `--fresh` forces a full re-sweep, `--deadline-minutes`
stops cleanly before a CI timeout.
