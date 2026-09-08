# Incremental crawler, full accumulated index

The modern pipeline keeps its own collected data and adds daily deltas. It
never imports retired workflow indexes, cached song titles, or archival data.

```text
One-time certified seed + modern metadata + recent-discovery history
                                 |
                new / changed / due unresolved albums
                                 |
             retain validated complete album observations
                                 |
         FULL library.json(.gz) + songs.tsv.gz + songs-index.json
```

## Collection and snapshot completeness

- A certified `catalogue.json` is the historical seed. Full listing enumeration
  occurs only at an empty bootstrap, not on each daily run.
- Modern metadata and recent discoveries extend the accumulated registry. New
  homepage slugs are eligible immediately, without another listing sweep.
- Accepted album records contain complete track lists. Failed refreshes retain
  last-good records; new failures stay pending. No songs or titles are invented.
- Every release is the whole successful accumulation, never only that run's delta.
  `complete: true` is scoped by `completeness_scope: cumulative_snapshot`.
- `crawl_complete` describes the known metadata queue; job `discovery_complete`
  separately describes the homepage scan window. Neither a successful job nor a
  complete accumulated snapshot promises an instantaneous full website crawl.
- True HTTP 404s and confirmed alias merges are explicit. A homepage redirect
  remains unresolved, rather than silently deleting or fabricating album data.
- Optional publisher/developer/date/format metadata may legitimately be empty.

## One engine, three entry points

All serving jobs call `live-data.yaml` / `live_pipeline.py` and share one
`khinsider-metadata` concurrency lock with cancellation disabled.

| Entry point | Work | Default schedule / budget |
| --- | --- | --- |
| `album-meta.yaml` | Incremental discovery and due album requests | Daily 03:20 UTC / 20 minutes |
| `album-meta-residual.yaml` | Incremental updates and unfinished backfill | Every four hours at :40 UTC / 240 minutes |
| `song-index.yaml` | Full accumulated rebuild, no website crawl | Monday 05:17 UTC |

Manual inputs are `minutes` (greater than zero, at most 240) and `publish`; the
rebuild has only `publish`. Budgets limit collection, not mandate its duration.
`refresh` remains a CLI alias for incremental work, not daily full enumeration.
GitHub may queue/delay scheduled runs.

Homepage scans use a three-day overlap, at most ten pages per slice, and a
resumable cursor. Acknowledgment requires a newer validated album observation.
Metadata uses three workers, 0.9-second delay plus up to 0.6-second jitter,
bounded HTTP retries, and slices of at most 25 minutes. New changes take priority.
Persistent failures back off exponentially from one hour to 24 hours; a newer
event bypasses that delay. Unstarted requests are not failures. No-progress
slices stop instead of repeatedly hitting the same failed pages.

## Checkpoint and publication safeguards

Only `checkpoint.tar.gz` in the `live-crawl-v2` prerelease is restored. It contains
the modern seed, metadata, discovery journals/cursors, failures, retry state,
and publication inventory. Source/schema markers, per-file hashes/sizes, safe
archive members, and staged restore remain mandatory. Only a truly absent release
permits bootstrap; missing assets, corrupt data, and transport/auth errors fail
closed. Nonempty restore destinations are rejected. Failed restore cannot
authorize checkpoint overwrite. Actions keeps a secondary recovery artifact.

Do not reset the modern accumulation or upload a synthetic/audit test state.
Retired `index.json`, `crawl-data`, `song-urls-*`, `songs_cached.jsonl.gz`,
`crawl_state_snapshot.tar.gz`, and published library rows never supply missing data.

At the initial serving cutover, the previous song manifest's row count guards
against replacing a full index with a small bootstrap; its rows are not imported.
Subsequent publication checks the saved modern album inventory, allowing only
explicit gone records or confirmed canonical merges to remove previous identities.

The shared engine enables `--cumulative` on both builders. Standalone builders
without it retain strict certified-catalogue checks. Invalid album records,
empty snapshots, changed build inputs, or mismatched outputs still block writes.
Every actual track is preserved even when titles/numbers coincide. Disc 0 is
serialized as 0, and observed song-page filenames are not guessed. Gzip output
has a deterministic timestamp and filename header.

All four assets are validated and uploaded to a draft library release. The
stable `song-index` payload is updated before its manifest; then the library is
promoted to latest. GitHub has no multi-release transaction: readers retain their
last-good data during inconsistencies. Existing releases are not deleted.
Unchanged serving contents skip publication; remembered metadata/registry inputs
also skip repeated full rebuilds. Historical release status describes its own
snapshot; Actions reports the latest queue status.

Check `fetched`, `tracks`, `pending`, `retained_last_good`, `snapshot_complete`,
`discovery_complete`, `ready_for_publish`, `published`, and `last_release` in the
job summary. A green job is not itself evidence of a new publication.

## Local use

```sh
python -m pip install -r scripts/requirements-meta.txt
python scripts/live_pipeline.py run --state-dir work/live-v2 --mode incremental --minutes 20
python scripts/live_pipeline.py status --state-dir work/live-v2
```

On a fresh runner, restore first. The workflows automate these steps.
Publication is off unless explicitly enabled:

```sh
python scripts/live_pipeline.py restore --repo nmt3325/khinsider-index
python scripts/live_pipeline.py run --repo nmt3325/khinsider-index --mode incremental --minutes 20 --checkpoint --publish
python scripts/live_pipeline.py save --repo nmt3325/khinsider-index
```

Do not restore over nonempty state or manually combine checkpoints. Preserve a
bad checkpoint for investigation instead of silently starting a reduced index.
After upgrades, dispatch current `main` rather than rerunning an old revision.

## Offline verification

```sh
python -m pip install -r scripts/requirements-meta.txt pytest ruff pyyaml
PYTHONPATH=scripts:wayback python -m pytest -q scripts wayback test_shared_player.py
ruff check --select E9,F63,F7,F82 scripts wayback test_shared_player.py
```

Coverage includes bootstrap, incremental discovery, outside-seed accumulation,
last-good retention, redirects, zero discs, observed extensionless filenames,
retry state, full snapshot contents, checkpoint integrity, publication failures,
and relay compatibility. Unit fixtures are never production inputs.
See [the shared contract](../docs/crawl-contract.md).
