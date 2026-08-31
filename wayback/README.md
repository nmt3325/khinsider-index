# Wayback archival pipeline (2026-09-01)

Extracts the URL of every individual song on downloads.khinsider.com and
registers each one with the Wayback Machine (Save Page Now).

## Scripts
- `extract_song_urls.py` — reads the cached per-album JSONs in `albums/` (2023 crawl)
  and emits `work/songs_cached.jsonl` (per-track records incl. direct vgmsite file
  URLs), `work/wayback_queue.txt` (unique track URLs), and `work/missing_slugs.txt`
  (albums in `index.json` that have no cached track data).
- `crawl_songs.py` — fetches each missing album page with curl_cffi (Chrome TLS
  fingerprint; the site Cloudflare-blocks plain datacenter clients), extracts the
  per-track URLs, and appends them to `work/wayback_queue_crawled.txt`.
  Resumable via `work/crawl_done.txt`.
- `wayback_submit.py` — submits every queued URL to
  `https://web.archive.org/save/<url>`. Resumable via `work/wayback_done.txt`.
  Retries 429/5xx with backoff; after 10 consecutive failures (e.g. blocked IP)
  it sleeps 30 min and rescans. Failed URLs are re-attempted on later passes.

## Status at creation
- 104,431 albums in the live index (2026-09-01 rebuild)
- 1,310,089 unique track URLs extracted from the 47,288 cached albums (1,310,140 songs)
- 58,111 albums still need their album page crawled for track URLs
- WARNING: anonymous Save Page Now appears blocked from datacenter IPs
  (GitHub-hosted runners): even the example.com control save failed (HTTP 523 /
  connection timeout). For real throughput use archive.org S3 credentials
  (SPN2 API: POST https://web.archive.org/save with `Authorization: LOW key:secret`)
  or run `wayback_submit.py` from a residential connection.

## Release assets (tag `song-urls-2026-09-01`)
- `wayback_queue_khinsider-track-urls.txt.gz` — canonical track-page URLs (SPN queue)
- `wayback_queue_vgmsite-direct-urls.txt.gz` — direct file URLs (from the 2023 cache)
- `songs_cached.jsonl.gz` — full per-track metadata for the cached albums
- `crawl_state_snapshot.tar.gz` — crawler resume state (missing slugs + done list)
