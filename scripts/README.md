# Standalone live-data pipeline

The serving dataset is generated exclusively from the current-generation live
crawler. A checkout without `index.json`, `albums/`, old title caches or old
release snapshots can bootstrap it from the website.

```text
Full paginated album listing -> certified catalogue.json
                                  |
Homepage updates -> pending events + full album-page/track crawl
                                  |
                 strict completeness and provenance checks
                                  |
          library.json(.gz) + songs.tsv.gz + songs-index.json
```

## What “complete” means

- Every page in the full `/game-soundtracks` listing was fetched. The catalogue
  records the page set, advertised album count, observations and a content hash.
  Unique albums must meet the advertised count; capped/incoherent sweeps do not
  replace the last-good catalogue.
- Every album in that catalogue must have a validated, nonempty, complete track
  list from this crawler generation, or a separately reported HTTP 404 observed
  since the listing sweep began. Previously missing albums are rechecked after
  a new listing. An HTTP 200 with a missing/malformed songlist stays pending.
- Recent events for catalogue albums require a later successful page observation.
  A capped recent scan retains its page cursor and cannot authorize publication.
- The first partial backfill is **not** a serving release. Missing tracks cannot
  be filled from old indexes, filename reconstruction or historical title caches.
- Completeness is relative to the certified listing and observed updates, not an
  instantaneous snapshot of a changing website. Daily full listing refreshes
  bring newly listed albums into scope; unlisted recent slugs are not unioned
  into an uncertified catalogue. Unannounced page edits are not detected instantly.
- Optional publisher/developer/date/format fields can legitimately be empty.
  They are not fabricated or filled from older data. Facet enrichment is not a
  required input, and field richness is not claimed to exceed historical data.

## One engine, three entry points

All serving workflows call `live-data.yaml` / `scripts/live_pipeline.py` and
share one `khinsider-metadata` concurrency lock with cancellation disabled.

| Entry point | Purpose | Default schedule/budget |
| --- | --- | --- |
| `album-meta.yaml` | Refresh the complete listing, discover updates, collect pending album pages | Daily 03:20 UTC; 180 minutes |
| `album-meta-residual.yaml` | Bootstrap or resume missing/changed modern records | Every 4 hours at :40 UTC; 240 minutes |
| `song-index.yaml` | Rebuild both serving outputs from an already complete modern checkpoint | Monday 05:17 UTC; no discovery |

Manual sweep/backfill inputs are `minutes` (1–240) and `publish`. Old
`full_reconcile`, listing/facet caps and legacy song-source inputs were removed.
The weekly rebuild also has a `publish` input. Schedule times are not guaranteed
start times: GitHub can queue runs behind another writer.

Metadata work uses at most 3 workers, 0.9 seconds delay plus up to 0.6 seconds
jitter per worker, retries with backoff, and slices of at most 25 minutes.
Only pending/newly changed albums are queued. No-progress slices stop early.
Full listing stages and recent-page cursors resume across bounded runs.

## Checkpoints are not published libraries

The new pipeline only restores `checkpoint.tar.gz` from the prerelease tag
`live-crawl-v2`. Its descriptor identifies `khinsider-live-v2` and stores per-file
sizes and SHA-256 hashes. Restore rejects foreign generations, corrupt hashes,
unsafe tar members and nonempty local destinations. Only a genuinely missing
release is a bootstrap; authentication/transport failures and a missing asset on
an existing release stop the run.

The checkpoint includes the catalogue, staged listing rows/ledger, complete
modern metadata records, failures, discovery state and publication state.
Partial progress is saved between slices and at the end. Final save is guarded
by successful restore. Actions also keeps a short-lived recovery artifact.
The archive replaces locally only after validation; failed uploads do not imply
that the new state reached GitHub.

`crawl-data`, `song-urls-*`, `songs_cached.jsonl.gz`, `crawl_state_snapshot.tar.gz`,
`index.json` and previously published libraries are **never generation inputs**.
Old files/releases remain historical or last-good serving data during initial
collection, not hole-filling sources. Reusing this new pipeline's own checkpoint
is permitted and avoids starting 100,000+ album requests over every run.

## Publication and relay compatibility

Both builders require complete inputs before touching their output files. There
is no `--allow-partial` or legacy-cache merge path. The song builder preserves
separate actual tracks even when their displayed titles/numbers are identical.
Gzip output has a deterministic timestamp and filename header.

Publication validates provenance, catalogue identity, counts, metadata/TSV
hashes, the stored manifest and compressed library. It uploads all four files to
a draft `library-live-v2-*` release. It then updates the compatible `song-index`
payload before its manifest and finally publishes the library release as latest.
Existing default relay URLs therefore remain valid.

GitHub does not provide a transaction spanning these releases/assets. Readers
must retain last-good data when a payload/manifest pair is inconsistent. No
release deletion is performed. Equal content skips publication using this
pipeline's own publication checkpoint, not the old serving data as crawl input.

`progress.json` / the job summary distinguish `fetched`, `unavailable`, `pending`,
`tracks`, `ready_for_publish` and `published`. A green bounded collection job can
still have `published: false`. Initial full acquisition may take multiple runs.
Code deployment, complete acquisition, release publication and running-relay
uptake are separate milestones.

## Local use (from repository root)

```sh
python -m pip install -r scripts/requirements-meta.txt
# Fresh temporary workspace; no legacy files need to be copied here.
python scripts/live_pipeline.py run --state-dir work/live-v2 --mode refresh --minutes 30
python scripts/live_pipeline.py status --state-dir work/live-v2
# Run again to continue; publication is OFF unless --publish is supplied.
python scripts/live_pipeline.py run --state-dir work/live-v2 --mode backfill --minutes 30
```

On a new runner, restore the new generation before collecting. The scheduled
workflow performs these steps automatically:

```sh
python scripts/live_pipeline.py restore --repo nmt3325/khinsider-index
python scripts/live_pipeline.py run --repo nmt3325/khinsider-index \
  --mode backfill --minutes 240 --checkpoint --publish
python scripts/live_pipeline.py save --repo nmt3325/khinsider-index
```

Do not restore into nonempty local state or manually import old metadata into
`work/live-v2`. Keep a damaged checkpoint aside for investigation rather than
silently replacing it with a fresh partial run.

## Offline tests

```sh
python -m pip install -r scripts/requirements-meta.txt pytest ruff pyyaml
PYTHONPATH=scripts:wayback python -m pytest -q scripts wayback test_shared_player.py
ruff check --select E9,F63,F7,F82 scripts wayback test_shared_player.py
```

Tests cover cold bootstrap without legacy inputs, resume, full-list/track gates,
Unicode paths, metadata acknowledgment, deterministic artifacts, corrupt
checkpoints, publication failures and relay compatibility. Test catalogues with a
few albums are synthetic fixtures, never evidence of a complete live-site crawl.
