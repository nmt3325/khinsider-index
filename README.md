# khinsider-index

Incrementally collected album and song metadata for
[khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay).

## Incremental crawling, full accumulated releases

1. Restore this pipeline's own verified `live-crawl-v2` checkpoint. Previously
   collected modern data is retained, not discarded on a daily run.
2. Discover homepage additions/updates with a three-day overlap. Crawl only
   new, changed, and due unresolved albums. Full listing enumeration is needed
   only at a genuinely empty bootstrap, not on a daily refresh.
3. Publish **all accumulated valid albums and tracks** in `library.json`,
   `library.json.gz`, `songs.tsv.gz`, and `songs-index.json`. Releases are full
   snapshots, never just the latest crawl's delta.

Missing new albums stay queued. Failed refreshes retain the preceding complete
track list. Failed requests back off between runs; new update events take priority.
Confirmed redirects are canonicalized without duplicate albums. True HTTP 404s
are reported separately; a homepage redirect is not fabricated into a 404.

No retired workflow index, cached song titles, archival snapshot, or published
library supplies missing records. The current generation's own metadata and
recent-discovery history are deliberately accumulated. At the first serving
cutover, only the previous song manifest's **row count** is checked to prevent
replacing a large serving index with a small bootstrap; its rows are never imported.

See [the pipeline guide](scripts/README.md) and [the data contract](docs/crawl-contract.md).

### Workflows

- `album-meta.yaml`: daily incremental discovery and collection (20-minute budget).
- `album-meta-residual.yaml`: incremental updates and due backfill every four hours.
- `song-index.yaml`: rebuild the full accumulated snapshot without website crawling.
- `live-data.yaml`: the shared engine and single serving-writer concurrency lock.
- `wayback-archive.yaml`: separate archival work, never a serving-data input.
- `tests.yaml`: offline regression and workflow validation.

`crawl.yaml`, `index.yaml`, and `release.yaml` remain retired. Generated serving
data lives in releases, not source-tree commits.

### Outputs and status

The relay's default URLs are unchanged:

- `releases/latest/download/library.json`
- `releases/download/song-index/songs.tsv.gz`
- `releases/download/song-index/songs-index.json`

Serving artifacts declare `data_source: khinsider-live-v2`, `complete: true`,
`completeness_scope: cumulative_snapshot`, and `legacy_inputs: []`. **Complete
snapshot is not a claim that every website album or update has been collected.**
Pending requests and retained refresh data are reported separately.

The job summary separates metadata queue completion (`crawl_complete`), recent
scan completion (`discovery_complete`), snapshot readiness, and actual
`published` / `last_release` results. An unchanged full snapshot need not create a
new release. `live-crawl-v2` is a recovery prerelease, not the serving library.

## Historical material

Root `index.json`, `letters/`, `albums/` and older scripts/releases are retained
for historical reference and are not read by the new serving workflow. Old
manual scraper/export commands are not the current pipeline.

This fork originated from [marcus-crane/khinsider-index](https://github.com/marcus-crane/khinsider-index)
and the [khinsider indexer](https://github.com/marcus-crane/khinsider/tree/v2).
Credit also to [trackiam](https://github.com/glassechidna/trackiam), an inspiration
for the original static indexing workflow.
