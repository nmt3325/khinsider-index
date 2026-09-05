# Wayback archival pipeline

Archival submission is separate from the relay's serving-data pipeline. Its
historical queue and submission ledger are **not** inputs to `build_library.py`
or `build_song_index.py`.

## Current workflow

`wayback-archive.yaml` restores existing archival history separately from the
new `live-crawl-v2` checkpoint. Direct-link discovery runs only when the modern
catalogue and all its album track records are complete. While the initial full
collection is pending, existing queued archival work can still be submitted.

- `direct_links.py` targets the certified modern catalogue, not root `index.json`.
  It streams the selected records from `METADATA_FILE` (default
  `work/live-v2/album-meta.ndjson`) and uses `CATALOGUE_FILE` (default
  `work/live-v2/catalogue.json`).
- Valid observed MP3 URLs are reused by song ID. FLAC or unsupported/missing
  player URLs use the real song page. No cross-track hash or extension guessing.
  Discovery itself does not download audio bodies.
- Per-album direct-link completion records and `work/direct_queue.txt` remain
  resumable. Old guessed-hash records are not accepted as successful v2 direct
  URL resolution.
- `spn2_submit.py` submits queued URLs using configured Internet Archive
  credentials and maintains the existing submission ledger. It retries 429/5xx
  and respects the supplied time budget.
- `direct_minutes` accepts 0–60 and `submit_minutes` accepts 0–260. Zero skips
  that phase. The old `crawl_minutes` phase has been removed.

Existing archival state releases are retained to avoid resubmitting work.
They do not enrich or fill holes in the new relay dataset. This migration does
not purge archival queues or require archive submission to finish before the
relay dataset can be published.

## Retained historical utilities

`extract_song_urls.py`, `crawl_songs.py` and the anonymous `wayback_submit.py`
remain for historical/manual use. The current workflow no longer runs the old
album crawl or imports its cached titles into a serving index. See
[the current pipeline guide](../scripts/README.md) for complete dataset generation.
