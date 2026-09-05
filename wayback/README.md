# Wayback archival pipeline (2026-09-05)

Extracts the URL of every individual song on downloads.khinsider.com and
registers each one with the Wayback Machine (Save Page Now).

## Scripts
- `extract_song_urls.py` — reads the cached per-album JSONs in `albums/` (2023 crawl)
  and emits `work/songs_cached.jsonl` (per-track records incl. direct vgmsite file
  URLs), `work/wayback_queue.txt` (unique track URLs), and `work/missing_slugs.txt`
  (albums in `index.json` that have no cached track data).
- `crawl_songs.py` — emits canonical song-page URLs for albums missing from the 2023
  cache. When `METADATA_FILE` (default `album-meta.ndjson`) contains a latest complete
  canonical track list for the album, it reuses that metadata without refetching the
  album page. Otherwise it fetches the album page and parses `table#songlist`.
  Failed albums are not marked done.
- `direct_links.py` — emits actual direct audio URLs for Wayback submission. It reuses
  canonical metadata from `METADATA_FILE` when available: observed MP3 URLs come from
  the shared static player decoder, and tracks that still need FLAC or unsupported
  player resolution fall back to the real song page. Old guessed-hash outputs are not
  trusted as resume state; completion is tracked only by v2 records in
  `work/direct_links.jsonl`.
- `wayback_submit.py` — anonymous SPN submitter (legacy; blocked from datacenter IPs).
- `spn2_submit.py` — authenticated SPN2 submitter. Requires `ARCHIVE_ORG_S3_ACCESS` /
  `ARCHIVE_ORG_S3_SECRET` (https://archive.org/account/s3.php). Resumable via
  `work/wayback_done.txt`; retries 429/5xx with backoff; time-boxed via
  `SPN2_MAX_SECONDS`; queue selectable via `QUEUE_GLOB` (default `work/wayback_queue*.txt`).

## Notes
- `crawl_songs.py` still writes one JSON object per song to `work/songs_crawled.jsonl`
  so existing snapshot consumers remain compatible.
- `direct_links.py` still appends one queue entry per resolved URL to
  `work/direct_queue.txt`, but the per-album summary file now stores schema-tagged
  completion records instead of guessed hash state.
