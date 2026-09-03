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
  Resumable via `work/crawl_done.txt`. Time-boxed via `CRAWL_MAX_SECONDS`.
- `wayback_submit.py` — anonymous SPN submitter (legacy; blocked from datacenter IPs).
- `spn2_submit.py` — authenticated SPN2 submitter. Requires `ARCHIVE_ORG_S3_ACCESS` /
  `ARCHIVE_ORG_S3_SECRET` (https://archive.org/account/s3.php). Resumable via
  `work/wayback_done.txt`; retries 429/5xx with backoff; time-boxed via
  `SPN2_MAX_SECONDS`; queue selectable via `QUEUE_GLOB` (default `work/wayback_queue*.txt`).

## Status at creation
- 104,431 albums in the live index (2026-09-01 rebuild)
- 1,310,089 unique track URLs extracted from the 47,288 cached albums (1,310,140 songs)
- 58,111 albums still need their album page crawled for track URLs
- UPDATE 2026-09-01: SPN2 (authenticated) VERIFIED WORKING from Google Colab:
  album page and track page captures confirmed in CDX (status 200). GitHub-hosted
  runner IPs are edge-blocked even WITH auth; anonymous SPN is also rate-limited on
  Colab. Use `spn2_submit.py` with ARCHIVE_ORG_S3_ACCESS / ARCHIVE_ORG_S3_SECRET from
  https://archive.org/account/s3.php on Colab or a residential IP.
- Track URLs now serve HTML pages (status 200) embedding the current direct MP3
  link (host migrated: vgmsite.com -> jetta.vgmtreasurechest.com), so track-page
  captures preserve the pointer to each file. Direct MP3 capture via Wayback is
  currently unreliable (archive.org fetcher 523s on vgmsite URLs).

## Release assets (tag `song-urls-2026-09-01`)
- `wayback_queue_khinsider-track-urls.txt.gz` — canonical track-page URLs (SPN queue)
- `wayback_queue_vgmsite-direct-urls.txt.gz` — direct file URLs (from the 2023 cache)
- `songs_cached.jsonl.gz` — full per-track metadata for the cached albums
- `crawl_state_snapshot.tar.gz` — crawler + submitter resume state (updated every run)

## GitHub Actions (2026-09-03)
`.github/workflows/wayback-archive.yaml` runs the whole pipeline on GitHub-hosted
runners: restores state from the `song-urls-2026-09-01` release, crawls album pages
for song URLs (time-boxed), submits song URLs to the Wayback Machine via SPN2
(time-boxed), then uploads the updated state back to the release. Runs every 6h
and on demand (workflow_dispatch with crawl_minutes/submit_minutes inputs).
SPN2 verified working from GitHub runner IPs on 2026-09-03 (the 2026-09-01 block
was transient). Requires repo secrets `ARCHIVE_ORG_S3_ACCESS` / `ARCHIVE_ORG_S3_SECRET`.

Notes:
- The 2023-cached queue (~1.31M URLs) is partially stale: ~25% 404 on the live site
  in a 12-URL sample (re-ripped/expanded albums renamed tracks). The workflow builds
  `work/queue_submit.txt` with freshly crawled URLs FIRST and cached URLs second.
- SPN2 acceptance (HTTP 200 + job_id) is what the submitter tracks; capture jobs that
  end as not-found for stale URLs are harmless and visible only via the SPN2 status
  endpoint / CDX after the fact.
