# khinsider-index
A static file containing a complete index of the content on downloads.khinsider.com

This repo runs the same index functionality built into my [khinsider](https://github.com/marcus-crane/khinsider/tree/v2) tool, just on a timer and uploaded as a file.

While any user can generate their own index if they choose, by default they'll download a tar gzipped version of the index that is generated and released against this repo, whenever any changes are detected.

Shout outs to [trackiam](https://github.com/glassechidna/trackiam) as inspiration. I've taken the idea a little further with automatic releasing of the files being watched though so feel free to poke around this repo too. I ran into a few gotchas that I've briefly documented.

I intend to write a fuller README in future

## Live index rebuild (2026-09, this fork)

The original indexer (`khinsider --debug index`) no longer works:
downloads.khinsider.com is now behind Cloudflare bot protection which
hard-blocks datacenter clients and headless browsers.

`scripts/crawl_live.py` rebuilds the index from the **live site** using
[curl_cffi](https://github.com/lexiforest/curl_cffi) with a Chrome TLS (JA3)
fingerprint, which passes the protection without a browser:

    pip install curl_cffi
    python3 scripts/crawl_live.py

Result of the 2026-09-01 rebuild: **104,431 albums** (vs 49,536 in
v0.0.2888 from 2024-01-30; +56,052 added / -1,157 removed). See the
Releases page for `index.tar.gz` and the diff lists.

## Album metadata crawl (2026-09, this fork)

`index.json` / `letters/*.json` only map **title -> album path**. They carry no
release year, publisher, platform or album type, so anything consuming the index
(e.g. [khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay))
had nothing to show beyond album titles.

`scripts/crawl_album_meta.py` visits every album page once and records the info
block at the top of the page as NDJSON:

```sh
pip install -r scripts/requirements.txt
python3 scripts/crawl_album_meta.py --limit 200      # smoke test
python3 scripts/crawl_album_meta.py --letters A,B,C  # one browse section
python3 scripts/crawl_album_meta.py --shard 1/8      # 8 jobs in parallel
python3 scripts/crawl_album_meta.py                  # everything, resumable
```

One line per album in `album-meta.ndjson`:

```json
{"slug":"nintendo-3ds-background-music","title":"3DS Background Music","letter":"0-9",
 "year":2011,"publishers":["Nintendo"],"developers":[],"platforms":["3DS"],
 "album_type":"Gamerip","catalog_number":null,"date_added":"2026-04-07",
 "uploaders":["milesthecreator"],"total_filesize":"298 MB (MP3), 757 MB (FLAC)",
 "track_count":106,"duration":9786,"formats":["mp3","flac"],"cover":"https://..."}
```

Then merge it with `index.json` into the `library.json` the relay consumes:

```sh
python3 scripts/build_library.py --gzip
```

```json
{"library_version":"2026-09-01","index_version":"live-2026-09-01",
 "album_count":104453,"metadata_count":104100,
 "albums":[{"slug":"...","title":"...","letter":"0-9","year":2011,
            "publishers":["Nintendo"],"platforms":["3DS"],"album_type":"Gamerip",
            "date_added":"2026-04-07","track_count":106,"duration":9786}]}
```

How the relay maps these onto Subsonic tags:

| khinsider | Subsonic |
|---|---|
| `Year` | `year`, `releaseDate`, `originalReleaseDate` |
| `Published by` (fallback `Developed by`) | `artist`, `albumArtist`, `artistId` |
| `Platforms` | `genre`, `genres[]` |
| `Album type` | `genre`, `genres[]` |
| `Date Added` | `created` |

Notes:

- The parser lives in `scripts/album_meta.py` and is shared with the relay's
  `server.py`; keep the two in sync when the page layout changes.
- Pacing defaults to 4 workers with a ~0.4-1.0s delay each, i.e. roughly
  4-6 albums/s in practice but ~7-9 hours for all 104k albums. Cloudflare
  challenges, 429s and timeouts are retried with exponential backoff; whatever
  still fails is appended to `album-meta-failures.log` and can be retried with
  `--retry-failures`.
- The crawl is resumable: slugs already present in the NDJSON are skipped, so
  interrupting and rerunning (or sharding across jobs and concatenating the
  shards) is safe.
- `scripts/scrape.py` (the old per-album scraper) is superseded by this: it uses
  plain `requests`, which Cloudflare blocks, and it has a syntax error in its
  track URL f-string. `albums/*.json` from it is left in place for reference.
- `.github/workflows/album-meta.yaml` runs the crawl as a sharded manual job and
  uploads `library.json`/`library.json.gz` as artifacts.
