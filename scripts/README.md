# Album metadata pipeline

Builds `library.json`, the metadata file that
[khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay)
serves to Subsonic clients: album title, year, platform, album type, publisher,
developer, date added, track count, duration, cover and other page metadata.

## Listing sweeps plus full album-page coverage

There are roughly 104,600 albums. Listing and facet pages provide basic fields
cheaply; a resumable full backfill visits every individual album page for the
remaining fields, whether or not its publisher is already known.

| source | requests | what it yields |
| --- | --- | --- |
| `/game-soundtracks?page=N` | about 210 | title, platform, type, year for every listed album (500 rows/page) |
| `/game-soundtracks/publisher/<key>` | about 1,700 | publisher assignments from the company listings |
| `/game-soundtracks/developer/<key>` | about 1,000 | developer assignments from the company listings |
| `/game-soundtracks/album/<slug>` | 1 per album, plus retries | date added, track count, duration, cover, catalogue number, formats and the other page fields |

The bulk passes remain useful for fresh catalog discovery. They are not a
substitute for individual-page coverage: a known publisher does not mean the
album's detailed metadata has been fetched. Small companies may not appear
on the facet index at all.

## Scripts

| script | role |
| --- | --- |
| `album_list.py` | shared fetcher and parser for listing pages, with retries and resumable state |
| `crawl_index_pages.py` | flat listing sweep -> `album-list.ndjson` |
| `crawl_facets.py` | publisher/developer facets -> facet rows and per-company statistics |
| `crawl_album_meta.py` | resumable individual-page crawler; the full workflow uses `--index library.json` |
| `metadata_progress.py` | counts fetched, permanently unavailable and pending pages across the whole current library |
| `residual_slugs.py` | optional manual field-specific selection; no longer used to restrict the full backfill |
| `build_library.py` | merges all sources into `library.json` with a coverage manifest |
| `release_notes.py` | converts the manifest into release notes / a job summary |

### Known, empty, unknown

- **known**: a field has a value.
- **empty**: the field is `null` or `[]`; a source looked and found nothing.
- **unknown**: the key is absent; no relevant source has looked yet.

A successful page visit can legitimately leave fields empty. Full page
coverage does not promise a non-empty value for every field on every album.
The page wins for year/platform/type; a positive company facet hit can fill
an empty publisher/developer result from the page.

## Workflows

`album-meta.yaml` runs the daily listing and facet sweeps. It merges the
collected page records and publishes a dated library release after coverage
gates pass. The newest seven library releases are retained.

`album-meta-residual.yaml` now runs **Album metadata full backfill**:

- Targets **every album in the current published library**, regardless of
  publisher/year/other existing fields. Previously fetched pages are skipped.
- Scheduled every **4 hours**, with up to **240 minutes** of crawling per run.
  Manual dispatch accepts a `minutes` budget from 1 to 240.
- Keeps the existing conservative pacing: 3 workers, 0.9 seconds delay plus
  up to 0.6 seconds jitter per worker, and retries with backoff.
- Saves `album-meta.ndjson` and the failure log to `crawl-data` every
  **30 minutes** and again at the end, including after a crawl error.
- Resumes automatically on the next scheduled run. A slice with no progress
  stops early rather than spending the rest of the run on repeated failures.
- Treats 404/no-album-content results as unavailable, not fetched. Transient
  failures stay pending for later retries.
- Publishes an updated `library.json` after batches add unpublished records,
  using the same listing/publisher coverage gates as the daily sweep.
- Writes `metadata-progress.json` to `crawl-data`, separating full-page
  coverage from the fraction of albums with any basic metadata.

Both workflows share the `khinsider-metadata` concurrency group with
`cancel-in-progress: false`: only one runs at a time, so checkpoint replacement
and library publication cannot race. Scheduled runs can wait in GitHub's
queue; a cron expression is not an exact start-time guarantee.

Library publication is draft -> upload both assets -> publish as latest, so
clients never see half-uploaded assets. Intermediate state stays in releases,
not in git. The full backfill fails closed if it cannot restore the existing
checkpoint or catalog, rather than overwriting collected records with an
empty restart.

## Running it by hand

```sh
pip install -r scripts/requirements-meta.txt
python -m unittest discover -s scripts -p 'test_metadata_progress.py' -v

# After restoring library.json and the album-meta checkpoint:
python scripts/metadata_progress.py
python scripts/crawl_album_meta.py --index library.json --order hash \
  --deadline-minutes 30 --workers 3 --delay 0.9 --jitter 0.6 --retries 4
python scripts/metadata_progress.py --summary metadata-progress.json
```

Every crawler is resumable. Re-running skips completed work; `--refresh`
forces per-page re-fetches, and `--deadline-minutes` stops cleanly before a
runner timeout. Restore the persisted checkpoint before starting a new runner.
