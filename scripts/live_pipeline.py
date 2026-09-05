#!/usr/bin/env python3
"""Standalone live-data lifecycle: restore, collect, validate, publish, save.

Checkpoint archives belong exclusively to this pipeline generation. Restoring
old crawl releases, cached titles, archive queues or a published library as
crawl input is deliberately unsupported.
"""
import argparse
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import build_library
import build_song_index
import live_data
import publication

STATE_TAG = 'live-crawl-v2'
STATE_ASSET = 'checkpoint.tar.gz'
MARKER = '[khinsider-live-v2]'
ALLOWED = {
    'catalogue.json', 'album-list.ndjson', 'album-list.pages', 'album-list-failures.log',
    'album-list-staging.ndjson', 'album-list-staging.pages',
    'album-list-staging-failures.log', 'album-list-staging.context.json',
    'album-list.ndjson.rejected', 'album-meta.ndjson', 'album-meta-failures.log',
    'recent-state.json', 'recent-albums.ndjson', 'recent-slugs.txt',
    'pending-slugs.txt', 'discovery.json', 'progress.json', 'last-published.json',
}
MAX_CHECKPOINT_BYTES = 8 * 1024 ** 3


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def validate_state(directory):
    directory = Path(directory)
    if (directory / 'catalogue.json').exists():
        live_data.read_catalogue(directory / 'catalogue.json')
    live_data.latest_records(directory / 'album-meta.ndjson')
    recent = read_json(directory / 'recent-state.json')
    if recent is not None:
        if (not isinstance(recent, dict) or recent.get('version') != 1
                or not isinstance(recent.get('pending'), dict) or not isinstance(recent.get('seen'), dict)):
            raise live_data.DataError('invalid recent-discovery checkpoint')
        for item in recent['pending'].values():
            if not isinstance(item, dict):
                raise live_data.DataError('invalid recent-discovery event')
            live_data.canonical_slug(item.get('slug'))
            live_data.stamp(item.get('discovered_at'))
    for name in ('discovery.json', 'progress.json', 'last-published.json'):
        record = read_json(directory / name)
        if record is not None and (not isinstance(record, dict) or record.get('data_source') != live_data.SOURCE):
            raise live_data.DataError(f'{name}: checkpoint belongs to a different pipeline')


def pack_checkpoint(directory, output):
    directory, output = Path(directory), Path(output)
    validate_state(directory)
    files = {}
    for name in sorted(ALLOWED):
        path = directory / name
        if path.is_symlink():
            raise live_data.DataError('checkpoint cannot contain symlinks')
        if path.is_file():
            files[name] = {'sha256': live_data.digest_file(path), 'size': path.stat().st_size}
    if not files:
        raise live_data.DataError('refusing to save an empty checkpoint')
    if sum(item['size'] for item in files.values()) > MAX_CHECKPOINT_BYTES:
        raise live_data.DataError('checkpoint exceeds restore size limit')
    descriptor = live_data.stable_bytes({
        'data_source': live_data.SOURCE, 'schema_version': live_data.SCHEMA,
        'created_at': live_data.now(), 'files': files,
    })
    temporary = output.with_name(output.name + '.tmp')
    with tarfile.open(temporary, 'w:gz', compresslevel=3) as archive:
        info = tarfile.TarInfo('checkpoint.json')
        info.size = len(descriptor)
        archive.addfile(info, io.BytesIO(descriptor))
        for name in files:
            archive.add(directory / name, arcname=name, recursive=False)
    # Check all input hashes again: an inconsistent archive must never replace
    # the last recoverable checkpoint.
    for name, expected in files.items():
        if live_data.digest_file(directory / name) != expected['sha256']:
            temporary.unlink(missing_ok=True)
            raise live_data.DataError('checkpoint inputs changed while packing')
    os.replace(temporary, output)


def unpack_checkpoint(archive_path, destination):
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise live_data.DataError('restore refuses to overwrite nonempty local state')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='live-restore-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'state'
        stage.mkdir()
        with tarfile.open(archive_path, 'r:gz') as archive:
            members = []
            size = 0
            for member in archive:
                size += member.size
                if (member.name not in ALLOWED | {'checkpoint.json'} or not member.isfile()
                        or member.size < 0 or size > MAX_CHECKPOINT_BYTES
                        or len(members) >= len(ALLOWED) + 1):
                    raise live_data.DataError('invalid or unsafe checkpoint archive')
                members.append(member)
            names = [member.name for member in members]
            if (len(names) != len(set(names)) or 'checkpoint.json' not in names
                    or any(not m.isfile() or m.name not in ALLOWED | {'checkpoint.json'}
                           or m.size < 0 for m in members)
                    or sum(m.size for m in members) > MAX_CHECKPOINT_BYTES):
                raise live_data.DataError('invalid or unsafe checkpoint archive')
            descriptor_member = archive.getmember('checkpoint.json')
            if descriptor_member.size > 1024 * 1024:
                raise live_data.DataError('oversized checkpoint descriptor')
            descriptor = json.load(archive.extractfile(descriptor_member))
            if (descriptor.get('data_source') != live_data.SOURCE
                    or descriptor.get('schema_version') != live_data.SCHEMA):
                raise live_data.DataError('legacy checkpoint is not accepted')
            declared = descriptor.get('files', {})
            if not isinstance(declared, dict) or not declared or set(declared) != set(names) - {'checkpoint.json'}:
                raise live_data.DataError('checkpoint manifest/file set mismatch')
            for member in members:
                if member.name == 'checkpoint.json':
                    continue
                expected = declared[member.name]
                if member.size != expected.get('size'):
                    raise live_data.DataError('checkpoint size mismatch')
                path = stage / member.name
                with archive.extractfile(member) as source, path.open('wb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if live_data.digest_file(path) != expected.get('sha256'):
                    raise live_data.DataError('checkpoint hash mismatch')
        validate_state(stage)
        if destination.exists():
            destination.rmdir()
        os.replace(stage, destination)


def gh(repo, *arguments, check=True):
    command = ['gh', 'release', *map(str, arguments), '--repo', repo]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise live_data.DataError(result.stderr.strip() or 'GitHub release operation failed')
    return result


def release_info(repo, tag):
    result = gh(repo, 'view', tag, '--json', 'tagName,isDraft,isPrerelease,body,assets', check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    # Auth/rate-limit/transport failures are NOT a fresh bootstrap.
    if re.search(r'HTTP 404|release not found|404 \(Not Found\)', result.stderr, re.I):
        return None
    raise live_data.DataError(result.stderr.strip() or 'release lookup failed')


def restore(directory, repo):
    info = release_info(repo, STATE_TAG)
    if info is None:
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()):
            raise live_data.DataError('bootstrap refuses to reuse unverified local files')
        directory.mkdir(parents=True, exist_ok=True)
        print('No live-v2 checkpoint yet; bootstrap starts from the live website.', flush=True)
        return
    if MARKER not in (info.get('body') or ''):
        raise live_data.DataError('state release has an unexpected owner/provenance marker')
    if STATE_ASSET not in {asset['name'] for asset in info.get('assets', [])}:
        raise live_data.DataError('existing state release is missing its checkpoint; not a bootstrap')
    with tempfile.TemporaryDirectory() as temporary:
        gh(repo, 'download', STATE_TAG, '--pattern', STATE_ASSET, '--dir', temporary)
        unpack_checkpoint(Path(temporary) / STATE_ASSET, directory)
    print('Restored verified live-v2 checkpoint only.', flush=True)


def save(directory, repo):
    directory = Path(directory)
    if not directory.exists() or not any((directory / name).is_file() for name in ALLOWED):
        print('No new pipeline state to checkpoint.', flush=True)
        return
    # Place a recovery archive outside the state directory; Actions also saves
    # it as an artifact if a release upload fails.
    archive = directory.parent / STATE_ASSET
    pack_checkpoint(directory, archive)
    info = release_info(repo, STATE_TAG)
    if info is None:
        gh(repo, 'create', STATE_TAG, '--prerelease', '--title', 'Standalone live crawl checkpoints',
           '--notes', MARKER + ' Modern crawler state only. Not a serving dataset.')
    elif MARKER not in (info.get('body') or ''):
        raise live_data.DataError('refusing to overwrite an unrelated state release')
    gh(repo, 'upload', STATE_TAG, archive, '--clobber')
    print('Saved verified live-v2 checkpoint.', flush=True)


def script(name, arguments):
    command = [sys.executable, str(Path(__file__).with_name(name)), *map(str, arguments)]
    return subprocess.run(command, check=False).returncode


def progress(directory):
    directory = Path(directory)
    if not (directory / 'catalogue.json').exists():
        summary = {'data_source': live_data.SOURCE, 'phase': 'listing', 'complete': False,
                   'published': False, 'total': None, 'fetched': 0, 'pending': None, 'tracks': 0}
        pending = []
    else:
        _, _, _, pending, summary = live_data.inspect(
            directory / 'catalogue.json', directory / 'album-meta.ndjson', directory / 'recent-state.json')
        summary = dict(summary, phase='metadata', published=False)
    discovery = read_json(directory / 'discovery.json', {})
    summary['discovery_complete'] = (discovery.get('listing_complete') is True
                                     and discovery.get('recent_complete') is True)
    summary['ready_for_publish'] = bool(summary['complete'] and summary['discovery_complete'])
    live_data.atomic_json(directory / 'progress.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return pending, summary


def seed_recent(directory):
    path = directory / 'recent-state.json'
    if not path.exists():
        catalogue = live_data.read_catalogue(directory / 'catalogue.json')
        # The complete listing supplies history. Do not try to page through
        # years of news to bootstrap a recent-update watermark.
        live_data.atomic_json(path, {'version': 1, 'watermark': catalogue['started_at'][:10],
                                     'seen': {}, 'pending': {}})


def recent_arguments(directory):
    return ['--state', directory / 'recent-state.json', '--out', directory / 'recent-albums.ndjson',
            '--queue', directory / 'recent-slugs.txt', '--metadata', directory / 'album-meta.ndjson']


def build_outputs(directory, output):
    output.mkdir(parents=True, exist_ok=True)
    common = ['--catalogue', str(directory / 'catalogue.json'),
              '--recent-state', str(directory / 'recent-state.json')]
    build_library.main(common + ['--meta', str(directory / 'album-meta.ndjson'),
                                '--out', str(output / 'library.json'), '--gzip'])
    manifest = build_song_index.main(common + ['--metadata', str(directory / 'album-meta.ndjson'),
                                              '--out', str(output / 'songs.tsv.gz'),
                                              '--manifest', str(output / 'songs-index.json')])
    manifest['library_content_sha256'] = publication.library_content_sha256(str(output / 'library.json'))
    live_data.atomic_json(output / 'songs-index.json', manifest)
    return manifest


def publication_signature(manifest):
    return {key: manifest[key] for key in ('data_source', 'catalogue_id',
                                          'library_content_sha256', 'content_sha256', 'sha256')}


def publish(directory, output, repo, manifest):
    _, current = progress(directory)
    if not current['ready_for_publish'] or manifest.get('complete') is not True:
        raise live_data.IncompleteData('publication requires a complete live dataset')
    if manifest.get('data_source') != live_data.SOURCE:
        raise live_data.DataError('refusing to publish a foreign/legacy dataset')
    if (manifest.get('catalogue_id') != current.get('catalogue_id')
            or manifest.get('songs') != current.get('tracks')
            or manifest.get('metadata_sha256') != live_data.digest_file(directory / 'album-meta.ndjson')
            or manifest.get('sha256') != live_data.digest_file(output / 'songs.tsv.gz')
            or manifest.get('library_content_sha256') != publication.library_content_sha256(str(output / 'library.json'))):
        raise live_data.DataError('serving outputs do not match the validated current dataset')
    try:
        library = read_json(output / 'library.json')
        stored_manifest = read_json(output / 'songs-index.json')
        with gzip.open(output / 'library.json.gz', 'rb') as stream:
            gzip_content_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    except (OSError, ValueError, EOFError) as exc:
        raise live_data.DataError('invalid serving artifact') from exc
    if (stored_manifest != manifest or manifest.get('dataset_schema_version') != 2
            or manifest.get('legacy_inputs') != []
            or library.get('data_source') != live_data.SOURCE or library.get('complete') is not True
            or library.get('dataset_schema_version') != 2 or library.get('legacy_inputs') != []
            or library.get('album_count') != current['fetched']
            or library.get('catalogue_id') != current['catalogue_id']
            or gzip_content_hash != live_data.digest_file(output / 'library.json')):
        raise live_data.DataError('serving artifact provenance or compressed contents do not match')
    signature = publication_signature(manifest)
    last = read_json(directory / 'last-published.json', {})
    if last.get('signature') == signature and last.get('tag'):
        remote = release_info(repo, last['tag'])
        if remote and not remote.get('isDraft'):
            print('Complete dataset is unchanged; no release update.', flush=True)
            return False
    tag = time.strftime('library-live-v2-%Y%m%d-%H%M%S-', time.gmtime()) + os.environ.get('GITHUB_RUN_ID', 'manual')
    notes = (f"{MARKER}\n\n{manifest['albums']} complete albums / {manifest['songs']} tracks. "
             f"{len(manifest['unavailable_albums'])} explicitly unavailable HTTP-404 albums. "
             'No legacy index, title cache or archival snapshot was used as input.')
    gh(repo, 'create', tag, '--draft', '--title', tag, '--notes', notes)
    gh(repo, 'upload', tag, output / 'library.json', output / 'library.json.gz',
       output / 'songs.tsv.gz', output / 'songs-index.json')
    if release_info(repo, 'song-index') is None:
        gh(repo, 'create', 'song-index', '--prerelease', '--title', 'Song title index', '--notes', notes)
    # Payload first, manifest last: readers reject a mismatched intermediate
    # pair and keep their last-good cache. GitHub has no multi-release transaction.
    gh(repo, 'upload', 'song-index', output / 'songs.tsv.gz', '--clobber')
    gh(repo, 'upload', 'song-index', output / 'songs-index.json', '--clobber')
    gh(repo, 'edit', 'song-index', '--prerelease', '--notes', notes)
    gh(repo, 'edit', tag, '--draft=false', '--latest')
    live_data.atomic_json(directory / 'last-published.json', {
        'data_source': live_data.SOURCE, 'tag': tag, 'signature': signature,
        'published_at': live_data.now(),
    })
    print('Published complete live-only dataset: ' + tag, flush=True)
    return True


def run(directory, repo, mode='backfill', minutes=180, do_publish=False, checkpoint=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    validate_state(directory)
    deadline = time.monotonic() + minutes * 60
    discovery = read_json(directory / 'discovery.json', {'data_source': live_data.SOURCE})
    if mode != 'build':
        needs_listing = (mode == 'refresh' or not (directory / 'catalogue.json').exists()
                         or discovery.get('listing_complete') is not True)
        if needs_listing:
            discovery.update(listing_complete=False, recent_complete=False)
            live_data.atomic_json(directory / 'discovery.json', discovery)
            result = script('crawl_index_pages.py', [
                '--fresh', '--out', directory / 'album-list.ndjson',
                '--state', directory / 'album-list.pages',
                '--failures', directory / 'album-list-failures.log',
                '--catalogue', directory / 'catalogue.json',
                '--deadline-minutes', min(25, max(0.1, minutes)),
            ])
            discovery['listing_complete'] = result == 0
            live_data.atomic_json(directory / 'discovery.json', discovery)
            if result:
                progress(directory)
                return False
        seed_recent(directory)
        result = script('crawl_recent.py', recent_arguments(directory) + [
            '--overlap-days', 3, '--max-pages', 10, '--deadline-minutes', 5])
        discovery['recent_complete'] = result == 0
        live_data.atomic_json(directory / 'discovery.json', discovery)
        if checkpoint:
            save(directory, repo)
        while time.monotonic() < deadline:
            pending, before = progress(directory)
            if not pending:
                break
            # Only unresolved/newly changed albums are requested, not all rows.
            pending.sort(key=lambda slug: hashlib.sha256(slug.encode()).digest())
            queue = directory / 'pending-slugs.txt'
            queue.write_text(''.join(slug + '\n' for slug in pending), encoding='utf-8')
            remaining = min(25, (deadline - time.monotonic()) / 60)
            if remaining <= 0:
                break
            result = script('crawl_album_meta.py', [
                '--slugs-file', queue, '--out', directory / 'album-meta.ndjson',
                '--failures', directory / 'album-meta-failures.log', '--refresh', '--retry-failures',
                '--order', 'file',
                '--workers', 3, '--delay', 0.9, '--jitter', 0.6, '--retries', 4,
                '--deadline-minutes', remaining, '--progress-every', 200,
            ])
            if result:
                raise live_data.DataError('metadata crawler failed; refusing to publish')
            if script('crawl_recent.py', recent_arguments(directory) + ['--ack-only']):
                raise live_data.DataError('recent update acknowledgement failed; refusing to publish')
            _, after = progress(directory)
            if checkpoint:
                save(directory, repo)
            if after['pending'] >= before['pending']:
                print('No forward progress this slice; leave failures for the next run.', flush=True)
                break
    _, summary = progress(directory)
    if not summary['ready_for_publish']:
        print('Incomplete collection retained as a checkpoint only; serving data is unchanged.', flush=True)
        return False
    output = directory.parent / 'live-output'
    manifest = build_outputs(directory, output)
    published = publish(directory, output, repo, manifest) if do_publish else False
    summary.update(phase='ready', published=published)
    live_data.atomic_json(directory / 'progress.json', summary)
    return published


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('restore', 'run', 'save', 'status'))
    parser.add_argument('--state-dir', default='work/live-v2')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', 'nmt3325/khinsider-index'))
    parser.add_argument('--mode', choices=('refresh', 'backfill', 'build'), default='backfill')
    parser.add_argument('--minutes', type=float, default=180)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--checkpoint', action='store_true')
    args = parser.parse_args(argv)
    if not 0 < args.minutes <= 240:
        parser.error('--minutes must be greater than 0 and at most 240')
    directory = Path(args.state_dir)
    if args.command == 'restore':
        restore(directory, args.repo)
    elif args.command == 'save':
        save(directory, args.repo)
    elif args.command == 'run':
        run(directory, args.repo, args.mode, args.minutes, args.publish, args.checkpoint)
    else:
        summary = read_json(directory / 'progress.json')
        if summary is None:
            _, summary = progress(directory)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                stream.write('## Standalone live-data status\n\n')
                for key, value in summary.items():
                    stream.write(f'- {key}: **{value}**\n')
                stream.write('\nA successful bounded crawl is not necessarily a published complete dataset.\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except live_data.DataError as exc:
        raise SystemExit(str(exc))
