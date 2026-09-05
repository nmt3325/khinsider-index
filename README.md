# khinsider-index

Live album and song metadata for
[khinsider-subsonic-relay](https://github.com/nmt3325/khinsider-subsonic-relay).

## Current pipeline: standalone live data

The new workflows can start without any historical index or title cache:

1. Fetch every page of KHInsider's current album listing and certify the result.
2. Fetch and validate the complete track list for every catalogue album, resuming
   this generation's own checkpoint across bounded runs.
3. Generate `library.json`, `library.json.gz`, `songs.tsv.gz` and `songs-index.json`
   from that data only. A missing/invalid album track list blocks publication;
   confirmed HTTP 404s are reported separately.

No `index.json`, 2023 album cache, cached song titles, old crawl snapshot or
previously published library is used to fill missing data. Full acquisition is
required before the first new serving release. Existing serving releases remain
available during collection, rather than being replaced with a partial dataset.

See **[the pipeline guide](scripts/README.md)** for schedules, commands, checkpoint
recovery and completeness limits, and **[the data contract](docs/crawl-contract.md)**
for producer/relay compatibility.

### Workflows

- `album-meta.yaml`: daily full listing refresh and recent-update discovery.
- `album-meta-residual.yaml`: bounded full metadata acquisition/resume, every
  four hours.
- `song-index.yaml`: complete-only rebuild of the same serving dataset.
- `live-data.yaml`: shared implementation and serialization for those entry points.
- `wayback-archive.yaml`: separate archival work; it is not a serving-data input.
- `tests.yaml`: offline regression and workflow validation.

The old `crawl.yaml`, `index.yaml` and `release.yaml` workflows were retired.
Generated data is kept in releases, not committed back into the source tree.

### Outputs and status

Default distribution URLs remain compatible with the relay:

- `releases/latest/download/library.json`
- `releases/download/song-index/songs.tsv.gz`
- `releases/download/song-index/songs-index.json`

New-generation artifacts identify `khinsider-live-v2`, `complete: true` and
`legacy_inputs: []`. The resumable `live-crawl-v2` checkpoint release is **not**
a serving release. Check the workflow summary's pending count and publication
status; a successful time-boxed run need not mean a complete dataset was published.
A complete catalogue means the validated listing scope, not an instantaneous
snapshot of all changes on the website.

## Historical material

Root `index.json`, `letters/`, `albums/` and older scripts/releases are retained
for historical reference and are not read by the new serving workflow. Old
manual scraper/export commands are not the current pipeline.

This fork originated from [marcus-crane/khinsider-index](https://github.com/marcus-crane/khinsider-index)
and the [khinsider indexer](https://github.com/marcus-crane/khinsider/tree/v2).
Credit also to [trackiam](https://github.com/glassechidna/trackiam), an inspiration
for the original static indexing workflow.
